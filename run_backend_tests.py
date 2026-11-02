import sys
import json
import jwt
from datetime import datetime, timedelta, timezone
from bson import ObjectId

from app import app
from database import db

# Warna output terminal
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

results = []

def record_test(name, passed, note=""):
    status = "PASS" if passed else "FAIL"
    color = GREEN if passed else RED
    print(f"[{color}{status}{RESET}] {name}: {note}")
    results.append({
        "scenario": name,
        "status": status,
        "note": note
    })

def run_all_tests():
    client = app.test_client()
    secret = app.config["SECRET_KEY"]

    # 1. Setup Test Users
    user_a = "__test_user_a__"
    user_b = "__test_user_b__"

    db.users.delete_many({"username": {"$in": [user_a, user_b]}})
    db.users.insert_many([
        {
            "username": user_a,
            "password": "hashed_password_a",
            "profile_name": "Test User Alpha",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Tester A"
        },
        {
            "username": user_b,
            "password": "hashed_password_b",
            "profile_name": "Test User Beta",
            "profile_pic": "",
            "profile_pic_real": "profile_pics/profile_placeholder.png",
            "profile_info": "Tester B"
        }
    ])

    token_a = jwt.encode(
        {"id": user_a, "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        secret,
        algorithm="HS256"
    )
    token_b = jwt.encode(
        {"id": user_b, "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        secret,
        algorithm="HS256"
    )

    # 2. Setup Test Post 1 & Post 2
    post_res1 = db.posts.insert_one({
        "username": user_a,
        "profile_name": "Test User Alpha",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "Postingan Utama untuk Testing Komentar",
        "date": datetime.now(timezone.utc).isoformat()
    })
    test_post_id = str(post_res1.inserted_id)

    post_res2 = db.posts.insert_one({
        "username": user_b,
        "profile_name": "Test User Beta",
        "profile_pic_real": "profile_pics/profile_placeholder.png",
        "comment": "Postingan Kedua untuk Testing Isolasi Post",
        "date": datetime.now(timezone.utc).isoformat()
    })
    test_post_id_2 = str(post_res2.inserted_id)

    created_comment_ids = []

    try:
        # ----------------------------------------------------
        # Test 1: Membuat komentar utama (Root Comment)
        # ----------------------------------------------------
        client.set_cookie("mytoken", token_a)
        res = client.post("/create_comment", data={
            "post_id_give": test_post_id,
            "content_give": "Ini komentar utama pertama dari User A",
            "parent_comment_id_give": ""
        })
        data = res.get_json()
        root_comment_id = data.get("comment", {}).get("_id") if data else None
        if root_comment_id:
            created_comment_ids.append(root_comment_id)
        t1_pass = (
            res.status_code == 201 and
            data.get("result") == "success" and
            data.get("comment", {}).get("parent_comment_id") is None and
            data.get("comment", {}).get("username") == user_a
        )
        record_test("1. Membuat komentar utama", t1_pass, f"Status: {res.status_code}, Comment ID: {root_comment_id}")

        # ----------------------------------------------------
        # Test 2: Membuat reply terhadap komentar utama
        # ----------------------------------------------------
        client.set_cookie("mytoken", token_b)
        res = client.post("/create_comment", data={
            "post_id_give": test_post_id,
            "content_give": "Ini balasan tingkat 1 dari User B",
            "parent_comment_id_give": root_comment_id
        })
        data = res.get_json()
        reply_b_id = data.get("comment", {}).get("_id") if data else None
        if reply_b_id:
            created_comment_ids.append(reply_b_id)
        t2_pass = (
            res.status_code == 201 and
            data.get("result") == "success" and
            data.get("comment", {}).get("parent_comment_id") == root_comment_id and
            data.get("comment", {}).get("username") == user_b
        )
        record_test("2. Membuat reply", t2_pass, f"Status: {res.status_code}, Parent ID: {root_comment_id}")

        # ----------------------------------------------------
        # Test 3: Membuat nested reply (balasan ke balasan)
        # ----------------------------------------------------
        client.set_cookie("mytoken", token_a)
        res = client.post("/create_comment", data={
            "post_id_give": test_post_id,
            "content_give": "Ini nested reply dari User A membalas User B",
            "parent_comment_id_give": reply_b_id
        })
        data = res.get_json()
        nested_reply_id = data.get("comment", {}).get("_id") if data else None
        if nested_reply_id:
            created_comment_ids.append(nested_reply_id)
        t3_pass = (
            res.status_code == 201 and
            data.get("result") == "success" and
            data.get("comment", {}).get("parent_comment_id") == reply_b_id and
            data.get("comment", {}).get("username") == user_a
        )
        record_test("3. Membuat nested reply", t3_pass, f"Status: {res.status_code}, Nested Parent ID: {reply_b_id}")

        # ----------------------------------------------------
        # Test 4: Mengambil komentar berdasarkan post
        # ----------------------------------------------------
        client.set_cookie("mytoken", token_a)
        res = client.get(f"/get_comments?post_id_give={test_post_id}")
        data = res.get_json()
        comments = data.get("comments", []) if data else []
        t4_pass = (
            res.status_code == 200 and
            data.get("result") == "success" and
            len(comments) == 3 and
            comments[0]["_id"] == root_comment_id and
            comments[0]["is_mine"] is True and
            comments[1]["is_mine"] is False  # Milik User B dilihat oleh User A
        )
        record_test("4. Mengambil komentar berdasarkan post", t4_pass, f"Status: {res.status_code}, Total komentar: {len(comments)}, is_mine terverifikasi")

        # ----------------------------------------------------
        # Test 5: Mengambil komentar yang memiliki banyak reply
        # ----------------------------------------------------
        # Tambah satu reply lagi dari User B ke root
        client.set_cookie("mytoken", token_b)
        res_more = client.post("/create_comment", data={
            "post_id_give": test_post_id,
            "content_give": "Balasan kedua dari User B untuk root",
            "parent_comment_id_give": root_comment_id
        })
        more_id = res_more.get_json().get("comment", {}).get("_id")
        if more_id:
            created_comment_ids.append(more_id)

        res = client.get(f"/get_comments?post_id_give={test_post_id}")
        comments = res.get_json().get("comments", [])
        t5_pass = (
            res.status_code == 200 and
            len(comments) == 4
        )
        record_test("5. Mengambil komentar yang memiliki banyak reply", t5_pass, f"Status: {res.status_code}, Berhasil mengambil 4 komentar bertingkat")

        # ----------------------------------------------------
        # Test 6: Menghapus komentar milik sendiri (Soft Delete)
        # ----------------------------------------------------
        # User B menghapus komentarnya sendiri (reply_b_id)
        client.set_cookie("mytoken", token_b)
        res = client.post("/delete_comment", data={
            "comment_id_give": reply_b_id
        })
        data = res.get_json()
        # Periksa kondisi di DB
        doc_in_db = db.comments.find_one({"_id": ObjectId(reply_b_id)})
        # Periksa get_comments
        res_get = client.get(f"/get_comments?post_id_give={test_post_id}")
        comm_b = [c for c in res_get.get_json().get("comments", []) if c["_id"] == reply_b_id][0]

        t6_pass = (
            res.status_code == 200 and
            data.get("result") == "success" and
            doc_in_db.get("is_deleted") is True and
            comm_b.get("is_deleted") is True and
            comm_b.get("content") == "[Komentar ini telah dihapus oleh pengguna]"
        )
        record_test("6. Menghapus komentar milik sendiri", t6_pass, f"Status: {res.status_code}, is_deleted=True, konten tersamarkan")

        # ----------------------------------------------------
        # Test 7: Mencoba menghapus komentar pengguna lain
        # ----------------------------------------------------
        # User B mencoba menghapus komentar milik User A (root_comment_id)
        client.set_cookie("mytoken", token_b)
        res = client.post("/delete_comment", data={
            "comment_id_give": root_comment_id
        })
        data = res.get_json()
        t7_pass = (
            res.status_code == 403 and
            data.get("result") == "fail"
        )
        record_test("7. Mencoba menghapus komentar pengguna lain", t7_pass, f"Status: {res.status_code}, Pesan: {data.get('msg')}")

        # ----------------------------------------------------
        # Test 8: Mengakses endpoint tanpa JWT
        # ----------------------------------------------------
        client.delete_cookie("mytoken")
        res_create_no_jwt = client.post("/create_comment", data={"post_id_give": test_post_id, "content_give": "test"})
        res_get_no_jwt = client.get(f"/get_comments?post_id_give={test_post_id}")
        res_del_no_jwt = client.post("/delete_comment", data={"comment_id_give": root_comment_id})

        t8_pass = (
            res_create_no_jwt.status_code == 401 and
            res_get_no_jwt.status_code == 401 and
            res_del_no_jwt.status_code == 401 and
            res_create_no_jwt.get_json().get("result") == "fail"
        )
        record_test("8. Mengakses endpoint tanpa JWT", t8_pass, f"Status Code: {res_create_no_jwt.status_code}, {res_get_no_jwt.status_code}, {res_del_no_jwt.status_code} (All 401 JSON)")

        # ----------------------------------------------------
        # Test 9: Mengirim komentar kosong atau hanya spasi
        # ----------------------------------------------------
        client.set_cookie("mytoken", token_a)
        res = client.post("/create_comment", data={
            "post_id_give": test_post_id,
            "content_give": "   \n\t  "
        })
        data = res.get_json()
        t9_pass = (
            res.status_code == 400 and
            data.get("result") == "fail"
        )
        record_test("9. Mengirim komentar kosong", t9_pass, f"Status: {res.status_code}, Pesan: {data.get('msg')}")

        # ----------------------------------------------------
        # Test 10: Mengirim post ID yang tidak valid atau tidak ditemukan
        # ----------------------------------------------------
        res_invalid_format = client.post("/create_comment", data={
            "post_id_give": "invalid-format-id",
            "content_give": "Valid content"
        })
        non_existent_oid = str(ObjectId())
        res_not_found = client.post("/create_comment", data={
            "post_id_give": non_existent_oid,
            "content_give": "Valid content"
        })
        t10_pass = (
            res_invalid_format.status_code == 400 and
            res_not_found.status_code == 404
        )
        record_test("10. Mengirim post ID tidak valid atau tidak ditemukan", t10_pass, f"Invalid format: {res_invalid_format.status_code}, Not found: {res_not_found.status_code}")

        # ----------------------------------------------------
        # Test 11: Mengirim parent comment yang bukan bagian dari post tersebut
        # ----------------------------------------------------
        # Coba reply di post 2, tapi parent_comment_id merujuk ke post 1
        res = client.post("/create_comment", data={
            "post_id_give": test_post_id_2,
            "content_give": "Komentar di post 2 membalas post 1",
            "parent_comment_id_give": root_comment_id
        })
        data = res.get_json()
        t11_pass = (
            res.status_code == 400 and
            data.get("result") == "fail" and
            "belong" in data.get("msg", "").lower()
        )
        record_test("11. Mengirim parent comment bukan bagian dari post tersebut", t11_pass, f"Status: {res.status_code}, Pesan: {data.get('msg')}")

        # ----------------------------------------------------
        # Test 12: Memastikan comment counter menghitung komentar dan reply
        # ----------------------------------------------------
        # Di post 1 ada 4 komentar total
        client.set_cookie("mytoken", token_a)
        res = client.get("/get_posts")
        posts = res.get_json().get("posts", [])
        post1_data = [p for p in posts if p["_id"] == test_post_id][0]
        post2_data = [p for p in posts if p["_id"] == test_post_id_2][0]

        t12_pass = (
            res.status_code == 200 and
            post1_data.get("count_comments") == 4 and
            post2_data.get("count_comments") == 0
        )
        record_test("12. Memastikan comment counter menghitung komentar dan reply", t12_pass, f"Post 1 count_comments={post1_data.get('count_comments')} (expected 4), Post 2 count_comments={post2_data.get('count_comments')} (expected 0)")

        # ----------------------------------------------------
        # Test 13: Memastikan soft-deleted comment tetap dihitung
        # ----------------------------------------------------
        # Karena reply_b_id sudah di-soft-delete di Test 6, total count harus tetap 4
        t13_pass = (post1_data.get("count_comments") == 4)
        record_test("13. Memastikan soft-deleted comment tetap dihitung", t13_pass, f"Count tetap 4 meskipun ada komentar berstatus is_deleted: true")

        # ----------------------------------------------------
        # Test 14: Memastikan endpoint lama dan fitur reaction tetap berjalan
        # ----------------------------------------------------
        # Test toggle like di test_post_id
        res_like = client.post("/update_like", data={
            "post_id_give": test_post_id,
            "type_give": "heart",
            "action_give": "like"
        })
        like_data = res_like.get_json()
        # Verify di get_posts
        res_get_posts = client.get("/get_posts")
        post1_after_like = [p for p in res_get_posts.get_json().get("posts", []) if p["_id"] == test_post_id][0]

        t14_pass = (
            res_like.status_code == 200 and
            like_data.get("result") == "success" and
            post1_after_like.get("count_heart") == 1 and
            post1_after_like.get("heart_by_me") is True and
            "count_stars" in post1_after_like and
            "count_thumbs" in post1_after_like
        )
        record_test("14. Memastikan endpoint lama dan fitur reaction tetap berjalan", t14_pass, f"Update like status: {res_like.status_code}, heart_by_me={post1_after_like.get('heart_by_me')}, count_heart={post1_after_like.get('count_heart')}")

    finally:
        # CLEANUP: Hapus hanya data uji yang dibuat untuk testing ini
        print("\n--- Cleaning up test data ---")
        del_users = db.users.delete_many({"username": {"$in": [user_a, user_b]}})
        del_posts = db.posts.delete_many({"_id": {"$in": [ObjectId(test_post_id), ObjectId(test_post_id_2)]}})
        del_comments = db.comments.delete_many({"post_id": {"$in": [test_post_id, test_post_id_2]}})
        del_likes = db.likes.delete_many({"post_id": {"$in": [test_post_id, test_post_id_2]}})
        print(f"Cleanup done: {del_users.deleted_count} users, {del_posts.deleted_count} posts, {del_comments.deleted_count} comments, {del_likes.deleted_count} likes removed.")

    all_passed = all(r["status"] == "PASS" for r in results)
    print(f"\nFinal Test Summary: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    return all_passed

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)

