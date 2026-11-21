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

    u_me = "test_feed_me"
    u_followed = "test_feed_followed"
    u_stranger = "test_feed_stranger"
    u_lonely = "test_feed_lonely"

    all_test_users = [u_me, u_followed, u_stranger, u_lonely]

    # Cleanup any old test data
    db.users.delete_many({"username": {"$in": all_test_users}})
    db.posts.delete_many({"username": {"$in": all_test_users}})
    db.follows.delete_many({"$or": [
        {"follower": {"$in": all_test_users}},
        {"following": {"$in": all_test_users}}
    ]})

    # Setup test users
    db.users.insert_many([
        {
            "username": u_me,
            "password": "hashed_pw_me",
            "profile_name": "Myself",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Me user"
        },
        {
            "username": u_followed,
            "password": "hashed_pw_followed",
            "profile_name": "Followed Friend",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Followed user"
        },
        {
            "username": u_stranger,
            "password": "hashed_pw_stranger",
            "profile_name": "Total Stranger",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Stranger user"
        },
        {
            "username": u_lonely,
            "password": "hashed_pw_lonely",
            "profile_name": "Lonely User",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Has zero follows and zero posts"
        }
    ])

    # Establish follow relationship: u_me follows u_followed (does NOT follow u_stranger)
    db.follows.insert_one({
        "follower": u_me,
        "following": u_followed,
        "created_at": datetime.now(timezone.utc).isoformat()
    })

    token_me = jwt.encode({"id": u_me, "exp": datetime.now(timezone.utc) + timedelta(hours=2)}, secret, algorithm="HS256")
    token_lonely = jwt.encode({"id": u_lonely, "exp": datetime.now(timezone.utc) + timedelta(hours=2)}, secret, algorithm="HS256")

    # Insert posts with controlled chronological timestamps
    base_time = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)

    # Post 1: Stranger's post (oldest)
    id_stranger_1 = db.posts.insert_one({
        "username": u_stranger,
        "profile_name": "Total Stranger",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "Stranger post 1",
        "date": (base_time + timedelta(minutes=10)).isoformat()
    }).inserted_id

    # Post 2: Followed user's post
    id_followed_1 = db.posts.insert_one({
        "username": u_followed,
        "profile_name": "Followed Friend",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "Followed friend post 1",
        "date": (base_time + timedelta(minutes=20)).isoformat()
    }).inserted_id

    # Post 3: My post 1
    id_me_1 = db.posts.insert_one({
        "username": u_me,
        "profile_name": "Myself",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "My post 1",
        "date": (base_time + timedelta(minutes=30)).isoformat()
    }).inserted_id

    # Post 4: Followed user's post 2
    id_followed_2 = db.posts.insert_one({
        "username": u_followed,
        "profile_name": "Followed Friend",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "Followed friend post 2",
        "date": (base_time + timedelta(minutes=40)).isoformat()
    }).inserted_id

    # Post 5: My post 2 (newest)
    id_me_2 = db.posts.insert_one({
        "username": u_me,
        "profile_name": "Myself",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "My post 2 (newest)",
        "date": (base_time + timedelta(minutes=50)).isoformat()
    }).inserted_id

    # Also add 5 extra posts for pagination test
    extra_post_ids = []
    for i in range(5):
        pid = db.posts.insert_one({
            "username": u_followed,
            "profile_name": "Followed Friend",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "comment": f"Followed pagination test post {i}",
            "date": (base_time + timedelta(minutes=60 + i)).isoformat()
        }).inserted_id
        extra_post_ids.append(pid)

    try:
        # ------------------------------------------------------------------
        # 1. Unauthenticated Request to /get_posts -> 401
        # ------------------------------------------------------------------
        res = client.get('/get_posts')
        pass1 = (res.status_code == 401 and res.get_json().get('result') == 'fail')
        record(1, "Unauthenticated Request Enforcement", pass1, f"Status: {res.status_code}")

        # ------------------------------------------------------------------
        # 2. Personalized Timeline: Includes own posts & followed user posts
        # ------------------------------------------------------------------
        client.set_cookie('mytoken', token_me)
        res = client.get('/get_posts?tab=following&limit=20')
        data = res.get_json()
        posts = data.get('posts', [])
        post_authors = set(p['username'] for p in posts)
        pass2 = (
            res.status_code == 200 and
            data.get('result') == 'success' and
            u_me in post_authors and
            u_followed in post_authors
        )
        record(2, "Personalized Feed Includes Self & Followed", pass2, f"Authors present: {post_authors}")

        # ------------------------------------------------------------------
        # 3. Personalized Timeline: Excludes non-followed strangers
        # ------------------------------------------------------------------
        pass3 = (u_stranger not in post_authors)
        record(3, "Personalized Feed Excludes Non-Followed Users", pass3, f"Stranger in feed: {u_stranger in post_authors}")

        # ------------------------------------------------------------------
        # 4. Explore / Global Tab: Includes stranger's post
        # ------------------------------------------------------------------
        res_exp = client.get('/get_posts?tab=explore&limit=20')
        data_exp = res_exp.get_json()
        exp_authors = set(p['username'] for p in data_exp.get('posts', []))
        pass4 = (
            res_exp.status_code == 200 and
            u_stranger in exp_authors and
            u_me in exp_authors and
            u_followed in exp_authors
        )
        record(4, "Explore Tab Includes All Public Posts", pass4, f"All authors found: {exp_authors}")

        # ------------------------------------------------------------------
        # 5. Empty Following List Handling (User with 0 follows & 0 posts)
        # ------------------------------------------------------------------
        client.set_cookie('mytoken', token_lonely)
        res_lonely = client.get('/get_posts?tab=following&limit=20')
        data_lonely = res_lonely.get_json()
        pass5 = (
            res_lonely.status_code == 200 and
            data_lonely.get('result') == 'success' and
            len(data_lonely.get('posts', [])) == 0 and
            data_lonely.get('has_more') is False
        )
        record(5, "Empty Following List Handled Gracefully", pass5, f"Post count for lonely user: {len(data_lonely.get('posts', []))}")

        # ------------------------------------------------------------------
        # 6. Sorting Order (Newest First)
        # ------------------------------------------------------------------
        client.set_cookie('mytoken', token_me)
        res_sort = client.get('/get_posts?tab=following&limit=20')
        posts_sort = res_sort.get_json().get('posts', [])
        dates = [p['date'] for p in posts_sort]
        is_sorted_desc = all(dates[i] >= dates[i+1] for i in range(len(dates)-1))
        pass6 = (len(dates) > 0 and is_sorted_desc)
        record(6, "Chronological Sorting (Newest First)", pass6, f"Total posts: {len(dates)}, correctly ordered descending={is_sorted_desc}")

        # ------------------------------------------------------------------
        # 7. Keyset Cursor Pagination (Page 1 vs Page 2)
        # ------------------------------------------------------------------
        limit_size = 4
        res_p1 = client.get(f'/get_posts?tab=following&limit={limit_size}')
        data_p1 = res_p1.get_json()
        posts_p1 = data_p1.get('posts', [])
        next_cursor = data_p1.get('next_cursor')
        has_more_p1 = data_p1.get('has_more')

        res_p2 = client.get(f'/get_posts?tab=following&limit={limit_size}&cursor={next_cursor}')
        data_p2 = res_p2.get_json()
        posts_p2 = data_p2.get('posts', [])

        p1_ids = set(p['_id'] for p in posts_p1)
        p2_ids = set(p['_id'] for p in posts_p2)
        overlap = p1_ids.intersection(p2_ids)

        pass7 = (
            res_p1.status_code == 200 and
            res_p2.status_code == 200 and
            len(posts_p1) == limit_size and
            len(posts_p2) > 0 and
            has_more_p1 is True and
            next_cursor is not None and
            len(overlap) == 0 # Zero duplicate posts across pages
        )
        record(7, "Keyset Cursor Pagination & No Duplicates", pass7, f"Page 1: {len(posts_p1)} posts, Page 2: {len(posts_p2)} posts, Overlap: {len(overlap)}")

        # ------------------------------------------------------------------
        # 8. Invalid Pagination Input: Non-integer Limit -> 400
        # ------------------------------------------------------------------
        res_bad_limit = client.get('/get_posts?limit=abc')
        pass8 = (res_bad_limit.status_code == 400 and 'limit' in res_bad_limit.get_json().get('msg', '').lower())
        record(8, "Invalid Limit Parameter Handled", pass8, f"Status: {res_bad_limit.status_code}")

        # ------------------------------------------------------------------
        # 9. Invalid Pagination Input: Corrupt Cursor -> 400
        # ------------------------------------------------------------------
        res_bad_cursor = client.get('/get_posts?cursor=corrupt_cursor_not_object_id')
        pass9 = (res_bad_cursor.status_code == 400 and 'cursor' in res_bad_cursor.get_json().get('msg', '').lower())
        record(9, "Invalid Cursor Parameter Handled", pass9, f"Status: {res_bad_cursor.status_code}")

        # ------------------------------------------------------------------
        # 10. Nonexistent Valid Cursor -> 404
        # ------------------------------------------------------------------
        random_nonexistent_id = str(ObjectId())
        res_ghost_cursor = client.get(f'/get_posts?cursor={random_nonexistent_id}')
        pass10 = (res_ghost_cursor.status_code == 404 and 'not found' in res_ghost_cursor.get_json().get('msg', '').lower())
        record(10, "Nonexistent Cursor Handled with 404", pass10, f"Status: {res_ghost_cursor.status_code}")

        # ------------------------------------------------------------------
        # 11. Profile Feed Filter Compatibility (/get_posts?username_give=...)
        # ------------------------------------------------------------------
        res_prof = client.get(f'/get_posts?username_give={u_followed}')
        data_prof = res_prof.get_json()
        prof_posts = data_prof.get('posts', [])
        pass11 = (
            res_prof.status_code == 200 and
            len(prof_posts) > 0 and
            all(p['username'] == u_followed for p in prof_posts)
        )
        record(11, "Profile Feed Filter Compatibility", pass11, f"Fetched {len(prof_posts)} posts exclusively for {u_followed}")

        # ------------------------------------------------------------------
        # 12. Batch Reaction & Comment Integrity on Feed Posts
        # ------------------------------------------------------------------
        # React to post 5 (my post 2) with a heart from u_me
        target_post_id = str(id_me_2)
        db.likes.insert_one({"post_id": target_post_id, "username": u_me, "type": "heart"})
        db.comments.insert_one({
            "post_id": target_post_id,
            "parent_comment_id": "",
            "username": u_followed,
            "profile_name": "Followed Friend",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "content": "Nice post!",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "is_deleted": False
        })

        res_check = client.get('/get_posts?tab=following&limit=20')
        feed_posts = res_check.get_json().get('posts', [])
        target_in_feed = next((p for p in feed_posts if p['_id'] == target_post_id), None)
        pass12 = (
            target_in_feed is not None and
            target_in_feed.get('count_heart') == 1 and
            target_in_feed.get('heart_by_me') is True and
            target_in_feed.get('count_comments') == 1
        )
        record(12, "Batch Reactions & Comments Integrity", pass12, f"Heart count: {target_in_feed.get('count_heart') if target_in_feed else None}, Comment count: {target_in_feed.get('count_comments') if target_in_feed else None}")

    finally:
        # Cleanup
        db.users.delete_many({"username": {"$in": all_test_users}})
        db.posts.delete_many({"username": {"$in": all_test_users}})
        db.follows.delete_many({"$or": [
            {"follower": {"$in": all_test_users}},
            {"following": {"$in": all_test_users}}
        ]})
        db.likes.delete_many({"post_id": str(id_me_2)})
        db.comments.delete_many({"post_id": str(id_me_2)})

    print("\n--- Summary of Feed System Testing ---")
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
