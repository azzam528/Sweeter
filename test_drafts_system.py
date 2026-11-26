import io
import os
import sys
import json
import time
import shutil
import tempfile
import argparse
from datetime import datetime, timedelta, timezone

import jwt
from bson import ObjectId

import app as app_module
import database as db_module
import notifications as notif_module
from test_rich_media_posts import (
    GREEN,
    RED,
    RESET,
    _MockCollection,
    _MockDB as _BaseMockDB,
    VALID_JPEG_BYTES,
    VALID_PNG_BYTES,
    VALID_WEBP_BYTES,
)


class _DraftsMockDB(_BaseMockDB):
    def __init__(self):
        super().__init__()
        self.drafts = _MockCollection()


def run_tests(use_mock_db=False):
    active_db = db_module.db
    mode_label = "LIVE MONGODB"
    if use_mock_db:
        active_db = _DraftsMockDB()
        app_module.db = active_db
        db_module.db = active_db
        notif_module.db = active_db
        mode_label = "IN-MEMORY MOCK DB"

    orig_upload_dir = app_module.app.config["POST_UPLOAD_DIR"]
    temp_upload_dir = tempfile.mkdtemp(prefix="sweeter_drafts_test_")
    app_module.app.config["POST_UPLOAD_DIR"] = temp_upload_dir

    # Reset rate limiter cleanly
    app_module._draft_rate_buckets.clear()
    orig_max_rl = app_module.DRAFT_RATE_LIMIT_MAX

    client = app_module.app.test_client()
    secret = app_module.app.config["SECRET_KEY"]
    test_results = []

    def record(num, name, passed, details=""):
        status = "PASS" if passed else "FAIL"
        color = GREEN if passed else RED
        print(f"[{color}{status}{RESET}] {num}. {name}: {details}")
        test_results.append((num, name, passed, details))

    u_author = "test_draft_author"
    u_other = "test_draft_other"
    all_users = [u_author, u_other]

    def make_token(username):
        return jwt.encode(
            {"id": username, "exp": datetime.now(timezone.utc) + timedelta(hours=2)},
            secret,
            algorithm="HS256",
        )

    def auth_cookie(token):
        client.set_cookie("mytoken", token)

    def clear_cookie():
        client.delete_cookie("mytoken")

    try:
        active_db.users.delete_many({"username": {"$in": all_users}})
        active_db.posts.delete_many({"username": {"$in": all_users}})
        active_db.drafts.delete_many({"user_id": {"$in": all_users}})
        active_db.notifications.delete_many({"$or": [{"recipient": {"$in": all_users}}, {"actor": {"$in": all_users}}]})

        active_db.users.insert_many([
            {
                "username": u_author,
                "password": "pw",
                "profile_name": "Draft Author",
                "profile_pic_real": "profile_pics/profile_placeholder.png",
                "profile_info": "Writer",
            },
            {
                "username": u_other,
                "password": "pw",
                "profile_name": "Other User",
                "profile_pic_real": "profile_pics/profile_placeholder.png",
                "profile_info": "Reader",
            },
        ])

        t_author = make_token(u_author)
        t_other = make_token(u_other)

        print("=" * 68)
        print(f"RUNNING DRAFT POSTS & AUTOSAVE TEST SUITE ({mode_label})")
        print("=" * 68)

        # ------------------------------------------------------------------
        # 1. Authentication & Authorization Checks
        # ------------------------------------------------------------------
        clear_cookie()
        r_unauth_page = client.get("/drafts")
        r_unauth_api = client.get("/api/drafts")
        r_unauth_post = client.post("/api/drafts", json={"content": "No auth"})
        record(
            "1.1",
            "Unauthenticated access blocked on /drafts and /api/drafts",
            r_unauth_page.status_code == 302
            and r_unauth_api.status_code == 401
            and r_unauth_post.status_code == 401,
            f"page={r_unauth_page.status_code}, list={r_unauth_api.status_code}, create={r_unauth_post.status_code}",
        )

        # ------------------------------------------------------------------
        # 2. Draft Creation & Validation
        # ------------------------------------------------------------------
        auth_cookie(t_author)
        r_empty = client.post("/api/drafts", json={"content": "   "})
        r_too_long = client.post("/api/drafts", json={"content": "x" * 5001})
        record(
            "2.1",
            "Reject empty draft and >5000 char draft",
            r_empty.status_code == 400 and r_too_long.status_code == 400,
            f"empty={r_empty.status_code}, long={r_too_long.status_code}",
        )

        r_create = client.post(
            "/api/drafts",
            json={
                "content": "Ini draft pertama saya tentang #sweeter",
                "client_seq": 1,
                "client_draft_key": "dk_author_unique_1",
            },
        )
        d_create = r_create.get_json() or {}
        draft_1 = d_create.get("draft") or {}
        draft_1_id = draft_1.get("_id")
        record(
            "2.2",
            "Create valid text draft returns 201 and metadata",
            r_create.status_code == 201
            and d_create.get("result") == "success"
            and bool(draft_1_id)
            and draft_1.get("version") == 1
            and draft_1.get("user_id") == u_author,
            f"status={r_create.status_code}, id={draft_1_id}, version={draft_1.get('version')}",
        )

        # Repeated client_draft_key should update existing draft instead of creating duplicate
        r_dup_key = client.post(
            "/api/drafts",
            json={
                "content": "Pembaruan key sama tanpa duplikat",
                "client_seq": 2,
                "client_draft_key": "dk_author_unique_1",
            },
        )
        d_dup = r_dup_key.get_json() or {}
        draft_after_dup = d_dup.get("draft") or {}
        count_author = active_db.drafts.count_documents({"user_id": u_author})
        record(
            "2.3",
            "Idempotent client_draft_key prevents duplicate draft creation",
            r_dup_key.status_code == 200
            and draft_after_dup.get("_id") == draft_1_id
            and count_author == 1,
            f"status={r_dup_key.status_code}, id_match={draft_after_dup.get('_id') == draft_1_id}, count={count_author}",
        )

        # Draft creation with image upload
        r_img_draft = client.post(
            "/api/drafts",
            data={
                "content": "Draft dengan gambar",
                "client_seq": 1,
                "images": [(io.BytesIO(VALID_PNG_BYTES), "draft_pic.png")],
            },
            content_type="multipart/form-data",
        )
        d_img = r_img_draft.get_json() or {}
        draft_img = d_img.get("draft") or {}
        draft_img_id = draft_img.get("_id")
        img_meta = (draft_img.get("images") or [{}])[0]
        img_fname = img_meta.get("filename", "")
        img_exists_on_disk = bool(img_fname and os.path.isfile(os.path.join(temp_upload_dir, img_fname)))
        record(
            "2.4",
            "Create draft with valid image saves metadata & file",
            r_img_draft.status_code == 201
            and len(draft_img.get("images") or []) == 1
            and img_exists_on_disk,
            f"status={r_img_draft.status_code}, file_exists={img_exists_on_disk}",
        )

        # ------------------------------------------------------------------
        # 3. Drafts NEVER appear in public feeds, search, trending, or notifications
        # ------------------------------------------------------------------
        r_feed = client.get("/get_posts")
        r_search = client.get("/api/search?q=sweeter")
        r_trending = client.get("/api/trending")
        feed_posts = (r_feed.get_json() or {}).get("posts") or []
        search_posts = (r_search.get_json() or {}).get("posts") or []
        trending_topics = (r_trending.get_json() or {}).get("trending") or []
        notif_count = active_db.notifications.count_documents({})
        record(
            "3.1",
            "Drafts remain invisible in public feed, search, trending, and notifications",
            len(feed_posts) == 0
            and len(search_posts) == 0
            and len(trending_topics) == 0
            and notif_count == 0,
            f"feed={len(feed_posts)}, search={len(search_posts)}, trending={len(trending_topics)}, notif={notif_count}",
        )

        # ------------------------------------------------------------------
        # 4. Autosave, Versioning & Out-of-Order Sequencing
        # ------------------------------------------------------------------
        r_upd_1 = client.put(
            f"/api/drafts/{draft_1_id}",
            json={"content": "Revisi terbaru versi 3", "version": 2, "client_seq": 5},
        )
        d_upd_1 = r_upd_1.get_json() or {}
        upd_1 = d_upd_1.get("draft") or {}

        # Older client_seq should return 409 and NOT overwrite newer content
        r_stale_seq = client.put(
            f"/api/drafts/{draft_1_id}",
            json={"content": "Konten kadaluarsa", "version": 3, "client_seq": 4},
        )
        # Older version should return 409 and NOT overwrite newer content
        r_stale_ver = client.put(
            f"/api/drafts/{draft_1_id}",
            json={"content": "Versi lama menimpa", "version": 1, "client_seq": 9},
        )
        r_verify_latest = client.get(f"/api/drafts/{draft_1_id}")
        latest_doc = (r_verify_latest.get_json() or {}).get("draft") or {}
        record(
            "4.1",
            "Autosave updates version and rejects out-of-order seq/version (409)",
            r_upd_1.status_code == 200
            and upd_1.get("version") == 3
            and r_stale_seq.status_code == 409
            and r_stale_ver.status_code == 409
            and latest_doc.get("content") == "Revisi terbaru versi 3",
            f"upd={upd_1.get('version')}, stale_seq={r_stale_seq.status_code}, stale_ver={r_stale_ver.status_code}",
        )

        # Updating draft with image keeps existing image OR adds new & deletes removed unreferenced file
        r_upd_img = client.put(
            f"/api/drafts/{draft_img_id}",
            data={
                "content": "Draft gambar + gambar ke-2",
                "keep_images": json.dumps([img_fname]),
                "images": [(io.BytesIO(VALID_JPEG_BYTES), "second.jpg")],
            },
            content_type="multipart/form-data",
        )
        d_upd_img = (r_upd_img.get_json() or {}).get("draft") or {}
        imgs_after_add = d_upd_img.get("images") or []
        second_fname = imgs_after_add[1]["filename"] if len(imgs_after_add) == 2 else ""

        # Remove first image from draft
        r_rm_img = client.put(
            f"/api/drafts/{draft_img_id}",
            data={
                "content": "Draft gambar pertama dihapus",
                "keep_images": json.dumps([second_fname]),
            },
            content_type="multipart/form-data",
        )
        removed_deleted = not os.path.exists(os.path.join(temp_upload_dir, img_fname))
        kept_still_there = os.path.isfile(os.path.join(temp_upload_dir, second_fname))
        record(
            "4.2",
            "Draft image update keeps selected images and cleans up removed unreferenced image",
            r_upd_img.status_code == 200
            and len(imgs_after_add) == 2
            and r_rm_img.status_code == 200
            and removed_deleted
            and kept_still_there,
            f"count={len(imgs_after_add)}, removed_deleted={removed_deleted}, kept_exists={kept_still_there}",
        )

        # ------------------------------------------------------------------
        # 5. Ownership Isolation & Management Listing / Pagination
        # ------------------------------------------------------------------
        # Add 2 more drafts for author & 1 for other user to test sorting, pagination, and isolation
        time.sleep(0.02)
        r_d3 = client.post("/api/drafts", json={"content": "Draft author ke-3"})
        time.sleep(0.02)
        r_d4 = client.post("/api/drafts", json={"content": "Draft author ke-4 terbaru"})
        d4_id = ((r_d4.get_json() or {}).get("draft") or {}).get("_id")

        auth_cookie(t_other)
        r_other_create = client.post("/api/drafts", json={"content": "Draft rahasia milik other"})
        other_draft_id = ((r_other_create.get_json() or {}).get("draft") or {}).get("_id")

        # Author cannot GET, PUT, DELETE, or PUBLISH other's draft
        auth_cookie(t_author)
        r_get_forbid = client.get(f"/api/drafts/{other_draft_id}")
        r_put_forbid = client.put(f"/api/drafts/{other_draft_id}", json={"content": "Hack"})
        r_del_forbid = client.delete(f"/api/drafts/{other_draft_id}")
        r_pub_forbid = client.post(f"/api/drafts/{other_draft_id}/publish")
        r_bad_id = client.get("/api/drafts/not_an_object_id")
        record(
            "5.1",
            "Ownership isolation blocks cross-user read/edit/delete/publish (403) and bad ID (400)",
            r_get_forbid.status_code == 403
            and r_put_forbid.status_code == 403
            and r_del_forbid.status_code == 403
            and r_pub_forbid.status_code == 403
            and r_bad_id.status_code == 400,
            f"get={r_get_forbid.status_code}, put={r_put_forbid.status_code}, del={r_del_forbid.status_code}, pub={r_pub_forbid.status_code}",
        )

        # List drafts: only author's drafts (4 total), sorted updated_at desc, cursor pagination
        r_page1 = client.get("/api/drafts?limit=2")
        p1 = r_page1.get_json() or {}
        list1 = p1.get("drafts") or []
        cursor1 = p1.get("next_cursor")
        r_page2 = client.get(f"/api/drafts?limit=2&cursor={cursor1}")
        p2 = r_page2.get_json() or {}
        list2 = p2.get("drafts") or []
        all_listed_ids = [x["_id"] for x in (list1 + list2)]
        r_count = client.get("/api/drafts/count")
        r_page_html = client.get("/drafts")
        record(
            "5.2",
            "List /api/drafts sorted desc with cursor pagination, isolation & /drafts HTML 200",
            r_page1.status_code == 200
            and len(list1) == 2
            and list1[0]["_id"] == d4_id
            and len(list2) == 2
            and other_draft_id not in all_listed_ids
            and (r_count.get_json() or {}).get("draft_count") == 4
            and r_page_html.status_code == 200
            and b"draft privat tersimpan" in r_page_html.data,
            f"p1={len(list1)}, p2={len(list2)}, top_is_newest={list1[0]['_id'] == d4_id}, count={(r_count.get_json() or {}).get('draft_count')}",
        )

        # ------------------------------------------------------------------
        # 6. Publish Draft Workflow & Failure Preservation
        # ------------------------------------------------------------------
        # Failed publish (inline invalid empty content) must preserve draft and release lock
        r_pub_fail = client.post(
            f"/api/drafts/{draft_img_id}/publish",
            data={"content": "   ", "keep_images": json.dumps([])},
            content_type="multipart/form-data",
        )
        still_exists_after_fail = client.get(f"/api/drafts/{draft_img_id}").status_code == 200
        record(
            "6.1",
            "Failed publish preserves draft and unlocks it for retry",
            r_pub_fail.status_code == 400 and still_exists_after_fail,
            f"fail_status={r_pub_fail.status_code}, still_exists={still_exists_after_fail}",
        )

        # Valid publish of draft with image (from /drafts page: empty POST body)
        r_pub_ok = client.post(f"/api/drafts/{draft_img_id}/publish")
        d_pub = r_pub_ok.get_json() or {}
        pub_post = d_pub.get("post") or {}
        pub_post_id = d_pub.get("post_id")
        draft_gone = client.get(f"/api/drafts/{draft_img_id}").status_code == 404
        # Repeated publish of already-published/deleted draft must return 404 (not duplicate post)
        r_pub_repeat = client.post(f"/api/drafts/{draft_img_id}/publish")
        # Also test atomic is_publishing lock explicitly
        active_db.drafts.update_one({"_id": ObjectId(draft_1_id)}, {"$set": {"is_publishing": True}})
        r_pub_locked = client.post(f"/api/drafts/{draft_1_id}/publish")
        active_db.drafts.update_one({"_id": ObjectId(draft_1_id)}, {"$set": {"is_publishing": False}})

        # Published post now appears in /get_posts with preserved image
        r_feed_after = client.get("/get_posts")
        posts_after = (r_feed_after.get_json() or {}).get("posts") or []
        matching_posts = [p for p in posts_after if p.get("_id") == pub_post_id]
        record(
            "6.2",
            "Publish draft creates public post, keeps image, deletes draft, prevents duplicate publish",
            r_pub_ok.status_code == 201
            and bool(pub_post_id)
            and len(pub_post.get("images") or []) == 1
            and os.path.isfile(os.path.join(temp_upload_dir, second_fname))
            and draft_gone
            and r_pub_repeat.status_code == 404
            and r_pub_locked.status_code == 409
            and len(matching_posts) == 1,
            f"pub={r_pub_ok.status_code}, deleted={draft_gone}, repeat={r_pub_repeat.status_code}, locked={r_pub_locked.status_code}, in_feed={len(matching_posts)}",
        )

        # Publish text draft with inline edit + hashtags -> verify hashtags extracted
        r_pub_text = client.post(
            f"/api/drafts/{draft_1_id}/publish",
            json={"content": "Sudah siap tayang #DraftTest #Sweeter"},
        )
        pub_text_post = (r_pub_text.get_json() or {}).get("post") or {}
        record(
            "6.3",
            "Publish draft with inline content extracts hashtags cleanly",
            r_pub_text.status_code == 201
            and set(pub_text_post.get("hashtags") or []) == {"drafttest", "sweeter"},
            f"hashtags={pub_text_post.get('hashtags')}",
        )

        # ------------------------------------------------------------------
        # 7. Safe Deletion & Shared Media Reference Check
        # ------------------------------------------------------------------
        # Delete remaining draft d3 (no images)
        d3_id = ((r_d3.get_json() or {}).get("draft") or {}).get("_id")
        r_del_ok = client.delete(f"/api/drafts/{d3_id}")
        r_del_missing = client.delete(f"/api/drafts/{d3_id}")

        # Verify shared image safety: if a draft references second_fname AND published post references second_fname,
        # deleting the draft must NOT delete second_fname from disk!
        r_shared_draft = client.post("/api/drafts", json={"content": "Draft shared ref"})
        shared_id = ((r_shared_draft.get_json() or {}).get("draft") or {}).get("_id")
        active_db.drafts.update_one(
            {"_id": ObjectId(shared_id)},
            {"$set": {"images": [{"filename": second_fname, "url": f"/uploads/posts/{second_fname}", "mime_type": "image/jpeg", "size": 100}]}},
        )
        r_del_shared = client.delete(f"/api/drafts/{shared_id}")
        shared_still_on_disk = os.path.isfile(os.path.join(temp_upload_dir, second_fname))

        # Deleting a draft with an unreferenced WebP image DOES delete the file from disk
        r_webp_draft = client.post(
            "/api/drafts",
            data={"content": "Draft webp hapus", "images": [(io.BytesIO(VALID_WEBP_BYTES), "temp.webp")]},
            content_type="multipart/form-data",
        )
        webp_doc = (r_webp_draft.get_json() or {}).get("draft") or {}
        webp_fname = (webp_doc.get("images") or [{}])[0].get("filename", "")
        r_del_webp = client.delete(f"/api/drafts/{webp_doc['_id']}")
        webp_deleted = not os.path.exists(os.path.join(temp_upload_dir, webp_fname))

        record(
            "7.1",
            "Delete draft deletes unreferenced media but preserves media still used by published post",
            r_del_ok.status_code == 200
            and r_del_missing.status_code == 404
            and r_del_shared.status_code == 200
            and shared_still_on_disk
            and r_del_webp.status_code == 200
            and webp_deleted,
            f"shared_preserved={shared_still_on_disk}, webp_deleted={webp_deleted}",
        )

        # ------------------------------------------------------------------
        # 8. Rate Limit Protection
        # ------------------------------------------------------------------
        app_module._draft_rate_buckets.clear()
        app_module.DRAFT_RATE_LIMIT_MAX = 3
        rl_statuses = []
        for idx in range(4):
            rl_resp = client.put(
                f"/api/drafts/{d4_id}",
                json={"content": f"Rate limit check {idx}"},
            )
            rl_statuses.append(rl_resp.status_code)
        app_module.DRAFT_RATE_LIMIT_MAX = orig_max_rl
        app_module._draft_rate_buckets.clear()
        record(
            "8.1",
            "Draft autosave rate limit returns 429 when burst limit exceeded",
            rl_statuses == [200, 200, 200, 429],
            f"statuses={rl_statuses}",
        )

        # ------------------------------------------------------------------
        # 9. Frontend Template & JS Contract Verification
        # ------------------------------------------------------------------
        base_dir = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base_dir, "static", "myjs.js"), encoding="utf-8") as f:
            js_src = f.read()
        with open(os.path.join(base_dir, "templates", "post.html"), encoding="utf-8") as f:
            post_html = f.read()
        with open(os.path.join(base_dir, "templates", "drafts.html"), encoding="utf-8") as f:
            drafts_html = f.read()

        js_ok = all(
            token in js_src
            for token in [
                "AUTOSAVE_DEBOUNCE_MS = 2000",
                "schedule_draft_autosave",
                "save_draft_manual",
                "latestAppliedSeq",
                "client_draft_key",
                "beforeunload",
                "load_drafts",
                "publish_draft_item",
                "delete_draft_item",
                "localStorage",
            ]
        )
        templates_ok = (
            'id="post-save-draft-btn"' in post_html
            and 'id="draft-autosave-status"' in post_html
            and 'href="/drafts"' in post_html
            and 'id="drafts-box"' in drafts_html
            and "load_drafts(" in drafts_html
        )
        record(
            "9.1",
            "Frontend autosave JS & template contracts present",
            js_ok and templates_ok,
            f"js_ok={js_ok}, templates_ok={templates_ok}",
        )

    finally:
        app_module.DRAFT_RATE_LIMIT_MAX = orig_max_rl
        app_module._draft_rate_buckets.clear()
        app_module.app.config["POST_UPLOAD_DIR"] = orig_upload_dir
        shutil.rmtree(temp_upload_dir, ignore_errors=True)

    passed_count = sum(1 for _, _, p, _ in test_results if p)
    total_count = len(test_results)
    print("-" * 68)
    print(f"RESULT ({mode_label}): {passed_count}/{total_count} passed")
    print("=" * 68)
    return passed_count == total_count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Sweeter Draft Posts & Autosave tests")
    parser.add_argument("--mock-db", action="store_true", help="Use in-memory mock DB instead of MongoDB")
    args = parser.parse_args()
    success = run_tests(use_mock_db=args.mock_db)
    sys.exit(0 if success else 1)
