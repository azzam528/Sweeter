import os
import sys
import json
import jwt
from datetime import datetime, timedelta, timezone
from pymongo.errors import DuplicateKeyError

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

    u_alice = "test_flw_alice"
    u_bob = "test_flw_bob"
    u_charlie = "test_flw_charlie"

    # Cleanup any old test records
    db.users.delete_many({"username": {"$in": [u_alice, u_bob, u_charlie]}})
    db.follows.delete_many({"$or": [
        {"follower": {"$in": [u_alice, u_bob, u_charlie]}},
        {"following": {"$in": [u_alice, u_bob, u_charlie]}}
    ]})

    # Setup test users
    db.users.insert_many([
        {
            "username": u_alice,
            "password": "hashed_pw_alice",
            "profile_name": "Alice Wonderland",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Bio Alice"
        },
        {
            "username": u_bob,
            "password": "hashed_pw_bob",
            "profile_name": "Bob Builder",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Bio Bob"
        },
        {
            "username": u_charlie,
            "password": "hashed_pw_charlie",
            "profile_name": "Charlie Chaplin",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Bio Charlie"
        }
    ])

    token_alice = jwt.encode({"id": u_alice, "exp": datetime.now(timezone.utc) + timedelta(hours=2)}, secret, algorithm="HS256")
    token_bob = jwt.encode({"id": u_bob, "exp": datetime.now(timezone.utc) + timedelta(hours=2)}, secret, algorithm="HS256")

    try:
        # ------------------------------------------------------------------
        # 1. Unauthenticated Follow Request -> 401
        # ------------------------------------------------------------------
        res = client.post('/api/follow', data={'target_username_give': u_bob})
        pass1 = (res.status_code == 401 and res.get_json().get('result') == 'fail')
        record(1, "Unauthenticated Follow Request", pass1, f"Status: {res.status_code}")

        # ------------------------------------------------------------------
        # 2. Unauthenticated Unfollow Request -> 401
        # ------------------------------------------------------------------
        res = client.post('/api/unfollow', data={'target_username_give': u_bob})
        pass2 = (res.status_code == 401 and res.get_json().get('result') == 'fail')
        record(2, "Unauthenticated Unfollow Request", pass2, f"Status: {res.status_code}")

        # ------------------------------------------------------------------
        # 3. Follow Self Prevention -> 400
        # ------------------------------------------------------------------
        client.set_cookie('mytoken', token_alice)
        res = client.post('/api/follow', data={'target_username_give': u_alice})
        data = res.get_json()
        pass3 = (res.status_code == 400 and 'cannot follow yourself' in data.get('msg', '').lower())
        record(3, "Self-Follow Prevention", pass3, f"Status: {res.status_code}, Msg: {data.get('msg')}")

        # ------------------------------------------------------------------
        # 4. Follow Nonexistent User -> 404
        # ------------------------------------------------------------------
        res = client.post('/api/follow', data={'target_username_give': 'phantom_nonexistent_user_xyz'})
        pass4 = (res.status_code == 404 and res.get_json().get('result') == 'fail')
        record(4, "Follow Nonexistent User", pass4, f"Status: {res.status_code}")

        # ------------------------------------------------------------------
        # 5. Invalid / Empty Target Username -> 400
        # ------------------------------------------------------------------
        res = client.post('/api/follow', data={'target_username_give': '   '})
        pass5 = (res.status_code == 400 and res.get_json().get('result') == 'fail')
        record(5, "Invalid Target Username", pass5, f"Status: {res.status_code}")

        # ------------------------------------------------------------------
        # 6. Successful Follow (Alice follows Bob) -> 200
        # ------------------------------------------------------------------
        res = client.post('/api/follow', data={'target_username_give': u_bob})
        data = res.get_json()
        pass6 = (
            res.status_code == 200 and
            data.get('result') == 'success' and
            data.get('is_following') is True and
            data.get('follower_count') == 1
        )
        record(6, "Successful Follow", pass6, f"Status: {res.status_code}, is_following={data.get('is_following')}, followers={data.get('follower_count')}")

        # ------------------------------------------------------------------
        # 7. Duplicate Follow Attempt -> 409 Conflict
        # ------------------------------------------------------------------
        res = client.post('/api/follow', data={'target_username_give': u_bob})
        data = res.get_json()
        pass7 = (
            res.status_code == 409 and
            data.get('result') == 'fail' and
            'already following' in data.get('msg', '').lower()
        )
        # Verify database only has 1 record
        doc_count = db.follows.count_documents({'follower': u_alice, 'following': u_bob})
        pass7 = pass7 and (doc_count == 1)
        record(7, "Duplicate Follow Prevention", pass7, f"Status: {res.status_code}, DB doc count={doc_count}")

        # ------------------------------------------------------------------
        # 8. Follow Status Query API -> 200
        # ------------------------------------------------------------------
        res = client.get(f'/api/follow_status?target_username={u_bob}')
        data = res.get_json()
        pass8 = (
            res.status_code == 200 and
            data.get('result') == 'success' and
            data.get('is_following') is True and
            data.get('follower_count') == 1
        )
        record(8, "Follow Status Query API", pass8, f"Status: {res.status_code}, is_following={data.get('is_following')}")

        # ------------------------------------------------------------------
        # 9. Profile View of Followed User -> Shows "Following" State & Real Counts
        # ------------------------------------------------------------------
        res = client.get(f'/user/{u_bob}')
        html = res.get_data(as_text=True)
        pass9 = (
            res.status_code == 200 and
            'id="follow-btn"' in html and
            'data-following="true"' in html and
            'Following' in html and
            'id="follower-count-display"' in html and
            '>1<' in html # Follower count should be 1
        )
        record(9, "Follow State on Profile View", pass9, f"Status: {res.status_code}, Follow button shows 'Following' state")

        # ------------------------------------------------------------------
        # 10. Own Profile -> No Follow Button, Shows Edit Profile
        # ------------------------------------------------------------------
        res = client.get(f'/user/{u_alice}')
        html = res.get_data(as_text=True)
        pass10 = (
            res.status_code == 200 and
            'Edit Profile' in html and
            'id="follow-btn"' not in html
        )
        record(10, "Own Profile Hides Follow Button", pass10, f"Status: {res.status_code}, Only Edit Profile rendered")

        # ------------------------------------------------------------------
        # 11. Followers List API (`/api/users/<username>/followers`) -> 200
        # ------------------------------------------------------------------
        res = client.get(f'/api/users/{u_bob}/followers')
        data = res.get_json()
        followers = data.get('users', [])
        pass11 = (
            res.status_code == 200 and
            data.get('total') == 1 and
            len(followers) == 1 and
            followers[0]['username'] == u_alice and
            followers[0]['profile_name'] == "Alice Wonderland"
        )
        record(11, "Followers List API", pass11, f"Status: {res.status_code}, Total={data.get('total')}, Follower={followers[0]['username'] if followers else 'None'}")

        # ------------------------------------------------------------------
        # 12. Following List API (`/api/users/<username>/following`) -> 200
        # ------------------------------------------------------------------
        res = client.get(f'/api/users/{u_alice}/following')
        data = res.get_json()
        following = data.get('users', [])
        pass12 = (
            res.status_code == 200 and
            data.get('total') == 1 and
            len(following) == 1 and
            following[0]['username'] == u_bob and
            following[0]['is_following'] is True
        )
        record(12, "Following List API", pass12, f"Status: {res.status_code}, Total={data.get('total')}, Following={following[0]['username'] if following else 'None'}")

        # ------------------------------------------------------------------
        # 13. Dedicated Followers Page (`/user/<username>/followers`) -> 200
        # ------------------------------------------------------------------
        res = client.get(f'/user/{u_bob}/followers')
        html = res.get_data(as_text=True)
        pass13 = (
            res.status_code == 200 and
            "Followers" in html and
            u_bob in html and
            "tab-follower-count" in html
        )
        record(13, "Dedicated Followers Page View", pass13, f"Status: {res.status_code}, Page rendered successfully")

        # ------------------------------------------------------------------
        # 14. Dedicated Following Page (`/user/<username>/following`) -> 200
        # ------------------------------------------------------------------
        res = client.get(f'/user/{u_alice}/following')
        html = res.get_data(as_text=True)
        pass14 = (
            res.status_code == 200 and
            "Following" in html and
            u_alice in html and
            "tab-following-count" in html
        )
        record(14, "Dedicated Following Page View", pass14, f"Status: {res.status_code}, Page rendered successfully")

        # ------------------------------------------------------------------
        # 15. RESTful URI Follow Route (`POST /api/users/<target>/follow`) -> Bob follows Charlie
        # ------------------------------------------------------------------
        client.set_cookie('mytoken', token_bob)
        res = client.post(f'/api/users/{u_charlie}/follow')
        data = res.get_json()
        pass15 = (
            res.status_code == 200 and
            data.get('result') == 'success' and
            data.get('is_following') is True
        )
        record(15, "RESTful URI Route Follow", pass15, f"Status: {res.status_code}, Bob followed Charlie via URI parameter")

        # ------------------------------------------------------------------
        # 16. Successful Unfollow (Alice unfollows Bob) -> 200
        # ------------------------------------------------------------------
        client.set_cookie('mytoken', token_alice)
        res = client.post('/api/unfollow', data={'target_username_give': u_bob})
        data = res.get_json()
        pass16 = (
            res.status_code == 200 and
            data.get('result') == 'success' and
            data.get('is_following') is False and
            data.get('follower_count') == 0
        )
        # Verify removed from database
        db_doc = db.follows.find_one({'follower': u_alice, 'following': u_bob})
        pass16 = pass16 and (db_doc is None)
        record(16, "Successful Unfollow", pass16, f"Status: {res.status_code}, is_following={data.get('is_following')}, followers={data.get('follower_count')}")

        # ------------------------------------------------------------------
        # 17. Safe Unfollow When Not Following (Idempotency) -> 200
        # ------------------------------------------------------------------
        res = client.post('/api/unfollow', data={'target_username_give': u_bob})
        data = res.get_json()
        pass17 = (
            res.status_code == 200 and
            data.get('result') == 'success' and
            data.get('is_following') is False
        )
        record(17, "Safe / Idempotent Unfollow", pass17, f"Status: {res.status_code}, Safe deletion when relation does not exist")

        # ------------------------------------------------------------------
        # 18. Profile Follow State After Unfollow -> Shows "Follow" State
        # ------------------------------------------------------------------
        res = client.get(f'/user/{u_bob}')
        html = res.get_data(as_text=True)
        pass18 = (
            res.status_code == 200 and
            'id="follow-btn"' in html and
            'data-following="false"' in html and
            '>0<' in html # Follower count is 0
        )
        record(18, "Profile State Updated After Unfollow", pass18, f"Status: {res.status_code}, Follow button restored to 'Follow' state")

        # ------------------------------------------------------------------
        # 19. Database Unique Compound Index Invariant Check
        # ------------------------------------------------------------------
        now = datetime.now(timezone.utc).isoformat()
        db.follows.insert_one({'follower': u_alice, 'following': u_charlie, 'created_at': now})
        duplicate_caught = False
        try:
            db.follows.insert_one({'follower': u_alice, 'following': u_charlie, 'created_at': now})
        except DuplicateKeyError:
            duplicate_caught = True
        pass19 = duplicate_caught
        record(19, "MongoDB Engine Unique Constraint", pass19, f"DuplicateKeyError raised={duplicate_caught}")

    finally:
        # Cleanup
        db.users.delete_many({"username": {"$in": [u_alice, u_bob, u_charlie]}})
        db.follows.delete_many({"$or": [
            {"follower": {"$in": [u_alice, u_bob, u_charlie]}},
            {"following": {"$in": [u_alice, u_bob, u_charlie]}}
        ]})

    print("\n--- Summary of Follow System Testing ---")
    all_passed = all(r["status"] == "PASS" for r in test_results)
    print(f"Total Tests: {len(test_results)}, Passed: {sum(1 for r in test_results if r['status'] == 'PASS')}, Failed: {sum(1 for r in test_results if r['status'] == 'FAIL')}")
    if all_passed:
        print("ALL TESTS PASSED SUCCESSFULLY!")
    else:
        print("SOME TESTS FAILED.")
    return all_passed

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
