import os
import sys
import json
import jwt
from datetime import datetime, timedelta, timezone
from bson import ObjectId

from app import app
from database import db
from notifications import (
    create_notification,
    get_unread_count,
    get_user_notifications,
    mark_notification_as_read,
    mark_all_notifications_as_read,
    cleanup_notifications_for_post,
)

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

    u_alice = "test_notif_alice"
    u_bob = "test_notif_bob"
    u_charlie = "test_notif_charlie"
    all_users = [u_alice, u_bob, u_charlie]

    # Cleanup test data
    db.users.delete_many({"username": {"$in": all_users}})
    db.posts.delete_many({"username": {"$in": all_users}})
    db.comments.delete_many({"username": {"$in": all_users}})
    db.likes.delete_many({"username": {"$in": all_users}})
    db.follows.delete_many({"$or": [
        {"follower": {"$in": all_users}},
        {"following": {"$in": all_users}}
    ]})
    db.notifications.delete_many({"$or": [
        {"recipient": {"$in": all_users}},
        {"actor": {"$in": all_users}}
    ]})

    # Setup test users
    db.users.insert_many([
        {
            "username": u_alice,
            "password": "hashed_alice",
            "profile_name": "Alice User",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Alice bio"
        },
        {
            "username": u_bob,
            "password": "hashed_bob",
            "profile_name": "Bob User",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Bob bio"
        },
        {
            "username": u_charlie,
            "password": "hashed_charlie",
            "profile_name": "Charlie User",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Charlie bio"
        }
    ])

    def create_token(username):
        payload = {
            "id": username,
            "exp": datetime.now(timezone.utc) + timedelta(hours=2)
        }
        return jwt.encode(payload, secret, algorithm="HS256")

    token_alice = create_token(u_alice)
    token_bob = create_token(u_bob)
    token_charlie = create_token(u_charlie)

    now_iso = datetime.now(timezone.utc).isoformat()

    # Create Alice's post
    res_p1 = db.posts.insert_one({
        "username": u_alice,
        "profile_name": "Alice User",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "Alice first post for notifications testing",
        "date": now_iso
    })
    alice_post_id = str(res_p1.inserted_id)

    print("==================================================")
    print("RUNNING SWEETER V2: NOTIFICATION SYSTEM TESTS")
    print("==================================================")

    # -------------------------------------------------------------------------
    # 1. FOLLOW NOTIFICATION
    # -------------------------------------------------------------------------
    client.set_cookie('mytoken', token_bob)
    resp = client.post("/api/follow", data={"target_username_give": u_alice})
    notif_follow = db.notifications.find_one({"recipient": u_alice, "actor": u_bob, "type": "follow"})
    record(1, "Follow triggers notification to target user",
           resp.status_code == 200 and notif_follow is not None and notif_follow.get("is_read") is False,
           f"Status: {resp.status_code}, notif found: {notif_follow is not None}")

    # -------------------------------------------------------------------------
    # 2. DUPLICATE FOLLOW NOTIFICATION PREVENTION
    # -------------------------------------------------------------------------
    dup_id = create_notification(recipient=u_alice, actor=u_bob, notif_type="follow")
    unread_follows = db.notifications.count_documents({"recipient": u_alice, "actor": u_bob, "type": "follow", "is_read": False})
    record(2, "Duplicate follow notification prevented when unread",
           unread_follows == 1,
           f"Unread follows count: {unread_follows}")

    # -------------------------------------------------------------------------
    # 3. REACTION (LIKE) NOTIFICATION
    # -------------------------------------------------------------------------
    client.set_cookie('mytoken', token_bob)
    resp = client.post("/update_like", data={
        "post_id_give": alice_post_id,
        "type_give": "heart",
        "action_give": "like"
    })
    notif_like = db.notifications.find_one({"recipient": u_alice, "actor": u_bob, "type": "reaction", "post_id": alice_post_id})
    record(3, "Post reaction triggers notification to post author",
           resp.status_code == 200 and notif_like is not None and notif_like.get("is_read") is False,
           f"Status: {resp.status_code}, notif found: {notif_like is not None}")

    # -------------------------------------------------------------------------
    # 4. SELF-REACTION DOES NOT TRIGGER NOTIFICATION
    # -------------------------------------------------------------------------
    client.set_cookie('mytoken', token_alice)
    client.post("/update_like", data={
        "post_id_give": alice_post_id,
        "type_give": "heart",
        "action_give": "like"
    })
    self_notif_like = db.notifications.find_one({"recipient": u_alice, "actor": u_alice})
    record(4, "Self-reaction does not generate notification",
           self_notif_like is None,
           f"Self notification found: {self_notif_like is not None}")

    # -------------------------------------------------------------------------
    # 5. COMMENT NOTIFICATION
    # -------------------------------------------------------------------------
    client.set_cookie('mytoken', token_bob)
    resp = client.post("/create_comment", data={
        "post_id_give": alice_post_id,
        "content_give": "Bob comments on Alice post"
    })
    data_comm = resp.get_json() or {}
    bob_comment_id = (data_comm.get("comment") or {}).get("_id")
    notif_comment = db.notifications.find_one({"recipient": u_alice, "actor": u_bob, "type": "comment", "comment_id": bob_comment_id})
    record(5, "Comment triggers notification to post author",
           resp.status_code == 201 and notif_comment is not None,
           f"Status: {resp.status_code}, notif found: {notif_comment is not None}")

    # -------------------------------------------------------------------------
    # 6. SELF-COMMENT DOES NOT TRIGGER NOTIFICATION
    # -------------------------------------------------------------------------
    client.set_cookie('mytoken', token_alice)
    resp = client.post("/create_comment", data={
        "post_id_give": alice_post_id,
        "content_give": "Alice comments on her own post"
    })
    data_self = resp.get_json() or {}
    alice_comm_id = (data_self.get("comment") or {}).get("_id")
    self_notif_comm = db.notifications.find_one({"recipient": u_alice, "actor": u_alice, "comment_id": alice_comm_id})
    record(6, "Self-comment does not generate notification",
           self_notif_comm is None,
           f"Self comment notification found: {self_notif_comm is not None}")

    # -------------------------------------------------------------------------
    # 7. REPLY NOTIFICATION
    # -------------------------------------------------------------------------
    # Charlie replies to Bob's comment on Alice's post
    client.set_cookie('mytoken', token_charlie)
    resp = client.post("/create_comment", data={
        "post_id_give": alice_post_id,
        "parent_comment_id_give": bob_comment_id,
        "content_give": "Charlie replies to Bob"
    })
    data_reply = resp.get_json() or {}
    charlie_reply_id = (data_reply.get("comment") or {}).get("_id")

    notif_reply_bob = db.notifications.find_one({"recipient": u_bob, "actor": u_charlie, "type": "reply", "comment_id": charlie_reply_id})
    notif_comm_alice = db.notifications.find_one({"recipient": u_alice, "actor": u_charlie, "type": "comment", "comment_id": charlie_reply_id})
    record(7, "Reply triggers reply notification to parent author and comment to post author",
           resp.status_code == 201 and notif_reply_bob is not None and notif_comm_alice is not None,
           f"Reply to Bob: {notif_reply_bob is not None}, Comment to Alice: {notif_comm_alice is not None}")

    # -------------------------------------------------------------------------
    # 8. SELF-REPLY DOES NOT TRIGGER REPLY NOTIFICATION
    # -------------------------------------------------------------------------
    # Bob replies to his own comment
    client.set_cookie('mytoken', token_bob)
    resp = client.post("/create_comment", data={
        "post_id_give": alice_post_id,
        "parent_comment_id_give": bob_comment_id,
        "content_give": "Bob replies to his own comment"
    })
    data_self_reply = resp.get_json() or {}
    bob_self_reply_id = (data_self_reply.get("comment") or {}).get("_id")
    self_notif_reply = db.notifications.find_one({"recipient": u_bob, "actor": u_bob, "comment_id": bob_self_reply_id})
    record(8, "Self-reply does not generate reply notification to oneself",
           self_notif_reply is None,
           f"Self reply notif found: {self_notif_reply is not None}")

    # -------------------------------------------------------------------------
    # 9. UNREAD COUNT API
    # -------------------------------------------------------------------------
    client.set_cookie('mytoken', token_alice)
    resp = client.get("/api/notifications/unread_count")
    data_count = resp.get_json() or {}
    actual_unread_alice = db.notifications.count_documents({"recipient": u_alice, "is_read": False})
    record(9, "GET /api/notifications/unread_count returns accurate unread count",
           resp.status_code == 200 and data_count.get("unread_count") == actual_unread_alice and actual_unread_alice >= 3,
           f"Status: {resp.status_code}, unread_count: {data_count.get('unread_count')}, actual: {actual_unread_alice}")

    # -------------------------------------------------------------------------
    # 10. NOTIFICATION LISTING & DATA ENRICHMENT
    # -------------------------------------------------------------------------
    resp = client.get("/api/notifications")
    data_list = resp.get_json() or {}
    notifs = data_list.get("notifications", [])
    first_notif = notifs[0] if notifs else {}
    actor_info = first_notif.get("actor_info", {})
    record(10, "GET /api/notifications returns enriched notification documents",
           resp.status_code == 200 and len(notifs) >= 3 and "profile_name" in actor_info and "profile_pic_real" in actor_info and "is_read" in first_notif,
           f"Status: {resp.status_code}, total: {len(notifs)}, actor_info: {actor_info}")

    # -------------------------------------------------------------------------
    # 11. KEYSET CURSOR PAGINATION
    # -------------------------------------------------------------------------
    resp_page1 = client.get("/api/notifications?limit=2")
    data_p1 = resp_page1.get_json() or {}
    notifs_p1 = data_p1.get("notifications", [])
    next_cursor = data_p1.get("next_cursor")

    resp_page2 = client.get(f"/api/notifications?limit=2&cursor={next_cursor}")
    data_p2 = resp_page2.get_json() or {}
    notifs_p2 = data_p2.get("notifications", [])

    overlap = set(n["_id"] for n in notifs_p1).intersection(set(n["_id"] for n in notifs_p2))
    record(11, "Keyset cursor pagination slices results without overlaps",
           resp_page1.status_code == 200 and resp_page2.status_code == 200 and len(notifs_p1) == 2 and len(notifs_p2) >= 1 and len(overlap) == 0,
           f"Page1: {len(notifs_p1)}, Page2: {len(notifs_p2)}, Overlap count: {len(overlap)}")

    # -------------------------------------------------------------------------
    # 12. MARK SINGLE NOTIFICATION AS READ
    # -------------------------------------------------------------------------
    target_nid = notifs[0]["_id"]
    resp_read = client.post(f"/api/notifications/{target_nid}/read")
    data_read = resp_read.get_json() or {}
    updated_doc = db.notifications.find_one({"_id": ObjectId(target_nid)})
    record(12, "Mark single notification as read updates is_read flag",
           resp_read.status_code == 200 and updated_doc.get("is_read") is True and data_read.get("unread_count") == (actual_unread_alice - 1),
           f"Status: {resp_read.status_code}, is_read: {updated_doc.get('is_read')}, unread_count: {data_read.get('unread_count')}")

    # -------------------------------------------------------------------------
    # 13. SECURITY: CANNOT MARK ANOTHER USER'S NOTIFICATION AS READ (403)
    # -------------------------------------------------------------------------
    # Bob attempts to mark Alice's notification as read
    client.set_cookie('mytoken', token_bob)
    resp_hack = client.post(f"/api/notifications/{target_nid}/read")
    record(13, "Cross-user notification modification blocked with 403 Forbidden",
           resp_hack.status_code == 403,
           f"Status: {resp_hack.status_code}")

    # -------------------------------------------------------------------------
    # 14. SECURITY: CANNOT VIEW ANOTHER USER'S NOTIFICATIONS (ISOLATION)
    # -------------------------------------------------------------------------
    # Bob views notifications - should only see his own
    resp_bob = client.get("/api/notifications")
    data_bob = resp_bob.get_json() or {}
    bob_notifs = data_bob.get("notifications", [])
    bob_recipients = set(n.get("recipient") for n in bob_notifs)
    record(14, "User can only view their own notifications",
           resp_bob.status_code == 200 and (len(bob_recipients) == 0 or bob_recipients == {u_bob}),
           f"Recipients in Bob's feed: {bob_recipients}")

    # -------------------------------------------------------------------------
    # 15. ERROR HANDLING: INVALID FORMAT & NONEXISTENT NOTIFICATION ID
    # -------------------------------------------------------------------------
    client.set_cookie('mytoken', token_alice)
    resp_inv = client.post("/api/notifications/invalid-format-id/read")
    resp_404 = client.post(f"/api/notifications/{ObjectId()}/read")
    record(15, "Invalid format returns 400 and nonexistent returns 404",
           resp_inv.status_code == 400 and resp_404.status_code == 404,
           f"Invalid format status: {resp_inv.status_code}, Nonexistent status: {resp_404.status_code}")

    # -------------------------------------------------------------------------
    # 16. MARK ALL NOTIFICATIONS AS READ
    # -------------------------------------------------------------------------
    resp_all = client.post("/api/notifications/read_all")
    data_all = resp_all.get_json() or {}
    remaining_unread_alice = db.notifications.count_documents({"recipient": u_alice, "is_read": False})
    record(16, "Mark all notifications as read resets unread count to 0",
           resp_all.status_code == 200 and remaining_unread_alice == 0 and data_all.get("unread_count") == 0,
           f"Status: {resp_all.status_code}, remaining unread: {remaining_unread_alice}")

    # -------------------------------------------------------------------------
    # 17. CASCADING CLEANUP ON POST DELETION
    # -------------------------------------------------------------------------
    # Post Alice has notifications referencing alice_post_id
    notif_post_count_before = db.notifications.count_documents({"post_id": alice_post_id})
    # Alice deletes her post
    client.set_cookie('mytoken', token_alice)
    resp_del = client.post(f"/api/posts/{alice_post_id}/delete")
    notif_post_count_after = db.notifications.count_documents({"post_id": alice_post_id})
    record(17, "Deleting post cascades cleanup to associated notifications",
           resp_del.status_code == 200 and notif_post_count_before > 0 and notif_post_count_after == 0,
           f"Notif before: {notif_post_count_before}, after: {notif_post_count_after}")

    # -------------------------------------------------------------------------
    # 18. WEB ROUTE: GET /notifications AUTHENTICATION & RENDERING
    # -------------------------------------------------------------------------
    client.delete_cookie('mytoken')
    resp_unauth_page = client.get("/notifications")
    record(18, "Unauthenticated access to /notifications redirects to login",
           resp_unauth_page.status_code in [302, 401],
           f"Status: {resp_unauth_page.status_code}")

    client.set_cookie('mytoken', token_alice)
    resp_auth_page = client.get("/notifications")
    record(19, "Authenticated access to /notifications renders 200 HTML page",
           resp_auth_page.status_code == 200 and b"Notifications" in resp_auth_page.data,
           f"Status: {resp_auth_page.status_code}, data length: {len(resp_auth_page.data)}")

    # Cleanup test data
    db.users.delete_many({"username": {"$in": all_users}})
    db.posts.delete_many({"username": {"$in": all_users}})
    db.comments.delete_many({"username": {"$in": all_users}})
    db.likes.delete_many({"username": {"$in": all_users}})
    db.follows.delete_many({"$or": [
        {"follower": {"$in": all_users}},
        {"following": {"$in": all_users}}
    ]})
    db.notifications.delete_many({"$or": [
        {"recipient": {"$in": all_users}},
        {"actor": {"$in": all_users}}
    ]})

    print("==================================================")
    passed_count = sum(1 for t in test_results if t["status"] == "PASS")
    total_count = len(test_results)
    print(f"RESULTS: {passed_count}/{total_count} tests passed.")
    print("==================================================")

    if passed_count != total_count:
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
