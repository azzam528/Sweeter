import os
import sys
import json
import jwt
from datetime import datetime, timezone, timedelta
from bson import ObjectId

from app import app
from database import db, is_reposted, count_reposts
from notifications import create_notification, get_unread_count

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

def make_token(secret, username):
    payload = {
        'id': username,
        'exp': datetime.now(timezone.utc) + timedelta(hours=2)
    }
    return jwt.encode(payload, secret, algorithm='HS256')

def run_tests():
    client = app.test_client()
    secret = app.config["SECRET_KEY"]

    u_author = "test_rq_author"
    u_reposter = "test_rq_reposter"
    u_follower = "test_rq_follower"
    u_other = "test_rq_other"

    all_users = [u_author, u_reposter, u_follower, u_other]

    # Cleanup
    db.users.delete_many({"username": {"$in": all_users}})
    db.posts.delete_many({"username": {"$in": all_users}})
    db.follows.delete_many({"$or": [{"follower": {"$in": all_users}}, {"following": {"$in": all_users}}]})
    db.notifications.delete_many({"$or": [{"recipient": {"$in": all_users}}, {"actor": {"$in": all_users}}]})

    # Setup users
    for u in all_users:
        db.users.insert_one({
            "username": u,
            "password": "hashed_pw",
            "profile_name": f"User {u}",
            "profile_pic_real": "profile_pics/profile_placeholder.png"
        })

    # Follow relations: u_follower follows u_reposter
    db.follows.insert_one({
        "follower": u_follower,
        "following": u_reposter,
        "created_at": datetime.now(timezone.utc).isoformat()
    })

    t_author = make_token(secret, u_author)
    t_reposter = make_token(secret, u_reposter)
    t_follower = make_token(secret, u_follower)

    # Insert an original post by u_author
    orig_post_doc = {
        "username": u_author,
        "profile_name": f"User {u_author}",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "Original tweet content for repost and quote testing",
        "date": datetime.now(timezone.utc).isoformat(),
        "post_type": "original"
    }
    res = db.posts.insert_one(orig_post_doc)
    orig_pid = str(res.inserted_id)

    print("=" * 60)
    print("RUNNING REPOST & QUOTE POST TEST SUITE")
    print("=" * 60)

    # 1. Repost requires authentication (401)
    client.delete_cookie('mytoken')
    resp = client.post(f"/api/posts/{orig_pid}/repost")
    record(1, "Repost unauthenticated returns 401", resp.status_code == 401, f"Status: {resp.status_code}")

    # 2. Repost invalid ID returns 400
    client.set_cookie('mytoken', t_reposter)
    resp = client.post("/api/posts/invalid_hex_id/repost")
    record(2, "Repost invalid ID returns 400", resp.status_code == 400, f"Status: {resp.status_code}")

    # 3. Repost nonexistent post returns 404
    fake_id = str(ObjectId())
    resp = client.post(f"/api/posts/{fake_id}/repost")
    record(3, "Repost nonexistent post returns 404", resp.status_code == 404, f"Status: {resp.status_code}")

    # 4. Self-repost prohibited (400)
    client.set_cookie('mytoken', t_author)
    resp = client.post(f"/api/posts/{orig_pid}/repost")
    record(4, "Self-repost prohibited returns 400", resp.status_code == 400, f"Status: {resp.status_code}")

    # 5. Repost post succeeds (200)
    client.set_cookie('mytoken', t_reposter)
    resp = client.post(f"/api/posts/{orig_pid}/repost")
    data = json.loads(resp.data)
    record(5, "Repost post succeeds with 200", resp.status_code == 200 and data.get("is_reposted") is True, f"Data: {data}")

    # 6. Repost count is incremented (1)
    cnt = count_reposts(orig_pid)
    is_rep = is_reposted(u_reposter, orig_pid)
    record(6, "Repost count is 1 and is_reposted is True", cnt == 1 and is_rep is True, f"Count: {cnt}, is_rep: {is_rep}")

    # 7. Duplicate repost prohibited (409 Conflict)
    resp = client.post(f"/api/posts/{orig_pid}/repost")
    record(7, "Duplicate repost returns 409 Conflict", resp.status_code == 409, f"Status: {resp.status_code}")

    # 8. Notification created for original post author
    notif = db.notifications.find_one({
        "recipient": u_author,
        "actor": u_reposter,
        "type": "repost",
        "post_id": orig_pid
    })
    record(8, "Repost notification sent to original author", notif is not None, f"Notif found: {bool(notif)}")

    # 9. Repost appears in personalized feed of follower
    client.set_cookie('mytoken', t_follower)
    resp = client.get("/get_posts?tab=following")
    feed_data = json.loads(resp.data)
    posts = feed_data.get("posts", [])
    repost_card = next((p for p in posts if p.get("post_type") == "repost" and p.get("username") == u_reposter), None)
    has_orig = repost_card and repost_card.get("original_post") and repost_card["original_post"]["_id"] == orig_pid
    record(9, "Repost appears in follower's timeline with enriched original_post", bool(has_orig), f"Found repost: {bool(repost_card)}")

    # 10. Repost appears on reposter's profile feed
    client.set_cookie('mytoken', t_follower)
    resp = client.get(f"/get_posts?username_give={u_reposter}")
    prof_data = json.loads(resp.data)
    prof_posts = prof_data.get("posts", [])
    prof_repost = next((p for p in prof_posts if p.get("post_type") == "repost"), None)
    record(10, "Repost appears on reposter profile feed", prof_repost is not None, f"Found on profile: {bool(prof_repost)}")

    # 11. Undo repost succeeds (200)
    client.set_cookie('mytoken', t_reposter)
    resp = client.post(f"/api/posts/{orig_pid}/unrepost")
    undo_data = json.loads(resp.data)
    record(11, "Undo repost succeeds with 200", resp.status_code == 200 and undo_data.get("is_reposted") is False, f"Data: {undo_data}")

    # 12. Repost count decremented to 0
    cnt_after = count_reposts(orig_pid)
    is_rep_after = is_reposted(u_reposter, orig_pid)
    record(12, "Repost count is 0 after undo", cnt_after == 0 and is_rep_after is False, f"Count: {cnt_after}, is_rep: {is_rep_after}")

    # 13. Undo repost is idempotent
    resp = client.post(f"/api/posts/{orig_pid}/unrepost")
    record(13, "Undo repost is safe & idempotent", resp.status_code == 200, f"Status: {resp.status_code}")

    # 14. Quote post requires authentication (401)
    client.delete_cookie('mytoken')
    resp = client.post(f"/api/posts/{orig_pid}/quote", json={"comment": "Nice thought!"})
    record(14, "Quote post unauthenticated returns 401", resp.status_code == 401, f"Status: {resp.status_code}")

    # 15. Quote post invalid ID returns 400
    client.set_cookie('mytoken', t_reposter)
    resp = client.post("/api/posts/not_a_valid_id/quote", json={"comment": "Quote text"})
    record(15, "Quote invalid ID returns 400", resp.status_code == 400, f"Status: {resp.status_code}")

    # 16. Quote post nonexistent post returns 404
    resp = client.post(f"/api/posts/{fake_id}/quote", json={"comment": "Quote text"})
    record(16, "Quote nonexistent post returns 404", resp.status_code == 404, f"Status: {resp.status_code}")

    # 17. Quote post empty content returns 400
    resp = client.post(f"/api/posts/{orig_pid}/quote", json={"comment": "   "})
    record(17, "Quote empty comment returns 400", resp.status_code == 400, f"Status: {resp.status_code}")

    # 18. Quote post oversized content returns 400
    resp = client.post(f"/api/posts/{orig_pid}/quote", json={"comment": "A" * 5001})
    record(18, "Quote oversized comment returns 400", resp.status_code == 400, f"Status: {resp.status_code}")

    # 19. Quote post succeeds (201)
    resp = client.post(f"/api/posts/{orig_pid}/quote", json={"comment": "This is my insightful quote comment!"})
    quote_data = json.loads(resp.data)
    quote_id = quote_data.get("post_id")
    record(19, "Quote post succeeds with 201", resp.status_code == 201 and quote_id is not None, f"Quote ID: {quote_id}")

    # 20. Quote post notification sent to original author
    quote_notif = db.notifications.find_one({
        "recipient": u_author,
        "actor": u_reposter,
        "type": "quote",
        "post_id": orig_pid
    })
    record(20, "Quote notification created for original author", quote_notif is not None, f"Notif found: {bool(quote_notif)}")

    # 21. Quote post appears in follower feed with embedded original post
    client.set_cookie('mytoken', t_follower)
    resp = client.get("/get_posts?tab=following")
    feed_data = json.loads(resp.data)
    q_card = next((p for p in feed_data.get("posts", []) if p.get("_id") == quote_id), None)
    has_orig_embedded = q_card and q_card.get("original_post") and q_card["original_post"]["_id"] == orig_pid
    record(21, "Quote post appears in follower feed with embedded original", bool(has_orig_embedded), f"Found quote: {bool(q_card)}")

    # 22. Quote post appears on quoter profile
    resp = client.get(f"/get_posts?username_give={u_reposter}")
    prof_data = json.loads(resp.data)
    prof_q = next((p for p in prof_data.get("posts", []) if p.get("_id") == quote_id), None)
    record(22, "Quote post appears on quoter profile feed", prof_q is not None, f"Found on profile: {bool(prof_q)}")

    # 23. Cascading deletion: Create a repost again, then delete original post
    client.set_cookie('mytoken', t_reposter)
    resp = client.post(f"/api/posts/{orig_pid}/repost")
    record(23, "Re-created repost before deletion test", resp.status_code == 200, f"Status: {resp.status_code}")

    # Delete original post by u_author
    client.set_cookie('mytoken', t_author)
    resp = client.post(f"/api/posts/{orig_pid}/delete")
    record(24, "Author deletes original post", resp.status_code == 200, f"Status: {resp.status_code}")

    # 25. Cascading deletion deletes pure reposts
    repost_after_del = db.posts.find_one({"original_post_id": orig_pid, "post_type": "repost"})
    record(25, "Pure repost was cascade-deleted", repost_after_del is None, f"Repost exists: {bool(repost_after_del)}")

    # 26. Quote post is preserved, and enriched with is_deleted=True
    client.set_cookie('mytoken', t_follower)
    resp = client.get("/get_posts?tab=following")
    feed_after = json.loads(resp.data)
    q_card_after = next((p for p in feed_after.get("posts", []) if p.get("_id") == quote_id), None)
    is_orig_marked_deleted = q_card_after and q_card_after.get("original_post") and q_card_after["original_post"].get("is_deleted") is True
    record(26, "Quote post preserved and original marked is_deleted=True", bool(is_orig_marked_deleted), f"Quote card: {bool(q_card_after)}")

    print("=" * 60)
    passed_count = sum(1 for t in test_results if t["status"] == "PASS")
    total_count = len(test_results)
    print(f"RESULTS: {passed_count}/{total_count} tests passed.")
    print("=" * 60)

    # Cleanup test data
    db.users.delete_many({"username": {"$in": all_users}})
    db.posts.delete_many({"username": {"$in": all_users}})
    db.posts.delete_many({"_id": ObjectId(quote_id)})
    db.follows.delete_many({"$or": [{"follower": {"$in": all_users}}, {"following": {"$in": all_users}}]})
    db.notifications.delete_many({"$or": [{"recipient": {"$in": all_users}}, {"actor": {"$in": all_users}}]})

    if passed_count != total_count:
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
