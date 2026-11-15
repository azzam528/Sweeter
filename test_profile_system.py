import os
import json
import jwt
from datetime import datetime, timedelta, timezone
from bson import ObjectId

from app import app
from database import db

test_results = []

def record(test_num, name, passed, details=""):
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {test_num}. {name}: {details}")
    test_results.append({
        "test_num": test_num,
        "name": name,
        "status": status,
        "details": details
    })

def run_tests():
    client = app.test_client()
    secret = app.config["SECRET_KEY"]

    user1 = "test_profile_user1"
    user2 = "test_profile_user2"
    user_empty = "test_profile_empty"

    # Cleanup any old test users
    db.users.delete_many({"username": {"$in": [user1, user2, user_empty]}})
    db.posts.delete_many({"username": {"$in": [user1, user2, user_empty]}})
    db.comments.delete_many({"username": {"$in": [user1, user2, user_empty]}})
    db.likes.delete_many({"username": {"$in": [user1, user2, user_empty]}})

    # Insert test users
    db.users.insert_many([
        {
            "username": user1,
            "password": "hashed_password_1",
            "profile_name": "Profile User One",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Bio of User One"
        },
        {
            "username": user2,
            "password": "hashed_password_2",
            "profile_name": "Profile User Two",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Bio of User Two"
        },
        {
            "username": user_empty,
            "password": "hashed_password_empty",
            "profile_name": "Empty User",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "User with zero posts"
        }
    ])

    token1 = jwt.encode({"id": user1, "exp": datetime.now(timezone.utc) + timedelta(hours=2)}, secret, algorithm="HS256")
    token2 = jwt.encode({"id": user2, "exp": datetime.now(timezone.utc) + timedelta(hours=2)}, secret, algorithm="HS256")

    # Insert test posts
    post1_id = db.posts.insert_one({
        "username": user1,
        "profile_name": "Profile User One",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "User One Post #1",
        "date": datetime.now(timezone.utc).isoformat()
    }).inserted_id

    post2_id = db.posts.insert_one({
        "username": user1,
        "profile_name": "Profile User One",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "User One Post #2",
        "date": datetime.now(timezone.utc).isoformat()
    }).inserted_id

    post_other_id = db.posts.insert_one({
        "username": user2,
        "profile_name": "Profile User Two",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "User Two Post #1",
        "date": datetime.now(timezone.utc).isoformat()
    }).inserted_id

    try:
        # ----------------------------------------------------
        # 1. Membuka profil sendiri
        # ----------------------------------------------------
        client.set_cookie('mytoken', token1)
        res = client.get(f'/user/{user1}')
        html = res.get_data(as_text=True)
        pass1 = (
            res.status_code == 200 and
            "Profile User One" in html and
            "Edit Profile" in html and
            "Bio of User One" in html
        )
        record(1, "Membuka profil sendiri", pass1, f"Status: {res.status_code}, Own profile edit button verified")

        # ----------------------------------------------------
        # 2. Membuka profil pengguna lain
        # ----------------------------------------------------
        res = client.get(f'/user/{user2}')
        html = res.get_data(as_text=True)
        pass2 = (
            res.status_code == 200 and
            "Profile User Two" in html and
            "Edit Profile" not in html and # Edit profile button must NOT appear on other's profile
            "Follow (Coming Soon)" in html
        )
        record(2, "Membuka profil pengguna lain", pass2, f"Status: {res.status_code}, Other profile view without edit button")

        # ----------------------------------------------------
        # 3. Profil dengan banyak postingan
        # ----------------------------------------------------
        res = client.get(f'/get_posts?username_give={user1}')
        data = res.get_json()
        posts = data.get('posts', [])
        pass3 = (
            res.status_code == 200 and
            len(posts) == 2 and
            all(p['username'] == user1 for p in posts)
        )
        record(3, "Profil dengan banyak postingan", pass3, f"Status: {res.status_code}, Fetched {len(posts)} posts for {user1}")

        # ----------------------------------------------------
        # 4. Profil tanpa postingan
        # ----------------------------------------------------
        res = client.get(f'/get_posts?username_give={user_empty}')
        data = res.get_json()
        pass4 = (
            res.status_code == 200 and
            len(data.get('posts', [])) == 0
        )
        record(4, "Profil tanpa postingan", pass4, f"Status: {res.status_code}, Expected 0 posts, received {len(data.get('posts', []))}")

        # ----------------------------------------------------
        # 5. Username tidak ditemukan (404)
        # ----------------------------------------------------
        res = client.get('/user/nonexistent_phantom_user_999')
        html = res.get_data(as_text=True)
        pass5 = (
            res.status_code == 404 and
            "User Not Found" in html
        )
        record(5, "Username tidak ditemukan", pass5, f"Status: {res.status_code}, 404 page rendered correctly")

        # ----------------------------------------------------
        # 6. Postingan hanya milik pengguna terkait
        # ----------------------------------------------------
        res1 = client.get(f'/get_posts?username_give={user1}')
        res2 = client.get(f'/get_posts?username_give={user2}')
        posts1 = res1.get_json().get('posts', [])
        posts2 = res2.get_json().get('posts', [])
        pass6 = (
            all(p['username'] == user1 for p in posts1) and
            all(p['username'] == user2 for p in posts2) and
            not any(p['username'] == user2 for p in posts1)
        )
        record(6, "Postingan hanya milik pengguna terkait", pass6, f"User1 has {len(posts1)} isolated posts, User2 has {len(posts2)} isolated posts")

        # ----------------------------------------------------
        # 7. Navigasi dari feed ke profil
        # ----------------------------------------------------
        with open('templates/post.html', 'r', encoding='utf-8') as f:
            post_html_content = f.read()
        pass7 = (
            "/user/{{ user_info.username | e }}" in post_html_content and
            "/user/${encodeURIComponent(post['username'])}" in open('static/myjs.js', 'r', encoding='utf-8').read()
        )
        record(7, "Navigasi dari feed ke profil", pass7, "Feed contains verified link routes to user profile")

        # ----------------------------------------------------
        # 8. Navigasi dari profil kembali ke feed
        # ----------------------------------------------------
        with open('templates/user_profile.html', 'r', encoding='utf-8') as f:
            profile_html_content = f.read()
        pass8 = (
            'href="/"' in profile_html_content and
            'Home' in profile_html_content and
            'Sweeter' in profile_html_content
        )
        record(8, "Navigasi dari profil kembali ke feed", pass8, "Profile contains verified home feed links")

        # ----------------------------------------------------
        # 9. Komentar dan reaksi pada postingan profil
        # ----------------------------------------------------
        # Reaction test
        res_like = client.post('/update_like', data={
            'post_id_give': str(post1_id),
            'type_give': 'heart',
            'action_give': 'like'
        })
        like_data = res_like.get_json()

        # Comment test
        res_comment = client.post('/create_comment', data={
            'post_id_give': str(post1_id),
            'content_give': 'Komentar pada postingan profil',
            'parent_comment_id_give': ''
        })
        comm_data = res_comment.get_json()
        comment_id = comm_data.get('comment', {}).get('_id')

        # Reply test
        res_reply = client.post('/create_comment', data={
            'post_id_give': str(post1_id),
            'content_give': 'Balasan komentar profil',
            'parent_comment_id_give': comment_id
        })

        # Fetch comments
        res_get_comm = client.get(f'/get_comments?post_id_give={post1_id}')
        comments = res_get_comm.get_json().get('comments', [])

        pass9 = (
            res_like.status_code == 200 and
            like_data.get('result') == 'success' and
            res_comment.status_code == 201 and
            res_reply.status_code == 201 and
            len(comments) == 2
        )
        record(9, "Komentar dan reaksi pada postingan profil", pass9, f"Likes: {like_data.get('count')}, Comments count: {len(comments)}")

        # ----------------------------------------------------
        # 10. Tampilan mobile & responsivitas
        # ----------------------------------------------------
        pass10 = (
            "pl-0 md:pl-[264px]" in profile_html_content and
            "hidden md:flex" in profile_html_content and
            "overflow-x" not in profile_html_content and
            "px-4 md:px-margin-desktop" in profile_html_content
        )
        record(10, "Tampilan mobile & responsivitas", pass10, "Responsive layout classes verified for mobile & desktop")

    finally:
        # Cleanup
        db.users.delete_many({"username": {"$in": [user1, user2, user_empty]}})
        db.posts.delete_many({"_id": {"$in": [post1_id, post2_id, post_other_id]}})
        db.comments.delete_many({"post_id": str(post1_id)})
        db.likes.delete_many({"post_id": str(post1_id)})

    print("\n--- Summary of Session 2 Testing ---")
    all_passed = all(r["status"] == "PASS" for r in test_results)
    print(f"Total Tests: {len(test_results)}, Passed: {sum(1 for r in test_results if r['status'] == 'PASS')}, Failed: {sum(1 for r in test_results if r['status'] == 'FAIL')}")
    if all_passed:
        print("ALL TESTS PASSED SUCCESSFULLY!")
    else:
        print("SOME TESTS FAILED.")

if __name__ == "__main__":
    run_tests()
