import os
import sys
import json
import jwt
from datetime import datetime, timedelta, timezone
from bson import ObjectId

from app import app
from database import db

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

test_results = []

def record(test_num, name, passed, details=""):
    status = "PASS" if passed else "FAIL"
    color = GREEN if passed else RED
    print(f"[{color}{status}{RESET}] {test_num}. {name}: {details}")
    test_results.append({
        "test_num": test_num,
        "name": name,
        "status": status,
        "details": details
    })

def run_tests():
    client = app.test_client()
    secret = app.config["SECRET_KEY"]

    u_owner = "test_bmp_owner"
    u_other = "test_bmp_other"
    all_users = [u_owner, u_other]

    # Cleanup any old test data
    db.users.delete_many({"username": {"$in": all_users}})
    db.posts.delete_many({"username": {"$in": all_users}})
    db.bookmarks.delete_many({"username": {"$in": all_users}})
    db.likes.delete_many({"username": {"$in": all_users}})
    db.stars.delete_many({"username": {"$in": all_users}})
    db.thumbs.delete_many({"username": {"$in": all_users}})
    db.comments.delete_many({"username": {"$in": all_users}})

    # Insert test users
    db.users.insert_many([
        {
            "username": u_owner,
            "password": "hashed_pw_owner",
            "profile_name": "Post Owner",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Owner of posts"
        },
        {
            "username": u_other,
            "password": "hashed_pw_other",
            "profile_name": "Other User",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Other test user"
        }
    ])

    def create_token(username):
        payload = {
            "id": username,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1)
        }
        return jwt.encode(payload, secret, algorithm="HS256")

    token_owner = create_token(u_owner)
    token_other = create_token(u_other)

    now_iso = datetime.now(timezone.utc).isoformat()

    # Create test posts
    res1 = db.posts.insert_one({
        "username": u_owner,
        "profile_name": "Post Owner",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "Owner initial post 1",
        "date": now_iso
    })
    post1_id = str(res1.inserted_id)

    res2 = db.posts.insert_one({
        "username": u_owner,
        "profile_name": "Post Owner",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "Owner initial post 2",
        "date": now_iso
    })
    post2_id = str(res2.inserted_id)

    res_other = db.posts.insert_one({
        "username": u_other,
        "profile_name": "Other User",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "Other user's post",
        "date": now_iso
    })
    post_other_id = str(res_other.inserted_id)

    print("==================================================")
    print("RUNNING SWEETER V2: BOOKMARKS & POST MANAGEMENT TESTS")
    print("==================================================")

    # -------------------------------------------------------------------------
    # PART A: BOOKMARKS TESTS
    # -------------------------------------------------------------------------

    # 1. Bookmark post succeeds (POST /api/posts/<post_id>/bookmark)
    client.set_cookie('mytoken', token_other)
    resp = client.post(f"/api/posts/{post1_id}/bookmark")
    data = resp.get_json() or {}
    record(1, "Bookmark post via URL parameter succeeds",
           resp.status_code == 200 and data.get("result") == "success" and data.get("is_bookmarked") is True,
           f"Status: {resp.status_code}, data: {data}")

    # 2. Duplicate bookmark returns 409 Conflict
    resp = client.post(f"/api/posts/{post1_id}/bookmark")
    data = resp.get_json() or {}
    record(2, "Duplicate bookmark returns 409 Conflict",
           resp.status_code == 409 and data.get("result") == "fail" and data.get("is_bookmarked") is True,
           f"Status: {resp.status_code}, msg: {data.get('msg')}")

    # 3. Bookmark post via /api/bookmark (form-data / JSON)
    resp = client.post("/api/bookmark", data={"post_id_give": post2_id})
    data = resp.get_json() or {}
    record(3, "Bookmark post via /api/bookmark endpoint succeeds",
           resp.status_code == 200 and data.get("result") == "success" and data.get("is_bookmarked") is True,
           f"Status: {resp.status_code}, data: {data}")

    # 4. Unauthenticated bookmark returns 401
    client.delete_cookie('mytoken')
    resp = client.post(f"/api/posts/{post1_id}/bookmark")
    record(4, "Unauthenticated bookmark returns 401",
           resp.status_code == 401,
           f"Status: {resp.status_code}")

    # 5. Bookmark with invalid post ID format returns 400
    client.set_cookie('mytoken', token_other)
    resp = client.post("/api/posts/invalid-id-format/bookmark")
    record(5, "Bookmark invalid ID format returns 400",
           resp.status_code == 400,
           f"Status: {resp.status_code}")

    # 6. Bookmark nonexistent post returns 404
    nonexistent_id = str(ObjectId())
    resp = client.post(f"/api/posts/{nonexistent_id}/bookmark")
    record(6, "Bookmark nonexistent post returns 404",
           resp.status_code == 404,
           f"Status: {resp.status_code}")

    # 7. Unbookmark post succeeds (POST /api/posts/<post_id>/unbookmark)
    resp = client.post(f"/api/posts/{post1_id}/unbookmark")
    data = resp.get_json() or {}
    record(7, "Unbookmark post via URL parameter succeeds",
           resp.status_code == 200 and data.get("result") == "success" and data.get("is_bookmarked") is False,
           f"Status: {resp.status_code}, data: {data}")

    # 8. Unbookmark already unbookmarked post is idempotent (returns 200)
    resp = client.post(f"/api/posts/{post1_id}/unbookmark")
    data = resp.get_json() or {}
    record(8, "Unbookmark is safe and idempotent (returns 200)",
           resp.status_code == 200 and data.get("result") == "success" and data.get("is_bookmarked") is False,
           f"Status: {resp.status_code}, data: {data}")

    # 9. Alternative endpoint POST /api/unbookmark
    resp = client.post("/api/unbookmark", data={"post_id_give": post2_id})
    data = resp.get_json() or {}
    record(9, "Alternative endpoint POST /api/unbookmark succeeds",
           resp.status_code == 200 and data.get("result") == "success",
           f"Status: {resp.status_code}")

    # Setup specific bookmarks for retrieval testing:
    # u_other bookmarks post1 and post2; u_owner bookmarks post_other
    client.set_cookie('mytoken', token_other)
    client.post(f"/api/posts/{post1_id}/bookmark")
    client.post(f"/api/posts/{post2_id}/bookmark")

    client.set_cookie('mytoken', token_owner)
    client.post(f"/api/posts/{post_other_id}/bookmark")

    # 10. GET /api/bookmarks returns ONLY the authenticated user's bookmarks (Private)
    client.set_cookie('mytoken', token_other)
    resp = client.get("/api/bookmarks")
    data = resp.get_json() or {}
    posts = data.get("posts", [])
    p_ids = [p["_id"] for p in posts]
    record(10, "GET /api/bookmarks returns private list of bookmarks",
           resp.status_code == 200 and len(posts) == 2 and post1_id in p_ids and post2_id in p_ids and post_other_id not in p_ids,
           f"Status: {resp.status_code}, count: {len(posts)}, ids: {p_ids}")

    # 11. GET /api/bookmarks with limit and pagination
    resp = client.get("/api/bookmarks?limit=1")
    data = resp.get_json() or {}
    posts_p1 = data.get("posts", [])
    next_cursor = data.get("next_cursor")
    record(11, "GET /api/bookmarks pagination limit and cursor",
           resp.status_code == 200 and len(posts_p1) == 1 and bool(next_cursor),
           f"Count: {len(posts_p1)}, next_cursor: {next_cursor}")

    # 12. Stale bookmark cleanup: when a post is deleted externally, GET /api/bookmarks cleans orphan bookmark
    ghost_post = db.posts.insert_one({
        "username": u_owner,
        "comment": "Ghost post to be deleted",
        "date": now_iso
    })
    ghost_pid = str(ghost_post.inserted_id)
    # u_other bookmarks ghost_post
    client.set_cookie('mytoken', token_other)
    client.post(f"/api/posts/{ghost_pid}/bookmark")
    # Verify bookmark exists
    b_before = db.bookmarks.find_one({"username": u_other, "post_id": ghost_pid})
    # Remove post directly from db.posts simulating external deletion
    db.posts.delete_one({"_id": ObjectId(ghost_pid)})
    # Fetch bookmarks
    resp = client.get("/api/bookmarks")
    data = resp.get_json() or {}
    p_ids = [p["_id"] for p in data.get("posts", [])]
    b_after = db.bookmarks.find_one({"username": u_other, "post_id": ghost_pid})
    record(12, "Stale bookmarks are pruned automatically and excluded from results",
           resp.status_code == 200 and ghost_pid not in p_ids and b_before is not None and b_after is None,
           f"Ghost in results: {ghost_pid in p_ids}, b_before: {bool(b_before)}, b_after: {bool(b_after)}")

    # 13. Dedicated Web view GET /bookmarks requires auth
    client.delete_cookie('mytoken')
    resp = client.get("/bookmarks")
    record(13, "GET /bookmarks unauthenticated redirects to login",
           resp.status_code in [302, 401],
           f"Status: {resp.status_code}")

    client.set_cookie('mytoken', token_other)
    resp = client.get("/bookmarks")
    record(14, "GET /bookmarks authenticated returns 200 HTML page",
           resp.status_code == 200 and b"Bookmarks" in resp.data,
           f"Status: {resp.status_code}, data length: {len(resp.data)}")

    # -------------------------------------------------------------------------
    # PART B: POST MANAGEMENT TESTS (EDIT & DELETE)
    # -------------------------------------------------------------------------

    # 15. Edit own post succeeds (POST /api/posts/<post_id>/edit)
    client.set_cookie('mytoken', token_owner)
    new_text = "Updated comment by post owner!"
    resp = client.post(f"/api/posts/{post1_id}/edit", data={"comment_give": new_text})
    data = resp.get_json() or {}
    updated_doc = db.posts.find_one({"_id": ObjectId(post1_id)})
    record(15, "Edit own post succeeds and sets is_edited & updated_at",
           resp.status_code == 200 and data.get("comment") == new_text and updated_doc.get("comment") == new_text and updated_doc.get("is_edited") is True and "updated_at" in updated_doc,
           f"Status: {resp.status_code}, is_edited: {updated_doc.get('is_edited')}, updated_at: {updated_doc.get('updated_at')}")

    # 16. Edit post by non-owner returns 403 Forbidden
    client.set_cookie('mytoken', token_other)
    resp = client.post(f"/api/posts/{post1_id}/edit", data={"comment_give": "Malicious edit attempt"})
    record(16, "Edit post by non-owner returns 403 Forbidden",
           resp.status_code == 403,
           f"Status: {resp.status_code}")

    # 17. Edit unauthenticated returns 401 Unauthorized
    client.delete_cookie('mytoken')
    resp = client.post(f"/api/posts/{post1_id}/edit", data={"comment_give": "Unauthenticated edit"})
    record(17, "Edit unauthenticated returns 401 Unauthorized",
           resp.status_code == 401,
           f"Status: {resp.status_code}")

    # 18. Edit with empty content returns 400 Bad Request
    client.set_cookie('mytoken', token_owner)
    resp = client.post(f"/api/posts/{post1_id}/edit", data={"comment_give": "   "})
    record(18, "Edit with empty/whitespace content returns 400",
           resp.status_code == 400,
           f"Status: {resp.status_code}")

    # 19. Edit with content > 5000 characters returns 400
    oversized = "A" * 5001
    resp = client.post(f"/api/posts/{post1_id}/edit", data={"comment_give": oversized})
    record(19, "Edit with oversized content (>5000 chars) returns 400",
           resp.status_code == 400,
           f"Status: {resp.status_code}")

    # 20. Edit nonexistent post returns 404
    resp = client.post(f"/api/posts/{nonexistent_id}/edit", data={"comment_give": "Something"})
    record(20, "Edit nonexistent post returns 404",
           resp.status_code == 404,
           f"Status: {resp.status_code}")

    # 21. Delete post by non-owner returns 403 Forbidden
    client.set_cookie('mytoken', token_other)
    resp = client.post(f"/api/posts/{post1_id}/delete")
    record(21, "Delete post by non-owner returns 403 Forbidden",
           resp.status_code == 403,
           f"Status: {resp.status_code}")

    # 22. Delete unauthenticated returns 401 Unauthorized
    client.delete_cookie('mytoken')
    resp = client.post(f"/api/posts/{post1_id}/delete")
    record(22, "Delete unauthenticated returns 401 Unauthorized",
           resp.status_code == 401,
           f"Status: {resp.status_code}")

    # 23. Delete nonexistent post returns 404
    client.set_cookie('mytoken', token_owner)
    resp = client.post(f"/api/posts/{nonexistent_id}/delete")
    record(23, "Delete nonexistent post returns 404",
           resp.status_code == 404,
           f"Status: {resp.status_code}")

    # Setup relations on post2 to test cascading cleanup
    # Add bookmark, like, star, thumb, and comment on post2_id
    if not db.bookmarks.find_one({"username": u_other, "post_id": post2_id}):
        db.bookmarks.insert_one({"username": u_other, "post_id": post2_id, "created_at": now_iso})
    db.likes.insert_one({"username": u_other, "post_id": post2_id, "type": "heart"})
    db.stars.insert_one({"username": u_other, "post_id": post2_id, "type": "stars"})
    db.thumbs.insert_one({"username": u_other, "post_id": post2_id, "type": "thumbs"})
    db.comments.insert_one({"username": u_other, "post_id": post2_id, "comment": "Nice post", "created_at": now_iso})

    # 24. Delete own post succeeds and cascades to clean up relations
    resp = client.post(f"/api/posts/{post2_id}/delete")
    data = resp.get_json() or {}
    post2_doc = db.posts.find_one({"_id": ObjectId(post2_id)})
    b_rem = db.bookmarks.count_documents({"post_id": post2_id})
    l_rem = db.likes.count_documents({"post_id": post2_id})
    s_rem = db.stars.count_documents({"post_id": post2_id})
    t_rem = db.thumbs.count_documents({"post_id": post2_id})
    c_rem = db.comments.count_documents({"post_id": post2_id})

    cascaded = (b_rem == 0 and l_rem == 0 and s_rem == 0 and t_rem == 0 and c_rem == 0)
    record(24, "Delete own post succeeds and cascades to clean up bookmarks, reactions, and comments",
           resp.status_code == 200 and post2_doc is None and cascaded,
           f"Status: {resp.status_code}, post_deleted: {post2_doc is None}, cascaded: {cascaded}")

    # 25. Metadata enrichment in /get_posts includes is_bookmarked, is_mine, is_edited
    # We still have post1_id (owner is u_owner, is_edited is True, bookmarked by u_other)
    client.set_cookie('mytoken', token_owner)
    resp = client.get("/get_posts?tab=explore")
    data = resp.get_json() or {}
    posts = data.get("posts", [])
    target = next((p for p in posts if p["_id"] == post1_id), None)
    record(25, "Enriched metadata in /get_posts for post owner (is_mine=True, is_edited=True)",
           resp.status_code == 200 and target is not None and target.get("is_mine") is True and target.get("is_edited") is True and target.get("is_bookmarked") is False,
           f"target found: {target is not None}, is_mine: {target.get('is_mine') if target else None}, is_edited: {target.get('is_edited') if target else None}")

    client.set_cookie('mytoken', token_other)
    resp = client.get("/get_posts?tab=explore")
    data = resp.get_json() or {}
    posts = data.get("posts", [])
    target_other = next((p for p in posts if p["_id"] == post1_id), None)
    record(26, "Enriched metadata in /get_posts for other user (is_mine=False, is_bookmarked=True)",
           resp.status_code == 200 and target_other is not None and target_other.get("is_mine") is False and target_other.get("is_bookmarked") is True,
           f"target found: {target_other is not None}, is_mine: {target_other.get('is_mine') if target_other else None}, is_bookmarked: {target_other.get('is_bookmarked') if target_other else None}")

    # Cleanup test data
    db.users.delete_many({"username": {"$in": all_users}})
    db.posts.delete_many({"username": {"$in": all_users}})
    db.bookmarks.delete_many({"username": {"$in": all_users}})
    db.likes.delete_many({"username": {"$in": all_users}})
    db.stars.delete_many({"username": {"$in": all_users}})
    db.thumbs.delete_many({"username": {"$in": all_users}})
    db.comments.delete_many({"username": {"$in": all_users}})

    print("==================================================")
    passed_count = sum(1 for t in test_results if t["status"] == "PASS")
    total_count = len(test_results)
    print(f"RESULTS: {passed_count}/{total_count} tests passed.")
    print("==================================================")

    if passed_count != total_count:
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
