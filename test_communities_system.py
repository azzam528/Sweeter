import io
import os
import sys
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
    _MockDB,
    VALID_JPEG_BYTES,
    VALID_PNG_BYTES,
    VALID_WEBP_BYTES,
)


def run_tests(use_mock_db=False):
    active_db = db_module.db
    mode_label = "LIVE MONGODB"
    if use_mock_db:
        active_db = _MockDB()
        app_module.db = active_db
        db_module.db = active_db
        notif_module.db = active_db
        mode_label = "IN-MEMORY MOCK DB"

    orig_post_dir = app_module.app.config["POST_UPLOAD_DIR"]
    orig_comm_dir = app_module.app.config["COMMUNITY_UPLOAD_DIR"]
    temp_post_dir = tempfile.mkdtemp(prefix="sweeter_comm_posts_")
    temp_comm_dir = tempfile.mkdtemp(prefix="sweeter_comm_media_")
    app_module.app.config["POST_UPLOAD_DIR"] = temp_post_dir
    app_module.app.config["COMMUNITY_UPLOAD_DIR"] = temp_comm_dir

    client = app_module.app.test_client()
    secret = app_module.app.config["SECRET_KEY"]
    test_results = []

    def record(num, name, passed, details=""):
        status = "PASS" if passed else "FAIL"
        color = GREEN if passed else RED
        print(f"[{color}{status}{RESET}] {num}. {name}: {details}")
        test_results.append((num, name, passed, details))

    u_creator = "comm_creator"
    u_member = "comm_member"
    u_outsider = "comm_outsider"
    all_users = [u_creator, u_member, u_outsider]

    def make_token(username):
        return jwt.encode(
            {"id": username, "exp": datetime.now(timezone.utc) + timedelta(hours=2)},
            secret,
            algorithm="HS256",
        )

    def auth(token):
        client.set_cookie("mytoken", token)

    def clear_auth():
        client.delete_cookie("mytoken")

    try:
        active_db.users.delete_many({"username": {"$in": all_users}})
        active_db.posts.delete_many({"username": {"$in": all_users}})
        active_db.communities.delete_many({"creator_id": {"$in": all_users}})
        active_db.community_members.delete_many({"user_id": {"$in": all_users}})
        active_db.community_requests.delete_many({"user_id": {"$in": all_users}})
        active_db.notifications.delete_many({"$or": [{"recipient": {"$in": all_users}}, {"actor": {"$in": all_users}}]})

        active_db.users.insert_many([
            {
                "username": u_creator,
                "password": "pw",
                "profile_name": "Creator One",
                "profile_pic_real": "profile_pics/profile_placeholder.png",
                "profile_info": "Community builder",
            },
            {
                "username": u_member,
                "password": "pw",
                "profile_name": "Member Two",
                "profile_pic_real": "profile_pics/profile_placeholder.png",
                "profile_info": "Active participant",
            },
            {
                "username": u_outsider,
                "password": "pw",
                "profile_name": "Outsider Three",
                "profile_pic_real": "profile_pics/profile_placeholder.png",
                "profile_info": "Observer",
            },
        ])

        t_creator = make_token(u_creator)
        t_member = make_token(u_member)
        t_outsider = make_token(u_outsider)

        print("=" * 68)
        print(f"RUNNING COMMUNITY / CIRCLES TEST SUITE ({mode_label})")
        print("=" * 68)

        # ------------------------------------------------------------------
        # 1. Authentication Enforcement
        # ------------------------------------------------------------------
        clear_auth()
        r_page = client.get("/communities")
        r_api = client.get("/api/communities")
        r_post = client.post("/api/communities", json={"name": "No Auth"})
        record(
            "1.1",
            "Unauthenticated requests blocked on /communities and /api/communities",
            r_page.status_code == 302 and r_api.status_code == 401 and r_post.status_code == 401,
            f"page={r_page.status_code}, list={r_api.status_code}, create={r_post.status_code}",
        )

        # ------------------------------------------------------------------
        # 2. Community Creation & Validation
        # ------------------------------------------------------------------
        auth(t_creator)
        r_short = client.post("/api/communities", json={"name": "ab"})
        r_bad_vis = client.post("/api/communities", json={"name": "Valid Name", "visibility": "hidden"})
        r_reserved = client.post("/api/communities", json={"name": "Reserved", "slug": "create"})
        r_bad_img = client.post(
            "/api/communities",
            data={
                "name": "Bad Media Circle",
                "slug": "bad-media-circle",
                "profile_image": (io.BytesIO(b"not an image"), "bad.txt"),
            },
            content_type="multipart/form-data",
        )
        record(
            "2.1",
            "Reject invalid name, visibility, reserved slug, and fake image upload",
            r_short.status_code == 400
            and r_bad_vis.status_code == 400
            and r_reserved.status_code == 400
            and r_bad_img.status_code == 400,
            f"short={r_short.status_code}, vis={r_bad_vis.status_code}, resv={r_reserved.status_code}, img={r_bad_img.status_code}",
        )

        # Create public community with auto-generated slug
        r_pub_create = client.post(
            "/api/communities",
            json={
                "name": "Python Indonesia",
                "description": "Komunitas Python developer",
                "visibility": "public",
            },
        )
        pub_data = (r_pub_create.get_json() or {}).get("community") or {}
        pub_slug = pub_data.get("slug")
        pub_id = pub_data.get("_id")
        record(
            "2.2",
            "Create public community returns 201 with creator membership and member_count=1",
            r_pub_create.status_code == 201
            and pub_slug == "python-indonesia"
            and pub_data.get("creator_id") == u_creator
            and pub_data.get("member_count") == 1
            and pub_data.get("is_member") is True
            and pub_data.get("my_role") == "creator",
            f"status={r_pub_create.status_code}, slug={pub_slug}, count={pub_data.get('member_count')}",
        )

        # Duplicate slug prevented
        r_dup_slug = client.post(
            "/api/communities",
            json={"name": "Python ID Clone", "slug": "python-indonesia"},
        )
        record(
            "2.3",
            "Duplicate community slug prevented with 409 Conflict",
            r_dup_slug.status_code == 409,
            f"status={r_dup_slug.status_code}",
        )

        # Create private community with profile_image & cover_image
        r_priv_create = client.post(
            "/api/communities",
            data={
                "name": "Inner Circle Designers",
                "slug": "inner-designers",
                "description": "Diskusi desain tertutup",
                "visibility": "private",
                "profile_image": (io.BytesIO(VALID_PNG_BYTES), "avatar.png"),
                "cover_image": (io.BytesIO(VALID_JPEG_BYTES), "cover.jpg"),
            },
            content_type="multipart/form-data",
        )
        priv_data = (r_priv_create.get_json() or {}).get("community") or {}
        priv_slug = priv_data.get("slug")
        priv_id = priv_data.get("_id")
        av_fname = (priv_data.get("profile_image") or {}).get("filename", "")
        cv_fname = (priv_data.get("cover_image") or {}).get("filename", "")
        files_ok = (
            bool(av_fname and os.path.isfile(os.path.join(temp_comm_dir, av_fname)))
            and bool(cv_fname and os.path.isfile(os.path.join(temp_comm_dir, cv_fname)))
        )
        with client.get(f"/uploads/communities/{av_fname}") as r_serve_av:
            serve_ok = r_serve_av.status_code == 200 and r_serve_av.headers.get("X-Content-Type-Options") == "nosniff"
        with client.get("/uploads/communities/../../app.py") as r_trav:
            trav_blocked = r_trav.status_code == 404

        record(
            "2.4",
            "Create private community with profile & cover images stores files safely & serves securely",
            r_priv_create.status_code == 201 and files_ok and serve_ok and trav_blocked and priv_data.get("visibility") == "private",
            f"status={r_priv_create.status_code}, files_ok={files_ok}, serve_ok={serve_ok}, trav={r_trav.status_code}",
        )

        # ------------------------------------------------------------------
        # 3. Discovery & Visibility Filtering
        # ------------------------------------------------------------------
        auth(t_member)
        r_list_all = client.get("/api/communities?tab=all")
        all_slugs = [c["slug"] for c in ((r_list_all.get_json() or {}).get("communities") or [])]
        r_list_search_priv = client.get("/api/communities?tab=all&q=inner-designers")
        search_priv_slugs = [c["slug"] for c in ((r_list_search_priv.get_json() or {}).get("communities") or [])]
        r_detail_html = client.get(f"/communities/{pub_slug}")
        record(
            "3.1",
            "Public community discoverable; private excluded from unsearched list; detail page 200",
            pub_slug in all_slugs
            and priv_slug not in all_slugs
            and priv_slug in search_priv_slugs
            and r_detail_html.status_code == 200,
            f"all={all_slugs}, search_priv={search_priv_slugs}, page={r_detail_html.status_code}",
        )

        # ------------------------------------------------------------------
        # 4. Public Join & Leave Membership
        # ------------------------------------------------------------------
        r_join_pub = client.post(f"/api/communities/{pub_slug}/join")
        pub_after_join = (r_join_pub.get_json() or {}).get("community") or {}
        r_dup_join = client.post(f"/api/communities/{pub_slug}/join")
        r_leave_pub = client.post(f"/api/communities/{pub_slug}/leave")
        pub_after_leave = (r_leave_pub.get_json() or {}).get("community") or {}

        auth(t_creator)
        r_creator_leave = client.post(f"/api/communities/{pub_slug}/leave")
        record(
            "4.1",
            "Join public community increments count, blocks dup join (409), leave decrements, creator cannot leave",
            r_join_pub.status_code == 200
            and pub_after_join.get("member_count") == 2
            and r_dup_join.status_code == 409
            and r_leave_pub.status_code == 200
            and pub_after_leave.get("member_count") == 1
            and r_creator_leave.status_code == 400,
            f"join={r_join_pub.status_code}, dup={r_dup_join.status_code}, leave={r_leave_pub.status_code}, creator_leave={r_creator_leave.status_code}",
        )

        # ------------------------------------------------------------------
        # 5. Private Community Requests & Invitations
        # ------------------------------------------------------------------
        auth(t_member)
        r_priv_direct_join = client.post(f"/api/communities/{priv_slug}/join")
        r_priv_req = client.post(f"/api/communities/{priv_slug}/request")
        r_priv_req_dup = client.post(f"/api/communities/{priv_slug}/request")
        r_priv_cancel = client.post(f"/api/communities/{priv_slug}/request/cancel")
        r_priv_req2 = client.post(f"/api/communities/{priv_slug}/request")
        r_nonmgr_list_req = client.get(f"/api/communities/{priv_slug}/requests")

        # Outsider is blocked from seeing private members or posts
        auth(t_outsider)
        r_priv_members_blocked = client.get(f"/api/communities/{priv_slug}/members")
        r_priv_posts_blocked = client.get(f"/api/communities/{priv_slug}/posts")

        # Creator approves member's join request & invites outsider
        auth(t_creator)
        r_mgr_list_req = client.get(f"/api/communities/{priv_slug}/requests")
        req_users = [r["user_id"] for r in ((r_mgr_list_req.get_json() or {}).get("requests") or [])]
        r_approve = client.post(f"/api/communities/{priv_slug}/requests/{u_member}/approve")
        priv_after_approve = (r_approve.get_json() or {}).get("community") or {}

        r_invite = client.post(f"/api/communities/{priv_slug}/invite", json={"username": u_outsider})

        # Outsider sees invite tab and accepts invitation
        auth(t_outsider)
        r_invited_tab = client.get("/api/communities?tab=invited")
        inv_slugs = [c["slug"] for c in ((r_invited_tab.get_json() or {}).get("communities") or [])]
        r_accept_inv = client.post(f"/api/communities/{priv_slug}/invite/accept")
        priv_after_inv = (r_accept_inv.get_json() or {}).get("community") or {}

        record(
            "5.1",
            "Private join blocked (403); request -> approve and invite -> accept workflows update membership cleanly",
            r_priv_direct_join.status_code == 403
            and r_priv_req.status_code == 201
            and r_priv_req_dup.status_code == 409
            and r_priv_cancel.status_code == 200
            and r_priv_req2.status_code == 201
            and r_nonmgr_list_req.status_code == 403
            and r_priv_members_blocked.status_code == 403
            and r_priv_posts_blocked.status_code == 403
            and u_member in req_users
            and r_approve.status_code == 200
            and priv_after_approve.get("member_count") == 2
            and r_invite.status_code == 201
            and priv_slug in inv_slugs
            and r_accept_inv.status_code == 200
            and priv_after_inv.get("member_count") == 3,
            f"direct={r_priv_direct_join.status_code}, req={r_priv_req.status_code}, approve={priv_after_approve.get('member_count')}, inv_accept={priv_after_inv.get('member_count')}",
        )

        # ------------------------------------------------------------------
        # 6. Community Posts & Private Circle Isolation
        # ------------------------------------------------------------------
        # Make outsider leave private circle before testing private post isolation
        auth(t_outsider)
        client.post(f"/api/communities/{priv_slug}/leave")

        # Outsider cannot post inside private or unjoined community
        r_outsider_post = client.post(f"/api/communities/{priv_slug}/posts", json={"comment": "Intruder"})

        # Member posts inside private community (with image + hashtag)
        auth(t_member)
        r_comm_post = client.post(
            f"/api/communities/{priv_slug}/posts",
            data={
                "comment_give": "Rahasia desain UI internal #SecretDesign",
                "images": [(io.BytesIO(VALID_WEBP_BYTES), "sketch.webp")],
            },
            content_type="multipart/form-data",
        )
        comm_post_id = (r_comm_post.get_json() or {}).get("post_id")

        # Member ALSO posts to public community after joining it
        client.post(f"/api/communities/{pub_slug}/join")
        r_pub_post_via_posting = client.post(
            "/posting",
            data={"comment_give": "Halo Pythonista #PyID", "community_id": pub_id},
        )

        # Member CAN read private community feed & single post
        r_member_priv_feed = client.get(f"/api/communities/{priv_slug}/posts")
        member_priv_ids = [p["_id"] for p in ((r_member_priv_feed.get_json() or {}).get("posts") or [])]

        # Outsider CANNOT see private post in ANY public endpoint or direct interaction!
        auth(t_outsider)
        r_out_feed = client.get("/get_posts?tab=explore")
        r_out_profile = client.get(f"/get_posts?username_give={u_member}")
        r_out_trending = client.get("/api/trending")
        r_out_tag = client.get("/api/tags/secretdesign/posts")
        r_out_search = client.get("/api/search?q=Rahasia")
        r_out_single = client.get(f"/api/posts/{comm_post_id}")
        r_out_html = client.get(f"/post/{comm_post_id}")
        r_out_comment = client.post("/create_comment", data={"post_id_give": comm_post_id, "content_give": "Peek"})
        r_out_get_cmt = client.get(f"/get_comments?post_id_give={comm_post_id}")
        r_out_like = client.post("/update_like", data={"post_id_give": comm_post_id, "type_give": "heart", "action_give": "like"})
        r_out_bm = client.post(f"/api/posts/{comm_post_id}/bookmark")
        r_out_repost = client.post(f"/api/posts/{comm_post_id}/repost")
        r_out_quote = client.post(f"/api/posts/{comm_post_id}/quote", json={"comment": "Leak"})

        leaked = any(
            comm_post_id in [p["_id"] for p in ((resp.get_json() or {}).get("posts") or [])]
            for resp in (r_out_feed, r_out_profile, r_out_trending, r_out_tag, r_out_search)
        )
        interactions_blocked = all(
            resp.status_code == 403
            for resp in (r_out_single, r_out_comment, r_out_get_cmt, r_out_like, r_out_bm, r_out_repost, r_out_quote)
        )
        record(
            "6.1",
            "Private community posts accessible to members and strictly isolated from outsiders across all endpoints",
            r_outsider_post.status_code == 403
            and r_comm_post.status_code == 201
            and r_pub_post_via_posting.status_code == 200
            and comm_post_id in member_priv_ids
            and not leaked
            and interactions_blocked
            and r_out_html.status_code == 404,
            f"comm_post={r_comm_post.status_code}, leaked={leaked}, blocked_403={interactions_blocked}, html={r_out_html.status_code}",
        )

        # ------------------------------------------------------------------
        # 7. Management, Roles, Avatar Replacement & Deletion
        # ------------------------------------------------------------------
        auth(t_creator)
        r_promo = client.post(f"/api/communities/{priv_slug}/members/{u_member}/role", json={"role": "moderator"})
        r_update = client.post(
            f"/api/communities/{priv_slug}/update",
            data={
                "name": "Inner Circle Designers v2",
                "profile_image": (io.BytesIO(VALID_WEBP_BYTES), "new_avatar.webp"),
            },
            content_type="multipart/form-data",
        )
        new_av = ((r_update.get_json() or {}).get("community") or {}).get("profile_image", {}).get("filename", "")
        old_av_deleted = not os.path.exists(os.path.join(temp_comm_dir, av_fname))
        new_av_exists = bool(new_av and os.path.isfile(os.path.join(temp_comm_dir, new_av)))

        # Non-creator cannot delete community
        auth(t_member)
        r_del_forbid = client.delete(f"/api/communities/{priv_slug}")

        # Creator deletes community -> removes community, members, posts, and media files
        auth(t_creator)
        r_del_ok = client.delete(f"/api/communities/{priv_slug}")
        r_after_del = client.get(f"/api/communities/{priv_slug}")
        new_av_cleaned = not os.path.exists(os.path.join(temp_comm_dir, new_av))
        cv_cleaned = not os.path.exists(os.path.join(temp_comm_dir, cv_fname))
        posts_cleaned = active_db.posts.count_documents({"community_id": priv_id}) == 0

        record(
            "7.1",
            "Role update, image replacement cleanup, creator-only delete & cascade cleanup",
            r_promo.status_code == 200
            and r_update.status_code == 200
            and old_av_deleted
            and new_av_exists
            and r_del_forbid.status_code == 403
            and r_del_ok.status_code == 200
            and r_after_del.status_code == 404
            and new_av_cleaned
            and cv_cleaned
            and posts_cleaned,
            f"old_del={old_av_deleted}, forbid={r_del_forbid.status_code}, cleaned={new_av_cleaned and cv_cleaned and posts_cleaned}",
        )

        # ------------------------------------------------------------------
        # 8. Frontend Template & JS Contract Verification
        # ------------------------------------------------------------------
        base_dir = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base_dir, "static", "myjs.js"), encoding="utf-8") as f:
            js_src = f.read()
        with open(os.path.join(base_dir, "templates", "communities.html"), encoding="utf-8") as f:
            comm_html = f.read()
        with open(os.path.join(base_dir, "templates", "community_detail.html"), encoding="utf-8") as f:
            detail_html = f.read()
        with open(os.path.join(base_dir, "templates", "post.html"), encoding="utf-8") as f:
            post_html = f.read()

        js_ok = all(
            tok in js_src
            for tok in [
                "load_communities",
                "submit_create_community",
                "init_community_detail_page",
                "join_active_community",
                "leave_active_community",
                "request_active_community",
                "approve_community_request",
                "submit_invite_member",
                "submit_community_post",
            ]
        )
        tpl_ok = (
            'id="create-community-modal"' in comm_html
            and 'id="community-detail-root"' in detail_html
            and 'id="post-community-select"' in post_html
            and 'href="/communities"' in post_html
        )
        record(
            "8.1",
            "Frontend Community templates, navigation links, and JS controllers present",
            js_ok and tpl_ok,
            f"js_ok={js_ok}, tpl_ok={tpl_ok}",
        )

    finally:
        app_module.app.config["POST_UPLOAD_DIR"] = orig_post_dir
        app_module.app.config["COMMUNITY_UPLOAD_DIR"] = orig_comm_dir
        shutil.rmtree(temp_post_dir, ignore_errors=True)
        shutil.rmtree(temp_comm_dir, ignore_errors=True)

    passed_count = sum(1 for _, _, p, _ in test_results if p)
    total_count = len(test_results)
    print("-" * 68)
    print(f"RESULT ({mode_label}): {passed_count}/{total_count} passed")
    print("=" * 68)
    return passed_count == total_count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Sweeter Community / Circles tests")
    parser.add_argument("--mock-db", action="store_true", help="Use in-memory mock DB instead of MongoDB")
    args = parser.parse_args()
    success = run_tests(use_mock_db=args.mock_db)
    sys.exit(0 if success else 1)
