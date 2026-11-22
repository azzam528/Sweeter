import os
import sys
import json
import jwt
from datetime import datetime, timezone, timedelta
from bson import ObjectId

from app import app, calculate_trending_score
from database import db, extract_hashtags, get_trending_hashtags, get_suggested_users

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

    u_main = "test_exp_main"
    u_friend = "test_exp_friend"
    u_stranger1 = "test_exp_stranger1"
    u_stranger2 = "test_exp_stranger2"

    all_users = [u_main, u_friend, u_stranger1, u_stranger2]

    # Cleanup
    db.users.delete_many({"username": {"$in": all_users}})
    db.posts.delete_many({"username": {"$in": all_users}})
    db.follows.delete_many({"$or": [{"follower": {"$in": all_users}}, {"following": {"$in": all_users}}]})
    db.likes.delete_many({"username": {"$in": all_users}})
    db.stars.delete_many({"username": {"$in": all_users}})
    db.thumbs.delete_many({"username": {"$in": all_users}})
    db.comments.delete_many({"username": {"$in": all_users}})

    # Setup Users
    for u in all_users:
        db.users.insert_one({
            "username": u,
            "password": "hashed_pw",
            "profile_name": f"User {u}",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": f"Bio of {u}"
        })

    # Setup follow graph:
    # u_main follows u_friend
    # u_friend follows u_stranger1 (making u_stranger1 a mutual follow discovery for u_main!)
    # u_stranger2 is followed by nobody initially
    db.follows.insert_one({
        "follower": u_main,
        "following": u_friend,
        "created_at": datetime.now(timezone.utc).isoformat()
    })
    db.follows.insert_one({
        "follower": u_friend,
        "following": u_stranger1,
        "created_at": datetime.now(timezone.utc).isoformat()
    })

    t_main = make_token(secret, u_main)
    t_friend = make_token(secret, u_friend)

    print("=" * 60)
    print("RUNNING EXPLORE & TRENDING SYSTEM TEST SUITE")
    print("=" * 60)

    # ----------------------------------------------------
    # SECTION 1: HASHTAG EXTRACTION UNIT TESTS
    # ----------------------------------------------------
    t1_tags = extract_hashtags("Belajar #Python dan #Flask di #Sweeter!")
    record(1, "Hashtag extraction normal", t1_tags == ["python", "flask", "sweeter"], f"Tags: {t1_tags}")

    t2_tags = extract_hashtags("#Linux #LINUX #linux #DevOps")
    record(2, "Hashtag case-insensitive deduplication", t2_tags == ["linux", "devops"], f"Tags: {t2_tags}")

    t3_tags = extract_hashtags("Visit https://example.com/doc#section and #real_tag")
    record(3, "URL fragment ignored and real hashtag preserved", t3_tags == ["real_tag"], f"Tags: {t3_tags}")

    t4_tags = extract_hashtags("Nothing to extract here #")
    record(4, "Invalid bare hashtag handled", t4_tags == [], f"Tags: {t4_tags}")

    t5_tags = extract_hashtags(None)
    record(5, "None and empty text handled safely", t5_tags == [], f"Tags: {t5_tags}")

    # ----------------------------------------------------
    # SECTION 2: TRENDING SCORE ALGORITHM UNIT TESTS
    # ----------------------------------------------------
    now = datetime.now(timezone.utc)
    # Post A: 1 hour old, 5 hearts, 2 comments -> total = 5*2 + 2*3.5 = 17
    post_a = {
        'count_heart': 5,
        'count_stars': 0,
        'count_thumbs': 0,
        'count_comments': 2,
        'count_repost': 0,
        'is_bookmarked': False,
        'date': (now - timedelta(hours=1)).isoformat()
    }
    score_a = calculate_trending_score(post_a, now=now, period='week')

    # Post B: 1 hour old, 0 hearts, 0 comments -> total = 0
    post_b = {
        'count_heart': 0,
        'count_stars': 0,
        'count_thumbs': 0,
        'count_comments': 0,
        'count_repost': 0,
        'is_bookmarked': False,
        'date': (now - timedelta(hours=1)).isoformat()
    }
    score_b = calculate_trending_score(post_b, now=now, period='week')
    record(6, "Higher engagement has higher score for same age", score_a > score_b, f"Score A: {score_a}, Score B: {score_b}")

    # Post C: 48 hours old with SAME engagement as Post A (17 points)
    post_c = dict(post_a)
    post_c['date'] = (now - timedelta(hours=48)).isoformat()
    score_c = calculate_trending_score(post_c, now=now, period='week')
    record(7, "Recency decay: fresh post scores higher than stale post of equal engagement", score_a > score_c, f"Fresh: {score_a}, Stale: {score_c}")

    # ----------------------------------------------------
    # SECTION 3: TRENDING POSTS INTEGRATION & TIME PERIODS
    # ----------------------------------------------------
    # Insert posts with different dates and hashtags
    # Post 1 (Recent - 2 hours ago): #python #sweeter
    p1 = {
        "username": u_stranger1,
        "profile_name": f"User {u_stranger1}",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "Check out #python and #sweeter today!",
        "date": (now - timedelta(hours=2)).isoformat(),
        "hashtags": ["python", "sweeter"],
        "post_type": "original"
    }
    r1 = db.posts.insert_one(p1)
    p1_id = str(r1.inserted_id)

    # Post 2 (3 days ago - in week, not in today): #linux #devops
    p2 = {
        "username": u_stranger2,
        "profile_name": f"User {u_stranger2}",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "Mastering #linux and #devops configuration",
        "date": (now - timedelta(days=3)).isoformat(),
        "hashtags": ["linux", "devops"],
        "post_type": "original"
    }
    r2 = db.posts.insert_one(p2)
    p2_id = str(r2.inserted_id)

    # Post 3 (20 days ago - in all, not in week): #python
    p3 = {
        "username": u_friend,
        "profile_name": f"User {u_friend}",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "Old archive of #python tips",
        "date": (now - timedelta(days=20)).isoformat(),
        "hashtags": ["python"],
        "post_type": "original"
    }
    r3 = db.posts.insert_one(p3)
    p3_id = str(r3.inserted_id)

    # Add high engagement to Post 1: 3 hearts, 2 comments
    db.likes.insert_many([
        {"post_id": p1_id, "username": u_main, "type": "heart"},
        {"post_id": p1_id, "username": u_friend, "type": "heart"},
        {"post_id": p1_id, "username": u_stranger2, "type": "heart"},
    ])
    db.comments.insert_many([
        {"post_id": p1_id, "username": u_main, "comment": "Great post!", "is_deleted": False, "created_at": now.isoformat()},
        {"post_id": p1_id, "username": u_friend, "comment": "Awesome!", "is_deleted": False, "created_at": now.isoformat()}
    ])

    # Test period=today
    client.set_cookie('mytoken', t_main)
    resp = client.get("/api/trending?period=today")
    d_today = json.loads(resp.data)
    posts_today_ids = [p["_id"] for p in d_today.get("posts", [])]
    record(8, "Trending period=today includes 2h post and excludes 3d post", (p1_id in posts_today_ids) and (p2_id not in posts_today_ids), f"Today IDs: {posts_today_ids}")

    # Test period=week
    resp = client.get("/api/trending?period=week")
    d_week = json.loads(resp.data)
    posts_week_ids = [p["_id"] for p in d_week.get("posts", [])]
    record(9, "Trending period=week includes 2h and 3d posts, excludes 20d post", (p1_id in posts_week_ids) and (p2_id in posts_week_ids) and (p3_id not in posts_week_ids), f"Week IDs: {posts_week_ids}")

    # Test period=all
    resp = client.get("/api/trending?period=all")
    d_all = json.loads(resp.data)
    posts_all_ids = [p["_id"] for p in d_all.get("posts", [])]
    record(10, "Trending period=all includes all three posts", (p1_id in posts_all_ids) and (p2_id in posts_all_ids) and (p3_id in posts_all_ids), f"All IDs: {posts_all_ids}")

    # Test top ranked post in trending is post 1 (highest engagement & recency)
    top_post = d_week.get("posts", [])[0] if d_week.get("posts") else {}
    record(11, "Top post in trending week has highest score", top_post.get("_id") == p1_id, f"Top ID: {top_post.get('_id')}, Score: {top_post.get('trending_score')}")

    # ----------------------------------------------------
    # SECTION 4: DISCOVER PEOPLE (SUGGESTED USERS)
    # ----------------------------------------------------
    # u_main follows u_friend. u_friend follows u_stranger1.
    # Therefore, u_stranger1 should be recommended with "Followed by @test_exp_friend"
    resp = client.get("/api/discover/people")
    d_people = json.loads(resp.data)
    suggested = d_people.get("users", [])
    suggested_names = [u["username"] for u in suggested]

    record(12, "Current user is not in suggested users", u_main not in suggested_names, f"Suggested: {suggested_names}")
    record(13, "Already followed user is not in suggested users", u_friend not in suggested_names, f"Suggested: {suggested_names}")
    
    stranger1_rec = next((u for u in suggested if u["username"] == u_stranger1), None)
    has_mutual_reason = stranger1_rec and ("Followed by @" in stranger1_rec.get("recommendation_reason", "") or "Followed by people" in stranger1_rec.get("recommendation_reason", ""))
    record(14, "Mutual follow recommended with verified reason", bool(has_mutual_reason), f"Stranger1: {stranger1_rec}")

    # Unauthenticated access to /api/discover/people returns 401
    client.delete_cookie('mytoken')
    resp = client.get("/api/discover/people")
    record(15, "Discover people unauthenticated returns 401", resp.status_code == 401, f"Status: {resp.status_code}")

    # ----------------------------------------------------
    # SECTION 5: TRENDING HASHTAGS & TOPIC POSTS
    # ----------------------------------------------------
    # Test GET /api/trending/hashtags
    client.set_cookie('mytoken', t_main)
    resp = client.get("/api/trending/hashtags")
    d_tags = json.loads(resp.data)
    tags_list = d_tags.get("hashtags", [])
    python_tag = next((t for t in tags_list if t["tag"] == "python"), None)
    record(16, "Trending hashtags returns python tag with count >= 2", python_tag is not None and python_tag["post_count"] >= 2, f"Python tag: {python_tag}")

    # Test GET /api/tags/<tag>/posts?sort=latest
    resp = client.get("/api/tags/python/posts?sort=latest")
    d_tag_posts = json.loads(resp.data)
    tag_posts = d_tag_posts.get("posts", [])
    tag_pids = [p["_id"] for p in tag_posts]
    record(17, "Posts by hashtag latest returns both python posts", (p1_id in tag_pids) and (p3_id in tag_pids), f"Tag posts: {tag_pids}")

    # Test GET /api/tags/<tag>/posts?sort=trending
    resp = client.get("/api/tags/python/posts?sort=trending")
    d_tag_trend = json.loads(resp.data)
    trend_tag_posts = d_tag_trend.get("posts", [])
    record(18, "Posts by hashtag trending ranks high-engagement post first", len(trend_tag_posts) > 0 and trend_tag_posts[0]["_id"] == p1_id, f"First: {trend_tag_posts[0]['_id'] if trend_tag_posts else None}")

    # Test invalid hashtag returns 400
    resp = client.get("/api/tags/%20/posts")
    record(19, "Invalid empty hashtag returns 400", resp.status_code == 400, f"Status: {resp.status_code}")

    # ----------------------------------------------------
    # SECTION 6: UNIFIED SEARCH API
    # ----------------------------------------------------
    # 1. Search Users
    resp = client.get(f"/api/search?q={u_stranger2}&type=users")
    d_search_user = json.loads(resp.data)
    found_user = any(u["username"] == u_stranger2 for u in d_search_user.get("users", []))
    record(20, "Search users finds matching username", found_user, f"Users: {d_search_user.get('users')}")

    # 2. Search Posts by keyword
    resp = client.get("/api/search?q=Mastering&type=posts")
    d_search_post = json.loads(resp.data)
    found_post = any(p["_id"] == p2_id for p in d_search_post.get("posts", []))
    record(21, "Search posts finds matching comment content", found_post, f"Posts count: {len(d_search_post.get('posts', []))}")

    # 3. Search Hashtags
    resp = client.get("/api/search?q=%23devops&type=all")
    d_search_tag = json.loads(resp.data)
    found_tag = any(t["tag"] == "devops" for t in d_search_tag.get("hashtags", []))
    record(22, "Search with # hashtag query finds matching tag", found_tag, f"Tags: {d_search_tag.get('hashtags')}")

    # 4. Search input validations: empty returns 400
    resp = client.get("/api/search?q=   ")
    record(23, "Search empty query returns 400", resp.status_code == 400, f"Status: {resp.status_code}")

    # 5. Search oversized query returns 400
    resp = client.get(f"/api/search?q={'x' * 101}")
    record(24, "Search oversized query (>100 chars) returns 400", resp.status_code == 400, f"Status: {resp.status_code}")

    # 6. Regex special characters in search are sanitized
    resp = client.get("/api/search?q=[a-z]+(.*)?")
    record(25, "Search regex special characters handled safely", resp.status_code == 200, f"Status: {resp.status_code}")

    # ----------------------------------------------------
    # SECTION 7: HTML PAGE VIEWS (EXPLORE & TOPIC)
    # ----------------------------------------------------
    # Unauthenticated /explore redirects to login
    client.delete_cookie('mytoken')
    resp = client.get("/explore")
    record(26, "GET /explore unauthenticated redirects to login", resp.status_code == 302, f"Status: {resp.status_code}")

    # Authenticated /explore renders 200 HTML
    client.set_cookie('mytoken', t_main)
    resp = client.get("/explore")
    record(27, "GET /explore authenticated renders 200 HTML", resp.status_code == 200 and b"Explore" in resp.data, f"Status: {resp.status_code}")

    # Unauthenticated /explore/tags/python redirects to login
    client.delete_cookie('mytoken')
    resp = client.get("/explore/tags/python")
    record(28, "GET /explore/tags/<tag> unauthenticated redirects to login", resp.status_code == 302, f"Status: {resp.status_code}")

    # Authenticated /explore/tags/python renders 200 HTML
    client.set_cookie('mytoken', t_main)
    resp = client.get("/explore/tags/python")
    record(29, "GET /explore/tags/<tag> authenticated renders 200 HTML", resp.status_code == 200 and b"#python" in resp.data, f"Status: {resp.status_code}")

    print("=" * 60)
    passed_count = sum(1 for t in test_results if t["status"] == "PASS")
    total_count = len(test_results)
    print(f"RESULTS: {passed_count}/{total_count} tests passed.")
    print("=" * 60)

    # Cleanup test data
    db.users.delete_many({"username": {"$in": all_users}})
    db.posts.delete_many({"username": {"$in": all_users}})
    db.follows.delete_many({"$or": [{"follower": {"$in": all_users}}, {"following": {"$in": all_users}}]})
    db.likes.delete_many({"post_id": {"$in": [p1_id, p2_id, p3_id]}})
    db.comments.delete_many({"post_id": {"$in": [p1_id, p2_id, p3_id]}})

    if passed_count != total_count:
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
