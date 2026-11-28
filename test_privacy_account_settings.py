import os
import sys
import json
import hashlib
import jwt
from datetime import datetime, timezone
from bson import ObjectId

from app import app
from database import (
    db,
    is_private_account,
    is_account_deactivated,
    has_pending_follow_request,
    get_pending_follow_requests_count,
    can_view_user_content,
)

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

test_results = []

def record(test_num, name, passed, details=""):
    status = "PASS" if passed else "FAIL"
    color = GREEN if passed else RED
    print(f"[{color}{status}{RESET}] {test_num:02d}. {name}: {details}")
    test_results.append({
        "test_num": test_num,
        "name": name,
        "status": status,
        "details": details
    })

def make_token(username, token_version=1):
    payload = {
        "id": username,
        "token_version": token_version,
        "exp": datetime.now(timezone.utc).timestamp() + 86400
    }
    return jwt.encode(payload, app.config["SECRET_KEY"], algorithm="HS256")

def run_tests():
    client = app.test_client()

    u_alice = "test_priv_alice"
    u_bob = "test_priv_bob"
    u_charlie = "test_priv_charlie"
    u_david = "test_priv_david"
    all_users = [u_alice, u_bob, u_charlie, u_david]

    def cleanup():
        db.users.delete_many({"username": {"$in": all_users + ["test_priv_newalice"]}})
        db.follows.delete_many({"$or": [
            {"follower": {"$in": all_users + ["test_priv_newalice"]}},
            {"following": {"$in": all_users + ["test_priv_newalice"]}}
        ]})
        db.follow_requests.delete_many({"$or": [
            {"requester": {"$in": all_users + ["test_priv_newalice"]}},
            {"target": {"$in": all_users + ["test_priv_newalice"]}}
        ]})
        db.posts.delete_many({"username": {"$in": all_users + ["test_priv_newalice"]}})
        db.comments.delete_many({"username": {"$in": all_users + ["test_priv_newalice"]}})
        db.notifications.delete_many({"$or": [
            {"recipient": {"$in": all_users + ["test_priv_newalice"]}},
            {"actor": {"$in": all_users + ["test_priv_newalice"]}}
        ]})
        db.bookmarks.delete_many({"username": {"$in": all_users + ["test_priv_newalice"]}})
        db.blocks.delete_many({"$or": [
            {"blocker": {"$in": all_users + ["test_priv_newalice"]}},
            {"blocked": {"$in": all_users + ["test_priv_newalice"]}}
        ]})
        db.mutes.delete_many({"$or": [
            {"muter": {"$in": all_users + ["test_priv_newalice"]}},
            {"muted": {"$in": all_users + ["test_priv_newalice"]}}
        ]})

    cleanup()

    pw_plain = "Password123!"
    pw_hash = hashlib.sha256(pw_plain.encode("utf-8")).hexdigest()

    # Inisialisasi test users
    now_iso = datetime.now(timezone.utc).isoformat()
    db.users.insert_many([
        {
            "username": u_alice,
            "password": pw_hash,
            "profile_name": "Alice Privacy",
            "profile_info": "Alice Bio",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "email": "alice@testprivacy.com",
            "is_private": False,
            "show_follow_counts": True,
            "show_bio": True,
            "default_post_privacy": "public",
            "is_deactivated": False,
            "token_version": 1,
            "created_at": now_iso
        },
        {
            "username": u_bob,
            "password": pw_hash,
            "profile_name": "Bob Requester",
            "profile_info": "Bob Bio",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "email": "bob@testprivacy.com",
            "is_private": False,
            "show_follow_counts": True,
            "show_bio": True,
            "default_post_privacy": "public",
            "is_deactivated": False,
            "token_version": 1,
            "created_at": now_iso
        },
        {
            "username": u_charlie,
            "password": pw_hash,
            "profile_name": "Charlie Stranger",
            "profile_info": "Charlie Bio",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "email": "charlie@testprivacy.com",
            "is_private": False,
            "show_follow_counts": True,
            "show_bio": True,
            "default_post_privacy": "public",
            "is_deactivated": False,
            "token_version": 1,
            "created_at": now_iso
        },
        {
            "username": u_david,
            "password": pw_hash,
            "profile_name": "David Deact",
            "profile_info": "David Bio",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "email": "david@testprivacy.com",
            "is_private": False,
            "show_follow_counts": True,
            "show_bio": True,
            "default_post_privacy": "public",
            "is_deactivated": False,
            "token_version": 1,
            "created_at": now_iso
        }
    ])

    test_num = 1

    # ---------------------------------------------------------
    # 1. Update Profile & Account Information
    # ---------------------------------------------------------
    token_alice = make_token(u_alice, 1)
    client.set_cookie("mytoken", token_alice)

    # 1.1 Invalid email format rejected
    res = client.post("/api/account/profile", data={
        "profile_name": "Alice Updated",
        "profile_info": "New bio",
        "email": "invalid-email-format"
    })
    passed = (res.status_code == 400 and res.get_json()["result"] == "fail")
    record(test_num, "Invalid email format rejected with 400", passed, res.get_json().get("msg", ""))
    test_num += 1

    # 1.2 Duplicate email rejected (409)
    res = client.post("/api/account/profile", data={
        "profile_name": "Alice Updated",
        "profile_info": "New bio",
        "email": "bob@testprivacy.com"
    })
    passed = (res.status_code == 409 and res.get_json()["result"] == "fail")
    record(test_num, "Duplicate email rejected with 409", passed, res.get_json().get("msg", ""))
    test_num += 1

    # 1.3 Successful profile update
    res = client.post("/api/account/profile", data={
        "profile_name": "Alice Wonder",
        "profile_info": "Wonderland Explorer",
        "email": "alice_new@testprivacy.com"
    })
    alice_doc = db.users.find_one({"username": u_alice})
    passed = (res.status_code == 200 and alice_doc["profile_name"] == "Alice Wonder" and alice_doc["email"] == "alice_new@testprivacy.com")
    record(test_num, "Valid profile & email update succeeds", passed, f"Name: {alice_doc.get('profile_name')}, Email: {alice_doc.get('email')}")
    test_num += 1

    # 1.4 Get Account Settings API returns sanitized data (no password)
    res = client.get("/api/account/settings")
    data = res.get_json() or {}
    settings_data = data.get("settings", {})
    passed = (res.status_code == 200 and "password" not in settings_data and settings_data.get("username") == u_alice and settings_data.get("email") == "alice_new@testprivacy.com")
    record(test_num, "GET /api/account/settings returns sanitized settings", passed, f"Has password: {'password' in settings_data}")
    test_num += 1

    # ---------------------------------------------------------
    # 2. Password Change
    # ---------------------------------------------------------
    # 2.1 Wrong old password rejected (400)
    res = client.post("/api/account/change_password", json={
        "old_password": "WrongPassword!",
        "new_password": "NewSecretPassword123!",
        "confirm_password": "NewSecretPassword123!"
    })
    passed = (res.status_code == 400 and res.get_json()["result"] == "fail")
    record(test_num, "Wrong old password rejected with 400", passed, res.get_json().get("msg", ""))
    test_num += 1

    # 2.2 Short new password rejected (400)
    res = client.post("/api/account/change_password", json={
        "old_password": pw_plain,
        "new_password": "123",
        "confirm_password": "123"
    })
    passed = (res.status_code == 400 and res.get_json()["result"] == "fail")
    record(test_num, "Short new password rejected with 400", passed, res.get_json().get("msg", ""))
    test_num += 1

    # 2.3 Mismatched confirmation rejected (400)
    res = client.post("/api/account/change_password", json={
        "old_password": pw_plain,
        "new_password": "NewSecretPassword123!",
        "confirm_password": "DifferentPassword123!"
    })
    passed = (res.status_code == 400 and res.get_json()["result"] == "fail")
    record(test_num, "Mismatched confirmation rejected with 400", passed, res.get_json().get("msg", ""))
    test_num += 1

    # 2.4 Valid password change updates hash and increments token_version
    new_pw = "NewSecretPassword123!"
    old_version = db.users.find_one({"username": u_alice}).get("token_version", 1)
    res = client.post("/api/account/change_password", json={
        "old_password": pw_plain,
        "new_password": new_pw,
        "confirm_password": new_pw
    })
    alice_doc = db.users.find_one({"username": u_alice})
    expected_new_hash = hashlib.sha256(new_pw.encode("utf-8")).hexdigest()
    passed = (
        res.status_code == 200 and
        alice_doc["password"] == expected_new_hash and
        alice_doc.get("token_version") == old_version + 1
    )
    record(test_num, "Valid password change updates hash and increments token_version", passed, f"New version: {alice_doc.get('token_version')}")
    test_num += 1

    # 2.5 Old token with outdated token_version is rejected
    client.set_cookie("mytoken", token_alice)  # token_alice has version 1, user now has version 2
    res = client.get("/api/account/settings")
    passed = (res.status_code == 401)
    record(test_num, "Old session token rejected after password change", passed, f"Status: {res.status_code}")
    test_num += 1

    # Update token_alice to version 2
    token_alice = make_token(u_alice, alice_doc.get("token_version"))
    client.set_cookie("mytoken", token_alice)

    # ---------------------------------------------------------
    # 3. Privacy Settings Toggles
    # ---------------------------------------------------------
    # 3.1 Update privacy settings (set is_private = True)
    res = client.post("/api/account/privacy", json={
        "is_private": True,
        "show_follow_counts": False,
        "show_bio": False,
        "default_post_privacy": "followers"
    })
    alice_doc = db.users.find_one({"username": u_alice})
    passed = (
        res.status_code == 200 and
        alice_doc.get("is_private") is True and
        alice_doc.get("show_follow_counts") is False and
        alice_doc.get("show_bio") is False and
        alice_doc.get("default_post_privacy") == "followers"
    )
    record(test_num, "Update privacy settings (is_private=True, show_follow_counts=False)", passed, f"is_private: {alice_doc.get('is_private')}")
    test_num += 1

    # 3.2 is_private_account helper correctly identifies private status
    passed = is_private_account(u_alice) is True and is_private_account(u_bob) is False
    record(test_num, "is_private_account helper correctly detects private accounts", passed, f"Alice: {is_private_account(u_alice)}, Bob: {is_private_account(u_bob)}")
    test_num += 1

    # ---------------------------------------------------------
    # 4. Follow Request Lifecycle (Private Account)
    # ---------------------------------------------------------
    token_bob = make_token(u_bob, 1)
    client.set_cookie("mytoken", token_bob)

    # 4.1 Bob follows Alice (private account) -> Creates pending follow request, NOT direct follow
    res = client.post("/api/follow", data={"target_username_give": u_alice})
    data = res.get_json() or {}
    is_following_in_db = bool(db.follows.find_one({"follower": u_bob, "following": u_alice}))
    has_request_in_db = bool(db.follow_requests.find_one({"requester": u_bob, "target": u_alice, "status": "pending"}))
    notif_in_db = bool(db.notifications.find_one({"recipient": u_alice, "actor": u_bob, "type": "follow_request"}))
    passed = (
        res.status_code == 200 and
        data.get("is_requested") is True and
        data.get("is_following") is False and
        not is_following_in_db and
        has_request_in_db and
        notif_in_db
    )
    record(test_num, "Following private account creates pending follow request", passed, f"is_requested: {data.get('is_requested')}, in_db: {has_request_in_db}")
    test_num += 1

    # 4.2 Duplicate follow request prevented
    res = client.post("/api/follow", data={"target_username_give": u_alice})
    data = res.get_json() or {}
    passed = (res.status_code in [200, 409] and data.get("is_requested") is True)
    record(test_num, "Duplicate follow request handled cleanly", passed, f"Status: {res.status_code}")
    test_num += 1

    # 4.3 Bob cancels follow request
    res = client.post(f"/api/follow_requests/{u_alice}/cancel")
    has_request_in_db = bool(db.follow_requests.find_one({"requester": u_bob, "target": u_alice, "status": "pending"}))
    passed = (res.status_code == 200 and not has_request_in_db)
    record(test_num, "Requester can cancel pending follow request", passed, f"has_request_in_db: {has_request_in_db}")
    test_num += 1

    # 4.4 Bob re-sends follow request
    res = client.post("/api/follow", data={"target_username_give": u_alice})
    passed = (res.status_code == 200 and res.get_json().get("is_requested") is True)
    record(test_num, "Bob can re-send follow request", passed, "")
    test_num += 1

    # 4.5 Alice lists incoming follow requests
    client.set_cookie("mytoken", token_alice)
    res = client.get("/api/follow_requests")
    data = res.get_json() or {}
    requests = data.get("follow_requests", [])
    passed = (
        res.status_code == 200 and
        len(requests) == 1 and
        requests[0]["requester"] == u_bob
    )
    record(test_num, "Target can list incoming follow requests via API", passed, f"Count: {len(requests)}")
    test_num += 1

    # 4.6 Alice accepts Bob's request -> creates follow relationship and sends notification
    res = client.post(f"/api/follow_requests/{u_bob}/accept")
    has_request_in_db = bool(db.follow_requests.find_one({"requester": u_bob, "target": u_alice, "status": "pending"}))
    is_following_in_db = bool(db.follows.find_one({"follower": u_bob, "following": u_alice}))
    accept_notif = bool(db.notifications.find_one({"recipient": u_bob, "actor": u_alice, "type": "follow_request_accepted"}))
    passed = (
        res.status_code == 200 and
        not has_request_in_db and
        is_following_in_db and
        accept_notif
    )
    record(test_num, "Target accepting request creates follow relation and notification", passed, f"is_following: {is_following_in_db}, accept_notif: {accept_notif}")
    test_num += 1

    # 4.7 Charlie sends follow request to Alice, Alice rejects request
    token_charlie = make_token(u_charlie, 1)
    client.set_cookie("mytoken", token_charlie)
    client.post("/api/follow", data={"target_username_give": u_alice})

    client.set_cookie("mytoken", token_alice)
    res = client.post(f"/api/follow_requests/{u_charlie}/reject")
    has_charlie_req = bool(db.follow_requests.find_one({"requester": u_charlie, "target": u_alice}))
    is_charlie_following = bool(db.follows.find_one({"follower": u_charlie, "following": u_alice}))
    passed = (res.status_code == 200 and not has_charlie_req and not is_charlie_following)
    record(test_num, "Target rejecting request removes request without following", passed, f"has_req: {has_charlie_req}, is_following: {is_charlie_following}")
    test_num += 1

    # 4.8 Alice removes Bob from followers -> revokes follow relation
    res = client.post(f"/api/followers/{u_bob}/remove")
    is_bob_following_now = bool(db.follows.find_one({"follower": u_bob, "following": u_alice}))
    passed = (res.status_code == 200 and not is_bob_following_now)
    record(test_num, "Removing follower revokes follow relationship", passed, f"is_bob_following: {is_bob_following_now}")
    test_num += 1

    # Re-establish Bob as follower of Alice for content tests
    db.follows.insert_one({"follower": u_bob, "following": u_alice, "created_at": datetime.now(timezone.utc).isoformat()})

    # ---------------------------------------------------------
    # 5. Content Privacy Enforcement (Private Account & Post Privacy)
    # ---------------------------------------------------------
    # Create test posts by Alice
    post_pub_id = str(ObjectId())
    post_foll_id = str(ObjectId())
    post_priv_id = str(ObjectId())

    db.posts.insert_many([
        {
            "_id": ObjectId(post_pub_id),
            "username": u_alice,
            "profile_name": "Alice Wonder",
            "content": "Alice Public Post on Private Account",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "privacy": "public",
            "community_id": None
        },
        {
            "_id": ObjectId(post_foll_id),
            "username": u_alice,
            "profile_name": "Alice Wonder",
            "content": "Alice Followers-Only Post",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "privacy": "followers",
            "community_id": None
        },
        {
            "_id": ObjectId(post_priv_id),
            "username": u_alice,
            "profile_name": "Alice Wonder",
            "content": "Alice Private (Self-Only) Post",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "privacy": "private",
            "community_id": None
        }
    ])

    # 5.1 Non-follower Charlie accesses Alice's profile feed -> returns 0 posts and is_private_locked=True
    client.set_cookie("mytoken", token_charlie)
    res = client.get(f"/get_posts?username={u_alice}")
    data = res.get_json() or {}
    posts = data.get("posts", [])
    passed = (res.status_code == 200 and len(posts) == 0 and data.get("is_private_locked") is True)
    record(test_num, "Non-follower cannot view private account's posts in feed", passed, f"Posts: {len(posts)}, locked: {data.get('is_private_locked')}")
    test_num += 1

    # 5.2 Non-follower Charlie accessing followers/following API is blocked (403)
    res = client.get(f"/api/users/{u_alice}/followers")
    passed = (res.status_code == 403 and res.get_json().get("result") == "fail")
    record(test_num, "Non-follower blocked from accessing private user's followers API (403)", passed, f"Status: {res.status_code}")
    test_num += 1

    # 5.3 Non-follower Charlie directly accessing private account's post via API is blocked (403)
    res = client.get(f"/api/posts/{post_pub_id}")
    passed = (res.status_code == 403 and res.get_json().get("result") == "fail")
    record(test_num, "Non-follower blocked from accessing private post directly via API (403)", passed, f"Status: {res.status_code}")
    test_num += 1

    # 5.4 Approved follower Bob can access Alice's profile posts
    client.set_cookie("mytoken", token_bob)
    res = client.get(f"/get_posts?username={u_alice}")
    data = res.get_json() or {}
    bob_visible_posts = data.get("posts", [])
    # Bob should see public and followers posts, but NOT Alice's self-only private post
    post_ids = [p.get("_id") for p in bob_visible_posts]
    passed = (
        res.status_code == 200 and
        not data.get("is_private_locked") and
        post_pub_id in post_ids and
        post_foll_id in post_ids and
        post_priv_id not in post_ids
    )
    record(test_num, "Follower can view public & followers posts, but NOT self-only private posts", passed, f"Visible count: {len(bob_visible_posts)}, has_private: {post_priv_id in post_ids}")
    test_num += 1

    # 5.5 Follower Bob accessing Alice's self-only private post directly is blocked (403)
    res = client.get(f"/api/posts/{post_priv_id}")
    passed = (res.status_code == 403)
    record(test_num, "Follower blocked from accessing author's self-only private post (403)", passed, f"Status: {res.status_code}")
    test_num += 1

    # 5.6 Author Alice can view her own self-only private post directly
    client.set_cookie("mytoken", token_alice)
    res = client.get(f"/api/posts/{post_priv_id}")
    passed = (res.status_code == 200 and res.get_json().get("result") == "success")
    record(test_num, "Author can view their own self-only private post", passed, f"Status: {res.status_code}")
    test_num += 1

    # ---------------------------------------------------------
    # 6. Username Change & Cascading Updates
    # ---------------------------------------------------------
    # 6.1 Invalid username rejected (400)
    res = client.post("/api/account/change_username", json={"new_username": "invalid username!"})
    passed = (res.status_code == 400 and res.get_json().get("result") == "fail")
    record(test_num, "Invalid username format rejected with 400", passed, res.get_json().get("msg", ""))
    test_num += 1

    # 6.2 Reserved username rejected (400)
    res = client.post("/api/account/change_username", json={"new_username": "admin"})
    passed = (res.status_code == 400 and res.get_json().get("result") == "fail")
    record(test_num, "Reserved username rejected with 400", passed, res.get_json().get("msg", ""))
    test_num += 1

    # 6.3 Existing username collision rejected (409)
    res = client.post("/api/account/change_username", json={"new_username": u_bob})
    passed = (res.status_code == 409 and res.get_json().get("result") == "fail")
    record(test_num, "Existing username collision rejected with 409", passed, res.get_json().get("msg", ""))
    test_num += 1

    # 6.4 Valid username change cascades across collections
    u_new_alice = "test_priv_newalice"
    res = client.post("/api/account/change_username", json={"new_username": u_new_alice})
    alice_new_doc = db.users.find_one({"username": u_new_alice})
    alice_old_doc = db.users.find_one({"username": u_alice})
    post_updated = db.posts.find_one({"_id": ObjectId(post_pub_id)})
    follow_updated = db.follows.find_one({"follower": u_bob, "following": u_new_alice})
    passed = (
        res.status_code == 200 and
        alice_new_doc is not None and
        alice_old_doc is None and
        post_updated.get("username") == u_new_alice and
        follow_updated is not None
    )
    record(test_num, "Username change cascades across users, posts, and follows", passed, f"New user doc: {alice_new_doc is not None}, Post author: {post_updated.get('username') if post_updated else None}")
    test_num += 1

    # Update token to new username
    token_new_alice = make_token(u_new_alice, alice_new_doc.get("token_version", 1))
    client.set_cookie("mytoken", token_new_alice)

    # ---------------------------------------------------------
    # 7. Account Deactivation & Reactivation
    # ---------------------------------------------------------
    token_david = make_token(u_david, 1)
    client.set_cookie("mytoken", token_david)

    # Create post by David
    post_david_id = str(ObjectId())
    db.posts.insert_one({
        "_id": ObjectId(post_david_id),
        "username": u_david,
        "profile_name": "David Deact",
        "content": "David public post before deactivation",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "privacy": "public",
        "community_id": None
    })

    # 7.1 Deactivate with wrong password fails (400)
    res = client.post("/api/account/deactivate", json={"password": "WrongPassword"})
    passed = (res.status_code == 400 and res.get_json().get("result") == "fail")
    record(test_num, "Deactivate with wrong password rejected with 400", passed, res.get_json().get("msg", ""))
    test_num += 1

    # 7.2 Deactivate with valid password succeeds
    res = client.post("/api/account/deactivate", json={"password": pw_plain})
    david_doc = db.users.find_one({"username": u_david})
    passed = (
        res.status_code == 200 and
        david_doc.get("is_deactivated") is True and
        is_account_deactivated(u_david) is True
    )
    record(test_num, "Deactivate with valid password sets is_deactivated=True", passed, f"is_deactivated: {david_doc.get('is_deactivated')}")
    test_num += 1

    # 7.3 Deactivated user cannot perform authenticated actions (token rejected)
    client.set_cookie("mytoken", token_david)
    res = client.get("/api/account/settings")
    passed = (res.status_code == 401)
    record(test_num, "Deactivated user token rejected with 401", passed, f"Status: {res.status_code}")
    test_num += 1

    # 7.4 Deactivated user's post hidden from explore feed
    client.set_cookie("mytoken", token_charlie)
    res = client.get("/get_posts")
    data = res.get_json() or {}
    explore_post_ids = [p.get("_id") for p in data.get("posts", [])]
    passed = (post_david_id not in explore_post_ids)
    record(test_num, "Deactivated user's posts excluded from explore/timeline feeds", passed, f"In explore: {post_david_id in explore_post_ids}")
    test_num += 1

    # 7.5 Reactivate via sign_in endpoint restores account
    res = client.post("/sign_in", data={
        "username_give": u_david,
        "password_give": pw_plain
    })
    data = res.get_json() or {}
    david_doc = db.users.find_one({"username": u_david})
    passed = (
        res.status_code == 200 and
        data.get("result") == "success" and
        data.get("was_reactivated") is True and
        david_doc.get("is_deactivated") is False
    )
    record(test_num, "Sign in automatically reactivates deactivated account", passed, f"was_reactivated: {data.get('was_reactivated')}, db: {david_doc.get('is_deactivated')}")
    test_num += 1

    # ---------------------------------------------------------
    # 8. Permanent Account Deletion
    # ---------------------------------------------------------
    token_david = make_token(u_david, david_doc.get("token_version", 1))
    client.set_cookie("mytoken", token_david)

    # 8.1 Delete with wrong password fails (400)
    res = client.post("/api/account/delete", json={"password": "WrongPassword"})
    passed = (res.status_code == 400 and res.get_json().get("result") == "fail")
    record(test_num, "Delete account with wrong password rejected with 400", passed, res.get_json().get("msg", ""))
    test_num += 1

    # 8.2 Delete with valid password cascades deletion across all user data
    res = client.post("/api/account/delete", json={"password": pw_plain})
    david_in_users = db.users.find_one({"username": u_david})
    david_in_posts = db.posts.find_one({"username": u_david})
    passed = (
        res.status_code == 200 and
        david_in_users is None and
        david_in_posts is None
    )
    record(test_num, "Delete account cascades deletion of user document and posts", passed, f"User in DB: {david_in_users is not None}, Post in DB: {david_in_posts is not None}")
    test_num += 1

    # ---------------------------------------------------------
    # 9. Multi-device Session Invalidation (Logout All Sessions)
    # ---------------------------------------------------------
    client.set_cookie("mytoken", token_bob)
    cur_bob_ver = db.users.find_one({"username": u_bob}).get("token_version", 1)
    res = client.post("/api/account/logout_all_sessions")
    new_bob_ver = db.users.find_one({"username": u_bob}).get("token_version", 1)
    passed = (res.status_code == 200 and new_bob_ver == cur_bob_ver + 1)
    record(test_num, "Logout all sessions increments token_version", passed, f"Old: {cur_bob_ver}, New: {new_bob_ver}")
    test_num += 1

    # Previous token is now invalid
    client.set_cookie("mytoken", token_bob)
    res = client.get("/api/account/settings")
    passed = (res.status_code == 401)
    record(test_num, "Previous token rejected with 401 after logout_all_sessions", passed, f"Status: {res.status_code}")
    test_num += 1

    # Clean up test data
    cleanup()

    print("\n" + "=" * 50)
    total = len(test_results)
    passed_count = sum(1 for t in test_results if t["status"] == "PASS")
    failed_count = total - passed_count
    print(f"TOTAL TESTS: {total} | PASSED: {passed_count} | FAILED: {failed_count}")
    print("=" * 50)

    return failed_count == 0

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
