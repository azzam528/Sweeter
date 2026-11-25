import io
import os
import re
import sys
import json
import shutil
import tempfile
import argparse
from datetime import datetime, timedelta, timezone

import jwt
from bson import ObjectId

import app as app_module
import database as db_module
import notifications as notif_module


GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


# Minimal valid binary payloads for JPG, PNG, and WebP
VALID_JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
VALID_WEBP_BYTES = b"RIFF\x1a\x00\x00\x00WEBPVP8 \x0e\x00\x00\x00\x10\x00\x00\x9d\x01*\x01\x00\x01\x00\x00"


# ---------------------------------------------------------------------------
# Lightweight In-Memory PyMongo Mock Adapter (used ONLY when --mock-db is set)
# ---------------------------------------------------------------------------

def _match_filter(doc, filt):
    if not filt:
        return True
    for key, val in filt.items():
        if key == "$or":
            if not any(_match_filter(doc, sub) for sub in val):
                return False
            continue
        if key == "$and":
            if not all(_match_filter(doc, sub) for sub in val):
                return False
            continue

        # Dotted path support (e.g. images.filename)
        parts = key.split(".")
        cur = doc
        found = True
        for i, p in enumerate(parts):
            if isinstance(cur, list):
                # Array of dicts match
                rest_key = ".".join(parts[i:])
                return any(_match_filter(item, {rest_key: val}) for item in cur if isinstance(item, dict))
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                found = False
                cur = None
                break

        if isinstance(val, dict):
            for op, operand in val.items():
                if op == "$in":
                    if isinstance(cur, list):
                        if not any(x in operand for x in cur):
                            return False
                    elif cur not in operand:
                        return False
                elif op == "$ne":
                    if cur == operand:
                        return False
                elif op == "$lt":
                    if not (cur is not None and str(cur) < str(operand)):
                        return False
                elif op == "$gte":
                    if not (cur is not None and str(cur) >= str(operand)):
                        return False
                elif op == "$exists":
                    if bool(found) != bool(operand):
                        return False
                elif op == "$regex":
                    flags = re.IGNORECASE if "i" in val.get("$options", "") else 0
                    if isinstance(cur, list):
                        if not any(re.search(operand, str(x), flags) for x in cur):
                            return False
                    elif not re.search(operand, str(cur or ""), flags):
                        return False
                elif op == "$options":
                    pass
        else:
            if isinstance(cur, list):
                if val not in cur:
                    return False
            elif cur != val:
                return False
    return True


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _UpdateResult:
    def __init__(self, modified_count):
        self.modified_count = modified_count


class _DeleteResult:
    def __init__(self, deleted_count):
        self.deleted_count = deleted_count


class _MockCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key_or_list, direction=1):
        if isinstance(key_or_list, str):
            specs = [(key_or_list, direction)]
        else:
            specs = list(key_or_list)
        docs = list(self._docs)
        for field, d in reversed(specs):
            docs.sort(key=lambda x: str(x.get(field, "")), reverse=(d == -1))
        return _MockCursor(docs)

    def limit(self, n):
        return _MockCursor(self._docs[:n])

    def __iter__(self):
        return iter(self._docs)


class _MockCollection:
    def __init__(self):
        self.docs = []

    def create_index(self, *args, **kwargs):
        return "mock_index"

    def find_one(self, filt=None, projection=None):
        for d in self.docs:
            if _match_filter(d, filt or {}):
                return self._project(dict(d), projection)
        return None

    def find(self, filt=None, projection=None):
        matched = [self._project(dict(d), projection) for d in self.docs if _match_filter(d, filt or {})]
        return _MockCursor(matched)

    def _project(self, doc, projection):
        if not projection:
            return doc
        if any(v == 0 or v is False for k, v in projection.items()):
            for k, v in projection.items():
                if v == 0 or v is False:
                    doc.pop(k, None)
            return doc
        out = {}
        if projection.get("_id", 1) != 0 and "_id" in doc:
            out["_id"] = doc["_id"]
        for k, v in projection.items():
            if v and k in doc:
                out[k] = doc[k]
        return out

    def insert_one(self, doc):
        d = dict(doc)
        if "_id" not in d:
            d["_id"] = ObjectId()
        self.docs.append(d)
        return _InsertResult(d["_id"])

    def insert_many(self, docs):
        for d in docs:
            self.insert_one(d)

    def update_one(self, filt, update):
        for d in self.docs:
            if _match_filter(d, filt):
                if "$set" in update:
                    d.update(update["$set"])
                return _UpdateResult(1)
        return _UpdateResult(0)

    def update_many(self, filt, update):
        cnt = 0
        for d in self.docs:
            if _match_filter(d, filt):
                if "$set" in update:
                    d.update(update["$set"])
                cnt += 1
        return _UpdateResult(cnt)

    def delete_one(self, filt):
        for i, d in enumerate(self.docs):
            if _match_filter(d, filt):
                self.docs.pop(i)
                return _DeleteResult(1)
        return _DeleteResult(0)

    def delete_many(self, filt):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match_filter(d, filt)]
        return _DeleteResult(before - len(self.docs))

    def count_documents(self, filt):
        return sum(1 for d in self.docs if _match_filter(d, filt))

    def distinct(self, field, filt=None):
        seen = []
        for d in self.docs:
            if _match_filter(d, filt or {}):
                val = d.get(field)
                if val is not None and val not in seen:
                    seen.append(val)
        return seen

    def aggregate(self, pipeline):
        docs = list(self.docs)
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _match_filter(d, stage["$match"])]
            elif "$unwind" in stage:
                field = stage["$unwind"].lstrip("$")
                unwound = []
                for d in docs:
                    arr = d.get(field) or []
                    for item in arr:
                        nd = dict(d)
                        nd[field] = item
                        unwound.append(nd)
                docs = unwound
            elif "$group" in stage:
                spec = stage["$group"]
                id_expr = spec["_id"]
                field = id_expr.lstrip("$")
                groups = {}
                for d in docs:
                    k = d.get(field)
                    groups[k] = groups.get(k, 0) + 1
                count_key = [k for k in spec.keys() if k != "_id"][0]
                docs = [{"_id": k, count_key: v} for k, v in groups.items()]
            elif "$sort" in stage:
                for field, direction in reversed(list(stage["$sort"].items())):
                    docs.sort(key=lambda x: x.get(field, 0), reverse=(direction == -1))
            elif "$limit" in stage:
                docs = docs[: stage["$limit"]]
        return docs


class _MockDB:
    def __init__(self):
        self.users = _MockCollection()
        self.posts = _MockCollection()
        self.follows = _MockCollection()
        self.bookmarks = _MockCollection()
        self.likes = _MockCollection()
        self.stars = _MockCollection()
        self.thumbs = _MockCollection()
        self.comments = _MockCollection()
        self.notifications = _MockCollection()


def run_tests(use_mock_db=False):
    active_db = db_module.db
    mode_label = "LIVE MONGODB"
    if use_mock_db:
        active_db = _MockDB()
        app_module.db = active_db
        db_module.db = active_db
        notif_module.db = active_db
        mode_label = "IN-MEMORY MOCK DB"

    # Use isolated temporary upload directory so tests never pollute repo uploads/
    orig_upload_dir = app_module.app.config["POST_UPLOAD_DIR"]
    temp_upload_dir = tempfile.mkdtemp(prefix="sweeter_rich_media_test_")
    app_module.app.config["POST_UPLOAD_DIR"] = temp_upload_dir

    client = app_module.app.test_client()
    secret = app_module.app.config["SECRET_KEY"]

    test_results = []

    def record(num, name, passed, details=""):
        status = "PASS" if passed else "FAIL"
        color = GREEN if passed else RED
        print(f"[{color}{status}{RESET}] {num}. {name}: {details}")
        test_results.append((num, name, passed, details))

    u_author = "test_rm_author"
    u_other = "test_rm_other"
    all_users = [u_author, u_other]

    def make_token(username):
        return jwt.encode(
            {"id": username, "exp": datetime.now(timezone.utc) + timedelta(hours=2)},
            secret,
            algorithm="HS256",
        )

    try:
        # Clean & seed test users
        active_db.users.delete_many({"username": {"$in": all_users}})
        active_db.posts.delete_many({"username": {"$in": all_users}})
        active_db.bookmarks.delete_many({"username": {"$in": all_users}})
        active_db.notifications.delete_many({"$or": [{"recipient": {"$in": all_users}}, {"actor": {"$in": all_users}}]})

        active_db.users.insert_many([
            {
                "username": u_author,
                "password": "pw",
                "profile_name": "Rich Author",
                "profile_pic_real": "profile_pics/profile_placeholder.png",
                "profile_info": "Photographer",
            },
            {
                "username": u_other,
                "password": "pw",
                "profile_name": "Other User",
                "profile_pic_real": "profile_pics/profile_placeholder.png",
                "profile_info": "Viewer",
            },
        ])

        t_author = make_token(u_author)
        t_other = make_token(u_other)

        print("=" * 64)
        print(f"RUNNING RICH MEDIA POSTS TEST SUITE ({mode_label})")
        print("=" * 64)

        # ------------------------------------------------------------------
        # PART A: UPLOAD VALIDATION & SECURITY
        # ------------------------------------------------------------------
        client.set_cookie("mytoken", t_author)

        # 1. Single image upload succeeds (PNG)
        resp = client.post(
            "/posting",
            data={
                "comment_give": "Single image post #media",
                "date_give": datetime.now(timezone.utc).isoformat(),
                "images": (io.BytesIO(VALID_PNG_BYTES), "holiday.png"),
            },
            content_type="multipart/form-data",
        )
        d1 = resp.get_json() or {}
        p1_id = d1.get("post_id")
        imgs1 = d1.get("images", [])
        f1 = imgs1[0]["filename"] if imgs1 else ""
        safe_name_ok = bool(re.match(r"^[a-f0-9]{32}_[0-9]+\.png$", f1)) and ("holiday" not in f1)
        record(
            1,
            "Upload satu gambar berhasil & filename server aman/unik",
            resp.status_code == 200
            and d1.get("result") == "success"
            and len(imgs1) == 1
            and safe_name_ok
            and imgs1[0].get("mime_type") == "image/png"
            and imgs1[0].get("size") == len(VALID_PNG_BYTES)
            and os.path.isfile(os.path.join(temp_upload_dir, f1)),
            f"status={resp.status_code}, file={f1}",
        )

        # 2. Multiple images upload (4 images: JPG, JPEG, PNG, WebP) succeeds
        resp = client.post(
            "/posting",
            data={
                "comment_give": "Multi image gallery #gallery",
                "date_give": datetime.now(timezone.utc).isoformat(),
                "images": [
                    (io.BytesIO(VALID_JPEG_BYTES), "a.jpg"),
                    (io.BytesIO(VALID_JPEG_BYTES), "b.jpeg"),
                    (io.BytesIO(VALID_PNG_BYTES), "c.png"),
                    (io.BytesIO(VALID_WEBP_BYTES), "d.webp"),
                ],
            },
            content_type="multipart/form-data",
        )
        d2 = resp.get_json() or {}
        p2_id = d2.get("post_id")
        imgs2 = d2.get("images", [])
        record(
            2,
            "Upload beberapa gambar (4 format: JPG/JPEG/PNG/WebP) berhasil",
            resp.status_code == 200 and len(imgs2) == 4 and len({i["filename"] for i in imgs2}) == 4,
            f"status={resp.status_code}, count={len(imgs2)}",
        )

        # 3. Upload > 4 images rejected (400) and zero extra files written
        before_files = set(os.listdir(temp_upload_dir))
        resp = client.post(
            "/posting",
            data={
                "comment_give": "Too many images",
                "images": [(io.BytesIO(VALID_PNG_BYTES), f"img{i}.png") for i in range(5)],
            },
            content_type="multipart/form-data",
        )
        after_files = set(os.listdir(temp_upload_dir))
        record(
            3,
            "Upload lebih dari 4 gambar ditolak (400) tanpa sisa file",
            resp.status_code == 400 and before_files == after_files,
            f"status={resp.status_code}, msg={(resp.get_json() or {}).get('msg')}",
        )

        # 4. File > 5 MB rejected (400)
        oversized_bytes = VALID_PNG_BYTES + (b"\x00" * (5 * 1024 * 1024 + 10))
        resp = client.post(
            "/posting",
            data={
                "comment_give": "Too big",
                "images": (io.BytesIO(oversized_bytes), "huge.png"),
            },
            content_type="multipart/form-data",
        )
        record(
            4,
            "File melebihi 5 MB ditolak (400)",
            resp.status_code == 400 and set(os.listdir(temp_upload_dir)) == before_files,
            f"status={resp.status_code}, msg={(resp.get_json() or {}).get('msg')}",
        )

        # 5. Unsupported extension (.svg / .html) rejected AND spoofed extension (.png with HTML content) rejected
        resp_ext = client.post(
            "/posting",
            data={"comment_give": "Bad ext", "images": (io.BytesIO(b"<svg></svg>"), "vec.svg")},
            content_type="multipart/form-data",
        )
        resp_spoof = client.post(
            "/posting",
            data={"comment_give": "Spoofed", "images": (io.BytesIO(b"<html><script>alert(1)</script></html>"), "fake.png")},
            content_type="multipart/form-data",
        )
        resp_mismatch = client.post(
            "/posting",
            data={"comment_give": "Mismatch", "images": (io.BytesIO(VALID_JPEG_BYTES), "actually_jpeg.png")},
            content_type="multipart/form-data",
        )
        record(
            5,
            "Format tidak didukung & spoofing magic-byte ditolak (400)",
            resp_ext.status_code == 400 and resp_spoof.status_code == 400 and resp_mismatch.status_code == 400,
            f"ext={resp_ext.status_code}, spoof={resp_spoof.status_code}, mismatch={resp_mismatch.status_code}",
        )

        # 6. Empty file (0 bytes) rejected (400)
        resp_empty = client.post(
            "/posting",
            data={"comment_give": "Empty file", "images": (io.BytesIO(b""), "empty.png")},
            content_type="multipart/form-data",
        )
        record(
            6,
            "File kosong (0 byte) ditolak (400)",
            resp_empty.status_code == 400,
            f"status={resp_empty.status_code}",
        )

        # 7. Path traversal prevented on upload & serving
        resp_trav_up = client.post(
            "/posting",
            data={"comment_give": "Traversal", "images": (io.BytesIO(VALID_PNG_BYTES), "../../evil.png")},
            content_type="multipart/form-data",
        )
        resp_trav_get1 = client.get("/uploads/posts/../../.env")
        resp_trav_get2 = client.get("/uploads/posts/..%2F..%2Fapp.py")
        record(
            7,
            "Path traversal saat upload & akses file ditolak",
            resp_trav_up.status_code == 400
            and resp_trav_get1.status_code in (400, 404)
            and resp_trav_get2.status_code in (400, 404),
            f"up={resp_trav_up.status_code}, get1={resp_trav_get1.status_code}, get2={resp_trav_get2.status_code}",
        )

        # 8. Valid uploaded image served cleanly with X-Content-Type-Options: nosniff
        resp_serve = client.get(imgs1[0]["url"])
        record(
            8,
            "Gambar terunggah disajikan aman dengan header nosniff",
            resp_serve.status_code == 200
            and resp_serve.data == VALID_PNG_BYTES
            and resp_serve.headers.get("X-Content-Type-Options") == "nosniff",
            f"status={resp_serve.status_code}, nosniff={resp_serve.headers.get('X-Content-Type-Options')}",
        )

        # ------------------------------------------------------------------
        # PART B: POST CREATION, BACKWARD COMPATIBILITY & MULTI-SURFACE RENDERING
        # ------------------------------------------------------------------

        # 9. Text-only post still works (both form-urlencoded and multipart)
        resp_text = client.post(
            "/posting",
            data={"comment_give": "Text only legacy post", "date_give": datetime.now(timezone.utc).isoformat()},
        )
        d_text = resp_text.get_json() or {}
        record(
            9,
            "Post teks tanpa gambar tetap berfungsi",
            resp_text.status_code == 200 and d_text.get("result") == "success" and d_text.get("images") == [],
            f"status={resp_text.status_code}",
        )

        # 10. Image-only post (no text) succeeds
        resp_img_only = client.post(
            "/posting",
            data={"comment_give": "", "images": (io.BytesIO(VALID_WEBP_BYTES), "solo.webp")},
            content_type="multipart/form-data",
        )
        d_img_only = resp_img_only.get_json() or {}
        p_img_only_id = d_img_only.get("post_id")
        record(
            10,
            "Post gambar tanpa teks (image-only) berhasil dibuat",
            resp_img_only.status_code == 200 and len(d_img_only.get("images", [])) == 1,
            f"status={resp_img_only.status_code}",
        )

        # 11. Legacy post in MongoDB WITHOUT 'images' field renders safely in /get_posts
        legacy_res = active_db.posts.insert_one({
            "username": u_author,
            "profile_name": "Rich Author",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "comment": "Old post before rich media",
            "date": datetime.now(timezone.utc).isoformat(),
        })
        legacy_pid = str(legacy_res.inserted_id)

        # 12. Images appear across Feed, Profile, Trending, Bookmarks, Post Detail, Repost, Quote
        # Other user reposts p1_id, quotes p2_id, and bookmarks p1_id
        client.set_cookie("mytoken", t_other)
        client.post(f"/api/posts/{p1_id}/repost")
        resp_q = client.post(f"/api/posts/{p2_id}/quote", json={"comment": "Look at these 4 photos!"})
        q_id = (resp_q.get_json() or {}).get("post_id")
        client.post(f"/api/posts/{p1_id}/bookmark")

        # Check feed (explore), profile, trending, bookmarks, post detail
        feed_posts = (client.get("/get_posts?tab=explore&limit=20").get_json() or {}).get("posts", [])
        prof_posts = (client.get(f"/get_posts?username_give={u_author}&limit=20").get_json() or {}).get("posts", [])
        trend_posts = (client.get("/api/trending?period=all&limit=20").get_json() or {}).get("posts", [])
        bmk_posts = (client.get("/api/bookmarks").get_json() or {}).get("posts", [])
        detail_post = (client.get(f"/api/posts/{p1_id}").get_json() or {}).get("post") or {}
        detail_html = client.get(f"/post/{p1_id}")

        legacy_in_feed = next((p for p in feed_posts if p["_id"] == legacy_pid), None)
        p1_in_prof = next((p for p in prof_posts if p["_id"] == p1_id), None)
        p2_in_trend = next((p for p in trend_posts if p["_id"] == p2_id), None)
        p1_in_bmk = next((p for p in bmk_posts if p["_id"] == p1_id), None)
        repost_card = next((p for p in feed_posts if p.get("post_type") == "repost" and p.get("original_post_id") == p1_id), None)
        quote_card = next((p for p in feed_posts if p.get("_id") == q_id), None)

        all_rendered = (
            legacy_in_feed is not None and legacy_in_feed.get("images") == []
            and p1_in_prof is not None and len(p1_in_prof.get("images", [])) == 1
            and p2_in_trend is not None and len(p2_in_trend.get("images", [])) == 4
            and p1_in_bmk is not None and len(p1_in_bmk.get("images", [])) == 1
            and detail_post.get("_id") == p1_id and len(detail_post.get("images", [])) == 1
            and detail_html.status_code == 200
            and repost_card is not None and len((repost_card.get("original_post") or {}).get("images", [])) == 1
            and quote_card is not None and len((quote_card.get("original_post") or {}).get("images", [])) == 4
        )
        record(
            11,
            "Post lama aman & metadata gambar tampil di Feed, Profile, Trending, Bookmarks, Detail, Repost, Quote",
            all_rendered,
            f"legacy_ok={legacy_in_feed is not None}, detail_html={detail_html.status_code}",
        )

        # ------------------------------------------------------------------
        # PART C: EDIT & DELETE INTEGRATION
        # ------------------------------------------------------------------
        client.set_cookie("mytoken", t_author)

        # 12. Edit text-only preserves existing images
        resp_edit_txt = client.post(
            f"/api/posts/{p1_id}/edit",
            data={"comment_give": "Updated caption keeping image"},
        )
        ed1 = resp_edit_txt.get_json() or {}
        record(
            12,
            "Edit teks post tetap mempertahankan gambar yang sudah ada",
            resp_edit_txt.status_code == 200
            and len(ed1.get("images", [])) == 1
            and ed1["images"][0]["filename"] == f1
            and os.path.isfile(os.path.join(temp_upload_dir, f1)),
            f"status={resp_edit_txt.status_code}",
        )

        # 13. Edit with keep_images + adding 1 new image removes dropped image safely AFTER update
        resp_edit_img = client.post(
            f"/api/posts/{p2_id}/edit",
            data={
                "comment_give": "Replaced partial gallery",
                "keep_images": json.dumps([imgs2[0]["filename"], imgs2[1]["filename"]]),
                "images": (io.BytesIO(VALID_PNG_BYTES), "new_extra.png"),
            },
            content_type="multipart/form-data",
        )
        ed2 = resp_edit_img.get_json() or {}
        removed_2 = imgs2[2]["filename"]
        removed_3 = imgs2[3]["filename"]
        kept_0 = imgs2[0]["filename"]
        record(
            13,
            "Edit gambar menjaga yang dipertahankan, menambah baru, dan menghapus gambar lama yang dibuang",
            resp_edit_img.status_code == 200
            and len(ed2.get("images", [])) == 3
            and os.path.isfile(os.path.join(temp_upload_dir, kept_0))
            and not os.path.exists(os.path.join(temp_upload_dir, removed_2))
            and not os.path.exists(os.path.join(temp_upload_dir, removed_3)),
            f"new_count={len(ed2.get('images', []))}",
        )

        # 14. Deleting a REPOST does NOT delete original post's image file!
        repost_id = repost_card["_id"]
        client.set_cookie("mytoken", t_other)
        resp_del_rep = client.post(f"/api/posts/{repost_id}/delete")
        record(
            14,
            "Menghapus Repost tidak menghapus file gambar milik postingan asli",
            resp_del_rep.status_code == 200 and os.path.isfile(os.path.join(temp_upload_dir, f1)),
            f"status={resp_del_rep.status_code}, orig_file_exists={os.path.isfile(os.path.join(temp_upload_dir, f1))}",
        )

        # 15. Deleting post cleans up its image file, while NOT deleting shared/other posts' files
        client.set_cookie("mytoken", t_author)
        other_img_fname = d_img_only["images"][0]["filename"]
        resp_del_p1 = client.post(f"/api/posts/{p1_id}/delete")
        record(
            15,
            "Hapus post menghapus file gambar terkait tanpa menghapus file post lain",
            resp_del_p1.status_code == 200
            and not os.path.exists(os.path.join(temp_upload_dir, f1))
            and os.path.isfile(os.path.join(temp_upload_dir, other_img_fname)),
            f"deleted_f1={not os.path.exists(os.path.join(temp_upload_dir, f1))}, other_preserved={os.path.isfile(os.path.join(temp_upload_dir, other_img_fname))}",
        )

        # ------------------------------------------------------------------
        # PART D: FRONTEND ASSETS & LIGHTBOX / PREVIEW CONTRACT CHECKS
        # ------------------------------------------------------------------
        with open(os.path.join(app_module.app.root_path, "static", "myjs.js"), encoding="utf-8") as fh:
            myjs_src = fh.read()
        with open(os.path.join(app_module.app.root_path, "static", "mystyle.css"), encoding="utf-8") as fh:
            css_src = fh.read()
        with open(os.path.join(app_module.app.root_path, "templates", "post.html"), encoding="utf-8") as fh:
            post_html_src = fh.read()

        ui_checks = all([
            "init_rich_post_composer" in myjs_src,
            "URL.createObjectURL" in myjs_src and "URL.revokeObjectURL" in myjs_src,
            "render_post_images_html" in myjs_src,
            "handle_post_image_error" in myjs_src,
            "open_image_lightbox" in myjs_src and "close_image_lightbox" in myjs_src,
            "lightbox_prev" in myjs_src and "lightbox_next" in myjs_src,
            "e.key === 'Escape'" in myjs_src,
            "post-media-grid-1" in css_src and "post-media-grid-4" in css_src,
            "post-image-input" in post_html_src and "post-image-preview-container" in post_html_src,
        ])
        record(
            16,
            "Kontrak Frontend Preview, Grid 1-4, Fallback & Lightbox (Escape/Navigasi) lengkap",
            ui_checks,
            "Verified across myjs.js, mystyle.css, and post.html",
        )

    finally:
        # Cleanup DB and temp folder
        active_db.users.delete_many({"username": {"$in": all_users}})
        active_db.posts.delete_many({"username": {"$in": all_users}})
        active_db.bookmarks.delete_many({"username": {"$in": all_users}})
        active_db.notifications.delete_many({"$or": [{"recipient": {"$in": all_users}}, {"actor": {"$in": all_users}}]})
        app_module.app.config["POST_UPLOAD_DIR"] = orig_upload_dir
        shutil.rmtree(temp_upload_dir, ignore_errors=True)

    print("=" * 64)
    passed_count = sum(1 for r in test_results if r[2])
    total_count = len(test_results)
    print(f"RESULTS ({mode_label}): {passed_count}/{total_count} tests passed.")
    print("=" * 64)

    if passed_count != total_count:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock-db", action="store_true", help="Use in-memory PyMongo mock when live Atlas is unavailable")
    args = parser.parse_args()
    run_tests(use_mock_db=args.mock_db)
