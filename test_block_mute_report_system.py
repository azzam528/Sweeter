import os
import sys
import json
import jwt
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app import app
from database import db, has_block_relation, is_blocked, is_muted

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

def run_tests():
    client = app.test_client()
    secret = app.config["SECRET_KEY"]

    u_alice = "test_bmr_alice"
    u_bob = "test_bmr_bob"
    u_charlie = "test_bmr_charlie"
    u_admin = "test_bmr_admin"
    all_users = [u_alice, u_bob, u_charlie, u_admin]

    def cleanup():
        db.users.delete_many({"username": {"$in": all_users}})
        db.follows.delete_many({"$or": [
            {"follower": {"$in": all_users}},
            {"following": {"$in": all_users}}
        ]})
        db.blocks.delete_many({"$or": [
            {"blocker": {"$in": all_users}},
            {"blocked": {"$in": all_users}}
        ]})
        db.mutes.delete_many({"$or": [
            {"muter": {"$in": all_users}},
            {"muted": {"$in": all_users}}
        ]})
        db.reports.delete_many({"$or": [
            {"reporter_id": {"$in": all_users}},
            {"reporter": {"$in": all_users}},
            {"target_author": {"$in": all_users}},
            {"target_id": {"$in": all_users}}
        ]})
        db.posts.delete_many({"username": {"$in": all_users}})
        db.comments.delete_many({"username": {"$in": all_users}})
        db.notifications.delete_many({"$or": [
            {"recipient": {"$in": all_users}},
            {"actor": {"$in": all_users}}
        ]})
        db.moderation_logs.delete_many({"$or": [
            {"admin": {"$in": all_users}},
            {"admin_id": {"$in": all_users}}
        ]})

    cleanup()

    # Setup test users
    db.users.insert_many([
        {
            "username": u_alice,
            "password": "hashed_alice",
            "profile_name": "Alice BlockTester",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Bio Alice",
            "is_admin": False
        },
        {
            "username": u_bob,
            "password": "hashed_bob",
            "profile_name": "Bob Target",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Bio Bob",
            "is_admin": False
        },
        {
            "username": u_charlie,
            "password": "hashed_charlie",
            "profile_name": "Charlie ThirdParty",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Bio Charlie",
            "is_admin": False
        },
        {
            "username": u_admin,
            "password": "hashed_admin",
            "profile_name": "Super Admin",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Bio Admin",
            "is_admin": True,
            "role": "admin"
        }
    ])

    token_alice = jwt.encode({"id": u_alice, "exp": datetime.now(timezone.utc) + timedelta(hours=2)}, secret, algorithm="HS256")
    token_bob = jwt.encode({"id": u_bob, "exp": datetime.now(timezone.utc) + timedelta(hours=2)}, secret, algorithm="HS256")
    token_charlie = jwt.encode({"id": u_charlie, "exp": datetime.now(timezone.utc) + timedelta(hours=2)}, secret, algorithm="HS256")
    token_admin = jwt.encode({"id": u_admin, "exp": datetime.now(timezone.utc) + timedelta(hours=2)}, secret, algorithm="HS256")

    try:
        # ==================================================================
        # 1. BLOCK SYSTEM TESTS
        # ==================================================================
        
        # 1.1 Unauthenticated Block
        res = client.post('/api/block', data={'target_username': u_bob})
        pass1 = (res.status_code == 401)
        record(1, "Unauthenticated Block Request", pass1, f"Status: {res.status_code}")

        # 1.2 Self-Block Prevention
        client.set_cookie('mytoken', token_alice)
        res = client.post('/api/block', data={'target_username': u_alice})
        data = res.get_json() or {}
        pass2 = (res.status_code == 400 and 'cannot block yourself' in data.get('msg', '').lower())
        record(2, "Self-Block Prevention", pass2, f"Status: {res.status_code}, Msg: {data.get('msg')}")

        # 1.3 Block Non-existent User
        res = client.post('/api/block', data={'target_username': 'phantom_user_xyz'})
        pass3 = (res.status_code == 404)
        record(3, "Block Non-Existent User", pass3, f"Status: {res.status_code}")

        # Establish mutual follow between Alice and Bob first
        now_str = datetime.now(timezone.utc).isoformat()
        db.follows.insert_many([
            {"follower": u_alice, "following": u_bob, "created_at": now_str},
            {"follower": u_bob, "following": u_alice, "created_at": now_str}
        ])
        assert db.follows.count_documents({"$or": [{"follower": u_alice}, {"follower": u_bob}]}) == 2

        # 1.4 Alice Blocks Bob -> Mutual Unfollow and Record in db.blocks
        res = client.post('/api/block', data={'target_username': u_bob})
        data = res.get_json() or {}
        block_doc = db.blocks.find_one({"blocker": u_alice, "blocked": u_bob})
        follow_count = db.follows.count_documents({
            "$or": [
                {"follower": u_alice, "following": u_bob},
                {"follower": u_bob, "following": u_alice}
            ]
        })
        pass4 = (res.status_code == 200 and data.get('result') == 'success' and block_doc is not None and follow_count == 0)
        record(4, "Block User: Mutual Unfollow & Block Record Created", pass4, f"Status: {res.status_code}, Follow Count: {follow_count}")

        # 1.5 Database Unique Compound Index on (blocker, blocked)
        duplicate_caught = False
        try:
            db.blocks.insert_one({"blocker": u_alice, "blocked": u_bob, "created_at": now_str})
        except DuplicateKeyError:
            duplicate_caught = True
        record(5, "Database Unique Compound Index on Blocks", duplicate_caught, f"DuplicateKeyError caught: {duplicate_caught}")

        # 1.6 Blocked user (Bob) cannot follow blocker (Alice)
        client.set_cookie('mytoken', token_bob)
        res = client.post('/api/follow', data={'target_username_give': u_alice})
        pass6 = (res.status_code == 403)
        record(6, "Blocked User Cannot Follow Blocker", pass6, f"Status: {res.status_code}, Msg: {res.get_json()}")

        # 1.7 Blocker (Alice) cannot follow blocked user (Bob)
        client.set_cookie('mytoken', token_alice)
        res = client.post('/api/follow', data={'target_username_give': u_bob})
        pass7 = (res.status_code == 403)
        record(7, "Blocker Cannot Follow Blocked User", pass7, f"Status: {res.status_code}, Msg: {res.get_json()}")

        # 1.8 Create posts for Alice and Bob
        post_alice_id = db.posts.insert_one({
            "username": u_alice,
            "profile_name": "Alice BlockTester",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "post_title": "Alice's Secret Post",
            "post_content": "This is Alice's post.",
            "date": now_str,
            "type_post": "normal"
        }).inserted_id

        post_bob_id = db.posts.insert_one({
            "username": u_bob,
            "profile_name": "Bob Target",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "post_title": "Bob's Public Post",
            "post_content": "This is Bob's post.",
            "date": now_str,
            "type_post": "normal"
        }).inserted_id

        # 1.9 Feed Isolation: Bob's post does NOT appear in Alice's timeline or explore
        client.set_cookie('mytoken', token_alice)
        res_timeline = client.get('/get_posts')
        timeline_posts = res_timeline.get_json().get('posts', [])
        alice_sees_bob = any(p.get('username') == u_bob for p in timeline_posts)
        pass8 = (res_timeline.status_code == 200 and not alice_sees_bob)
        record(8, "Feed Isolation: Bob's post hidden from Alice's timeline", pass8, f"Bob in Alice feed: {alice_sees_bob}")

        # 1.10 Feed Isolation: Alice's post does NOT appear in Bob's timeline
        client.set_cookie('mytoken', token_bob)
        res_bob_timeline = client.get('/get_posts')
        bob_timeline_posts = res_bob_timeline.get_json().get('posts', [])
        bob_sees_alice = any(p.get('username') == u_alice for p in bob_timeline_posts)
        pass9 = (res_bob_timeline.status_code == 200 and not bob_sees_alice)
        record(9, "Feed Isolation: Alice's post hidden from Bob's timeline", pass9, f"Alice in Bob feed: {bob_sees_alice}")

        # 1.11 Profile Isolation: Bob cannot view Alice's profile posts
        res_profile_posts = client.get(f'/get_posts?username_give={u_alice}')
        profile_posts = res_profile_posts.get_json().get('posts', [])
        pass10 = (res_profile_posts.status_code == 200 and len(profile_posts) == 0)
        record(10, "Profile Isolation: Blocked user receives 0 posts for blocker profile", pass10, f"Post count: {len(profile_posts)}")

        # 1.12 Interaction Prevention: Bob cannot comment on Alice's post
        res_comment = client.post('/create_comment', data={
            'post_id_give': str(post_alice_id),
            'content_give': 'Attempting comment from blocked user'
        })
        pass11 = (res_comment.status_code == 403)
        record(11, "Interaction Prevention: Blocked user cannot comment on blocker's post", pass11, f"Status: {res_comment.status_code}")

        # 1.13 Interaction Prevention: Bob cannot like Alice's post
        res_like = client.post('/update_like', data={
            'post_id_give': str(post_alice_id),
            'type_give': 'like',
            'action_give': 'like'
        })
        pass12 = (res_like.status_code == 403)
        record(12, "Interaction Prevention: Blocked user cannot like blocker's post", pass12, f"Status: {res_like.status_code}")

        # 1.14 Comment Visibility: Charlie makes a post; Bob comments on it. Alice views Charlie's post comments -> Bob's comment must be filtered out
        post_charlie_id = db.posts.insert_one({
            "username": u_charlie,
            "profile_name": "Charlie ThirdParty",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "post_title": "Charlie's Open Post",
            "post_content": "Everyone can comment here.",
            "date": now_str,
            "type_post": "normal"
        }).inserted_id

        comment_bob_id = db.comments.insert_one({
            "post_id": str(post_charlie_id),
            "username": u_bob,
            "content": "Bob was here on Charlie's post",
            "created_at": now_str,
            "parent_comment_id": None,
            "is_deleted": False
        }).inserted_id

        client.set_cookie('mytoken', token_alice)
        res_comments = client.get(f'/get_comments?post_id_give={str(post_charlie_id)}')
        comments_data = res_comments.get_json().get('comments', [])
        alice_sees_bob_comment = any(c.get('username') == u_bob for c in comments_data)
        pass13 = (res_comments.status_code == 200 and not alice_sees_bob_comment)
        record(13, "Comment Isolation: Blocked user's comments hidden from blocker on 3rd party posts", pass13, f"Alice sees Bob comment: {alice_sees_bob_comment}")

        # 1.15 Block Management: List blocked users API
        res_blocked_list = client.get('/api/blocked_users')
        data_blocked = res_blocked_list.get_json() or {}
        blocked_users = data_blocked.get('blocked_users', [])
        pass14 = (res_blocked_list.status_code == 200 and any(u.get('username') == u_bob for u in blocked_users))
        record(14, "Block Management: GET /api/blocked_users returns blocked accounts", pass14, f"Found Bob: {pass14}")

        # 1.16 Unblock User: Alice unblocks Bob -> Follow relation is NOT restored
        res_unblock = client.post('/api/unblock', data={'target_username': u_bob})
        data_unblock = res_unblock.get_json() or {}
        block_exists = db.blocks.find_one({"blocker": u_alice, "blocked": u_bob})
        follow_exists = db.follows.find_one({"$or": [
            {"follower": u_alice, "following": u_bob},
            {"follower": u_bob, "following": u_alice}
        ]})
        pass15 = (res_unblock.status_code == 200 and data_unblock.get('result') == 'success' and block_exists is None and follow_exists is None)
        record(15, "Unblock User: Block removed, Follow NOT restored", pass15, f"Block exists: {block_exists is not None}, Follow exists: {follow_exists is not None}")

        # 1.17 After Unblock: Bob can now view and comment on Alice's post
        client.set_cookie('mytoken', token_bob)
        res_comment_after = client.post('/create_comment', data={
            'post_id_give': str(post_alice_id),
            'content_give': 'Now unblocked, Bob can comment!'
        })
        pass16 = (res_comment_after.status_code in [200, 201])
        record(16, "Post-Unblock Access: Bob can comment after unblock", pass16, f"Status: {res_comment_after.status_code}")

        # ==================================================================
        # 2. MUTE SYSTEM TESTS
        # ==================================================================
        
        # 2.1 Self-Mute Prevention
        client.set_cookie('mytoken', token_alice)
        res = client.post('/api/mute', data={'target_username': u_alice})
        pass17 = (res.status_code == 400 and 'cannot mute yourself' in (res.get_json() or {}).get('msg', '').lower())
        record(17, "Self-Mute Prevention", pass17, f"Status: {res.status_code}")

        # 2.2 Alice follows Charlie; then Alice mutes Charlie -> Follow MUST remain intact
        db.follows.insert_one({"follower": u_alice, "following": u_charlie, "created_at": now_str})
        res_mute = client.post('/api/mute', data={'target_username': u_charlie})
        follow_intact = db.follows.find_one({"follower": u_alice, "following": u_charlie})
        mute_exists = db.mutes.find_one({"muter": u_alice, "muted": u_charlie})
        pass18 = (res_mute.status_code == 200 and follow_intact is not None and mute_exists is not None)
        record(18, "Mute User: Content muted without altering Follow status", pass18, f"Mute exists: {mute_exists is not None}, Follow intact: {follow_intact is not None}")

        # 2.3 Unique constraint on mutes
        dup_mute_caught = False
        try:
            db.mutes.insert_one({"muter": u_alice, "muted": u_charlie, "created_at": now_str})
        except DuplicateKeyError:
            dup_mute_caught = True
        record(19, "Database Unique Compound Index on Mutes", dup_mute_caught, f"Duplicate caught: {dup_mute_caught}")

        # 2.4 Feed Filtering for Muted User: Charlie's post hidden from Alice's timeline
        res_timeline_mute = client.get('/get_posts')
        posts_alice = res_timeline_mute.get_json().get('posts', [])
        alice_sees_charlie = any(p.get('username') == u_charlie for p in posts_alice)
        pass20 = (res_timeline_mute.status_code == 200 and not alice_sees_charlie)
        record(20, "Feed Filtering: Muted user's posts hidden from timeline", pass20, f"Charlie seen in Alice feed: {alice_sees_charlie}")

        # 2.5 Profile Access Still Allowed for Muted User: Alice can still see Charlie's profile posts
        res_charlie_prof = client.get(f'/get_posts?username_give={u_charlie}')
        charlie_posts = res_charlie_prof.get_json().get('posts', [])
        pass21 = (res_charlie_prof.status_code == 200 and len(charlie_posts) > 0)
        record(21, "Profile Access: Blocker/Muter can still view muted user's profile", pass21, f"Posts returned: {len(charlie_posts)}")

        # 2.6 Mute Management: GET /api/muted_users
        res_muted_list = client.get('/api/muted_users')
        muted_users = (res_muted_list.get_json() or {}).get('muted_users', [])
        pass22 = (res_muted_list.status_code == 200 and any(u.get('username') == u_charlie for u in muted_users))
        record(22, "Mute Management: GET /api/muted_users returns muted accounts", pass22, f"Found Charlie: {pass22}")

        # 2.7 Unmute User: Restores visibility without altering follow relations
        res_unmute = client.post('/api/unmute', data={'target_username': u_charlie})
        mute_still_there = db.mutes.find_one({"muter": u_alice, "muted": u_charlie})
        follow_still_there = db.follows.find_one({"follower": u_alice, "following": u_charlie})
        res_timeline_unmute = client.get('/get_posts')
        alice_sees_charlie_now = any(p.get('username') == u_charlie for p in res_timeline_unmute.get_json().get('posts', []))
        pass23 = (res_unmute.status_code == 200 and mute_still_there is None and follow_still_there is not None and alice_sees_charlie_now)
        record(23, "Unmute User: Content restored to feed, follow remains intact", pass23, f"Mute removed: {mute_still_there is None}, Feed restored: {alice_sees_charlie_now}")

        # ==================================================================
        # 3. BLOCK & MUTE PRIORITY TESTS
        # ==================================================================
        
        # 3.1 Mute Charlie, then Block Charlie: Block priority dominates
        client.post('/api/mute', data={'target_username': u_charlie})
        client.post('/api/block', data={'target_username': u_charlie})
        assert db.mutes.find_one({"muter": u_alice, "muted": u_charlie}) is not None
        assert db.blocks.find_one({"blocker": u_alice, "blocked": u_charlie}) is not None

        # Charlie cannot follow Alice even though muted as well
        client.set_cookie('mytoken', token_charlie)
        res_charlie_flw = client.post('/api/follow', data={'target_username_give': u_alice})
        pass24 = (res_charlie_flw.status_code == 403)
        record(24, "Priority: Block takes precedence over Mute (Follow denied)", pass24, f"Status: {res_charlie_flw.status_code}")

        # 3.2 Unblocking does NOT automatically unmute
        client.set_cookie('mytoken', token_alice)
        client.post('/api/unblock', data={'target_username': u_charlie})
        mute_remains = db.mutes.find_one({"muter": u_alice, "muted": u_charlie}) is not None
        block_cleared = db.blocks.find_one({"blocker": u_alice, "blocked": u_charlie}) is None
        pass25 = (block_cleared and mute_remains)
        record(25, "Priority: Unblock does NOT automatically unmute", pass25, f"Block cleared: {block_cleared}, Mute remains: {mute_remains}")

        # Clean up mute
        client.post('/api/unmute', data={'target_username': u_charlie})

        # ==================================================================
        # 4. REPORT SYSTEM TESTS
        # ==================================================================
        
        # 4.1 Unauthenticated Report -> 401
        client.set_cookie('mytoken', '', expires=0)
        res_rep_unauth = client.post('/api/reports', json={
            'target_type': 'post',
            'target_id': str(post_bob_id),
            'reason': 'spam'
        })
        pass26 = (res_rep_unauth.status_code == 401)
        record(26, "Unauthenticated Report Request", pass26, f"Status: {res_rep_unauth.status_code}")

        # 4.2 Self-Report Prevention -> 400
        client.set_cookie('mytoken', token_alice)
        res_self_rep = client.post('/api/reports', json={
            'target_type': 'user',
            'target_id': u_alice,
            'reason': 'harassment'
        })
        pass27 = (res_self_rep.status_code == 400 and 'cannot report yourself' in (res_self_rep.get_json() or {}).get('msg', '').lower())
        record(27, "Self-Report Prevention", pass27, f"Status: {res_self_rep.status_code}")

        # 4.3 Invalid Report Reason -> 400
        res_invalid_reason = client.post('/api/reports', json={
            'target_type': 'post',
            'target_id': str(post_bob_id),
            'reason': 'i_just_dont_like_him'
        })
        pass28 = (res_invalid_reason.status_code == 400)
        record(28, "Invalid Report Reason Validation", pass28, f"Status: {res_invalid_reason.status_code}")

        # 4.4 Report Non-existent Target -> 404
        res_rep_404 = client.post('/api/reports', json={
            'target_type': 'post',
            'target_id': str(ObjectId()),
            'reason': 'spam'
        })
        pass29 = (res_rep_404.status_code == 404)
        record(29, "Report Non-Existent Target", pass29, f"Status: {res_rep_404.status_code}")

        # 4.5 Successful Report Creation for Post
        res_rep_post = client.post('/api/reports', json={
            'target_type': 'post',
            'target_id': str(post_bob_id),
            'reason': 'spam',
            'description': 'Promoting unwanted unsolicited crypto ads'
        })
        data_rep = res_rep_post.get_json() or {}
        report_id_str = data_rep.get('report_id')
        rep_doc = db.reports.find_one({"_id": ObjectId(report_id_str)}) if report_id_str else None
        pass30 = (
            res_rep_post.status_code in [200, 201] and
            data_rep.get('result') == 'success' and
            rep_doc is not None and
            rep_doc.get('status') == 'pending' and
            rep_doc.get('reason') == 'spam' and
            rep_doc.get('target_snapshot') is not None
        )
        record(30, "Successful Post Report Creation", pass30, f"Report ID: {report_id_str}, Status: {rep_doc.get('status') if rep_doc else None}")

        # 4.6 Reporting does NOT immediately delete the post
        post_still_exists = db.posts.find_one({"_id": ObjectId(post_bob_id)})
        pass31 = (post_still_exists is not None)
        record(31, "Report Integrity: Content NOT automatically deleted on report", pass31, f"Post exists: {post_still_exists is not None}")

        # 4.7 Duplicate Pending Report Prevention -> 409 Conflict
        res_rep_dup = client.post('/api/reports', json={
            'target_type': 'post',
            'target_id': str(post_bob_id),
            'reason': 'spam',
            'description': 'Reporting again duplicate'
        })
        pass32 = (res_rep_dup.status_code == 409)
        record(32, "Duplicate Pending Report Prevention (409 Conflict)", pass32, f"Status: {res_rep_dup.status_code}")

        # 4.8 Successful Report Creation for User
        res_rep_user = client.post('/api/reports', json={
            'target_type': 'user',
            'target_id': u_bob,
            'reason': 'harassment',
            'description': 'Harassing comments'
        })
        user_report_id = (res_rep_user.get_json() or {}).get('report_id')
        pass33 = (res_rep_user.status_code in [200, 201] and user_report_id is not None)
        record(33, "Successful User Profile Report Creation", pass33, f"Report ID: {user_report_id}")

        # ==================================================================
        # 5. ADMIN MODERATION DASHBOARD & ACTIONS
        # ==================================================================
        
        # 5.1 Non-Admin Access Denied to Admin Reports -> 403
        client.set_cookie('mytoken', token_alice)
        res_admin_deny = client.get('/admin/reports')
        res_admin_api_deny = client.get('/api/admin/reports')
        pass34 = (res_admin_deny.status_code == 403 and res_admin_api_deny.status_code == 403)
        record(34, "Non-Admin Authorization Check (403)", pass34, f"HTML: {res_admin_deny.status_code}, API: {res_admin_api_deny.status_code}")

        # 5.2 Admin Access Allowed to Admin Reports API
        client.set_cookie('mytoken', token_admin)
        res_admin_api = client.get('/api/admin/reports')
        data_admin_rep = res_admin_api.get_json() or {}
        reports_list = data_admin_rep.get('reports', [])
        metrics = data_admin_rep.get('metrics', {}) or data_admin_rep.get('stats', {})
        pass35 = (res_admin_api.status_code == 200 and len(reports_list) >= 2 and metrics.get('pending', 0) >= 2)
        record(35, "Admin Reports API: Returns reports & metrics", pass35, f"Report Count: {len(reports_list)}, Pending: {metrics.get('pending')}")

        # 5.3 Admin Status Filter
        res_filter_pending = client.get('/api/admin/reports?status=pending')
        reports_pending = (res_filter_pending.get_json() or {}).get('reports', [])
        pass36 = (res_filter_pending.status_code == 200 and all(r.get('status') == 'pending' for r in reports_pending))
        record(36, "Admin Reports Filtering: by status=pending", pass36, f"Matched: {len(reports_pending)}")

        # 5.4 Admin Updates Report Status
        res_upd_status = client.post(f'/api/admin/reports/{report_id_str}/status', json={
            'status': 'under_review',
            'admin_note': 'Investigating crypto spam'
        })
        rep_doc_updated = db.reports.find_one({"_id": ObjectId(report_id_str)})
        pass37 = (res_upd_status.status_code == 200 and rep_doc_updated.get('status') == 'under_review')
        record(37, "Admin Update Report Status to under_review", pass37, f"Status: {rep_doc_updated.get('status')}")

        # 5.5 Admin Action: Dismiss Report
        res_dismiss = client.post(f'/api/admin/reports/{user_report_id}/action', json={
            'action': 'dismiss',
            'admin_note': 'No violation found'
        })
        user_rep_doc = db.reports.find_one({"_id": ObjectId(user_report_id)})
        dismiss_log = db.moderation_logs.find_one({"target_id": str(u_bob), "action": "dismiss"}) or db.moderation_logs.find_one({"report_id": str(user_report_id)})
        pass38 = (res_dismiss.status_code == 200 and user_rep_doc.get('status') == 'rejected' and dismiss_log is not None)
        record(38, "Admin Action: Dismiss Report & Audit Log Record", pass38, f"Status: {user_rep_doc.get('status')}, Logged: {dismiss_log is not None}")

        # 5.6 Admin Action: Remove Post with Cascading Deletion
        res_remove_post = client.post(f'/api/admin/reports/{report_id_str}/action', json={
            'action': 'remove_post',
            'admin_note': 'Confirmed spam content'
        })
        post_after_del = db.posts.find_one({"_id": ObjectId(post_bob_id)})
        rep_doc_resolved = db.reports.find_one({"_id": ObjectId(report_id_str)})
        del_log = db.moderation_logs.find_one({"target_id": str(post_bob_id), "action": "remove_post"})
        pass39 = (
            res_remove_post.status_code == 200 and
            post_after_del is None and
            rep_doc_resolved.get('status') == 'resolved' and
            del_log is not None
        )
        record(39, "Admin Action: Remove Post Cascade & Audit Log Record", pass39, f"Post Deleted: {post_after_del is None}, Status: {rep_doc_resolved.get('status')}")

        # 5.7 Admin Action: Remove Comment
        res_rep_comment = client.post('/api/reports', json={
            'target_type': 'comment',
            'target_id': str(comment_bob_id),
            'reason': 'inappropriate_content',
            'description': 'Offensive comment'
        })
        comment_rep_id = (res_rep_comment.get_json() or {}).get('report_id')

        res_remove_comment = client.post(f'/api/admin/reports/{comment_rep_id}/action', json={
            'action': 'remove_comment',
            'admin_note': 'Inappropriate language'
        })
        comment_after_del = db.comments.find_one({"_id": ObjectId(comment_bob_id)})
        comment_log = db.moderation_logs.find_one({"target_id": str(comment_bob_id), "action": "remove_comment"})
        pass40 = (
            res_remove_comment.status_code == 200 and
            (comment_after_del is None or comment_after_del.get('is_deleted') is True) and
            comment_log is not None
        )
        record(40, "Admin Action: Remove Comment & Audit Log Record", pass40, f"Comment Soft/Hard Deleted: {comment_after_del is None or comment_after_del.get('is_deleted')}, Logged: {comment_log is not None}")

        # 5.8 Admin Action: Suspend User
        # First report Bob
        res_rep_bob2 = client.post('/api/reports', json={
            'target_type': 'user',
            'target_id': u_bob,
            'reason': 'hate_speech',
            'description': 'Severe terms of service breach'
        })
        rep_bob2_id = (res_rep_bob2.get_json() or {}).get('report_id')

        res_suspend = client.post(f'/api/admin/reports/{rep_bob2_id}/action', json={
            'action': 'suspend_user',
            'admin_note': 'Account suspended due to hate speech'
        })
        bob_user_doc = db.users.find_one({"username": u_bob})
        suspend_log = db.moderation_logs.find_one({"target_id": u_bob, "action": "suspend_user"})
        pass41 = (
            res_suspend.status_code == 200 and
            bob_user_doc.get('is_suspended') is True and
            suspend_log is not None
        )
        record(41, "Admin Action: Suspend User & Audit Log Record", pass41, f"Suspended: {bob_user_doc.get('is_suspended')}, Logged: {suspend_log is not None}")

        # 5.9 Suspended User Cannot Authenticate / Token is Rejected
        client.set_cookie('mytoken', token_bob)
        res_bob_suspended_act = client.get('/api/blocked_users')
        pass42 = (res_bob_suspended_act.status_code == 401)
        record(42, "Suspension Enforcement: Suspended user token is rejected (401)", pass42, f"Status: {res_bob_suspended_act.status_code}")

        # 5.10 Admin Action: Unsuspend User
        client.set_cookie('mytoken', token_admin)
        res_unsuspend = client.post(f'/api/admin/reports/{rep_bob2_id}/action', json={
            'action': 'unsuspend_user',
            'admin_note': 'Appeal accepted'
        })
        bob_user_doc2 = db.users.find_one({"username": u_bob})
        pass43 = (res_unsuspend.status_code == 200 and bob_user_doc2.get('is_suspended') is False)
        record(43, "Admin Action: Unsuspend User Restores Account Access", pass43, f"Suspended: {bob_user_doc2.get('is_suspended')}")

        # 5.11 Admin Audit Logs API
        res_audit = client.get('/api/admin/audit_logs')
        audit_data = res_audit.get_json() or {}
        audit_logs = audit_data.get('audit_logs', []) or audit_data.get('logs', [])
        pass44 = (res_audit.status_code == 200 and len(audit_logs) >= 4)
        record(44, "Admin Audit Logs API: Returns chronological action logs", pass44, f"Total Logs: {len(audit_logs)}")

    finally:
        cleanup()

    print("\n--- Summary of Block, Mute & Report System Testing ---")
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
