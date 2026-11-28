import os
import re
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

MONGODB_CONNECTION_STRING = os.getenv(
    "MONGODB_CONNECTION_STRING"
)
MONGODB_DATABASE = os.getenv(
    "MONGODB_DATABASE",
    "dbsparta_plus_week4",
)

if not MONGODB_CONNECTION_STRING:
    raise RuntimeError(
        "MONGODB_CONNECTION_STRING belum diatur di file .env"
    )

client = MongoClient(
    MONGODB_CONNECTION_STRING,
    serverSelectionTimeoutMS=5000,
)

db = client[MONGODB_DATABASE]


def init_indexes():
    """Membuat index yang diperlukan pada MongoDB secara aman dan idempotent."""
    try:
        # 1. Compound index (post_id, created_at): mempercepat query GET /get_comments yang diurutkan kronologis
        db.comments.create_index([("post_id", 1), ("created_at", 1)])
        # 2. Single index pada post_id untuk filter dan batch aggregation count_comments
        db.comments.create_index([("post_id", 1)])
        # 3. Single index pada parent_comment_id untuk pelacakan child replies
        db.comments.create_index([("parent_comment_id", 1)])
        # 4. Single index pada username untuk pencarian/otorisasi komentar pengguna
        db.comments.create_index([("username", 1)])
        # 5. Single index pada created_at untuk rentang waktu
        db.comments.create_index([("created_at", 1)])
        # 6. Compound unique index pada db.follows: mencegah duplicate follow & race condition
        db.follows.create_index([("follower", 1), ("following", 1)], unique=True)
        # 7. Single index pada following: pencarian cepat daftar follower pengguna
        db.follows.create_index([("following", 1)])
        # 8. Single index pada follower: pencarian cepat akun yang diikuti pengguna
        db.follows.create_index([("follower", 1)])
        # 9. Single index pada date: pengurutan feed kronologis terbaru
        db.posts.create_index([("date", -1)])
        # 10. Compound index pada (username, date): filter efisien timeline & profil
        db.posts.create_index([("username", 1), ("date", -1)])
        # 11. Compound index pada koleksi reaksi untuk batch aggregation cepat
        db.likes.create_index([("post_id", 1), ("type", 1)])
        db.stars.create_index([("post_id", 1), ("type", 1)])
        db.thumbs.create_index([("post_id", 1), ("type", 1)])
        # 12. Compound unique index pada db.bookmarks: mencegah duplikasi bookmark
        db.bookmarks.create_index([("username", 1), ("post_id", 1)], unique=True)
        # 13. Index pada (username, created_at) untuk pengambilan bookmark tersimpan secara kronologis
        db.bookmarks.create_index([("username", 1), ("created_at", -1)])
        # 14. Single index pada post_id untuk pembersihan referensi bookmark saat post dihapus
        db.bookmarks.create_index([("post_id", 1)])
        # 15. Index pada (recipient, created_at) untuk listing notifikasi kronologis terbaru
        db.notifications.create_index([("recipient", 1), ("created_at", -1)])
        # 16. Index pada (recipient, is_read) untuk optimasi penghitungan unread count
        db.notifications.create_index([("recipient", 1), ("is_read", 1)])
        # 17. Compound index untuk pencegahan duplikasi notifikasi (deduplication check)
        db.notifications.create_index([("recipient", 1), ("actor", 1), ("type", 1), ("post_id", 1)])
        # 18. Single index pada post_id untuk cascading cleanup notifikasi saat post dihapus
        db.notifications.create_index([("post_id", 1)])
        # 19. Single index pada original_post_id untuk pencarian relasi repost & quote
        db.posts.create_index([("original_post_id", 1)])
        # 20. Compound unique index untuk mencegah duplikasi repost oleh pengguna yang sama
        db.posts.create_index(
            [("username", 1), ("original_post_id", 1), ("post_type", 1)],
            unique=True,
            partialFilterExpression={"post_type": "repost"}
        )
        # 21. Single index pada hashtags untuk pencarian topik/tag cepat
        db.posts.create_index([("hashtags", 1)])
        # 22. Compound index pada (hashtags, date) untuk feed topik kronologis
        db.posts.create_index([("hashtags", 1), ("date", -1)])
        # 23. Index pada images.filename untuk pengecekan referensi file sebelum penghapusan aman
        db.posts.create_index([("images.filename", 1)])
        # 24. Compound index pada db.drafts (user_id, updated_at, _id) untuk listing draft milik user terbaru
        db.drafts.create_index([("user_id", 1), ("updated_at", -1), ("_id", -1)])
        # 25. Index pada images.filename di db.drafts untuk proteksi shared media saat hapus/edit
        db.drafts.create_index([("images.filename", 1)])
        # 26. Unique index (user_id, client_draft_key) untuk mencegah duplikasi draft akibat race/double-click
        db.drafts.create_index(
            [("user_id", 1), ("client_draft_key", 1)],
            unique=True,
            partialFilterExpression={"client_draft_key": {"$type": "string"}}
        )
        # 27. Index untuk Communities / Circles
        db.communities.create_index([("slug", 1)], unique=True)
        db.communities.create_index([("visibility", 1), ("member_count", -1), ("created_at", -1)])
        db.communities.create_index([("creator_id", 1)])
        db.communities.create_index([("profile_image.filename", 1)])
        db.communities.create_index([("cover_image.filename", 1)])
        # 28. Index untuk Community Membership
        db.community_members.create_index([("community_id", 1), ("user_id", 1)], unique=True)
        db.community_members.create_index([("user_id", 1), ("joined_at", -1)])
        db.community_members.create_index([("community_id", 1), ("role", 1)])
        # 29. Index untuk Community Requests & Invites
        db.community_requests.create_index([("community_id", 1), ("user_id", 1), ("status", 1)])
        db.community_requests.create_index([("user_id", 1), ("status", 1), ("created_at", -1)])
        # 30. Compound index postingan komunitas
        db.posts.create_index([("community_id", 1), ("date", -1)])
        # 31. Compound unique index pada db.blocks: mencegah duplicate block
        db.blocks.create_index([("blocker", 1), ("blocked", 1)], unique=True)
        db.blocks.create_index([("blocker", 1)])
        db.blocks.create_index([("blocked", 1)])
        # 32. Compound unique index pada db.mutes: mencegah duplicate mute
        db.mutes.create_index([("muter", 1), ("muted", 1)], unique=True)
        db.mutes.create_index([("muter", 1)])
        db.mutes.create_index([("muted", 1)])
        # 33. Indexes untuk db.reports
        db.reports.create_index([("status", 1), ("created_at", -1)])
        db.reports.create_index([("reporter_id", 1), ("target_id", 1), ("status", 1)])
        db.reports.create_index([("target_type", 1), ("target_id", 1)])
        db.reports.create_index([("created_at", -1)])
        # 34. Indexes untuk db.moderation_logs (Audit Trail)
        db.moderation_logs.create_index([("created_at", -1)])
        db.moderation_logs.create_index([("admin_id", 1), ("created_at", -1)])
        # 35. Unique index pada username di db.users
        db.users.create_index([("username", 1)], unique=True)
        # 36. Sparse unique index pada email di db.users
        db.users.create_index([("email", 1)], unique=True, sparse=True)
        db.users.create_index([("is_deactivated", 1)])
        db.users.create_index([("is_private", 1)])
        # 37. Indexes untuk db.follow_requests
        db.follow_requests.create_index([("requester", 1), ("target", 1)], unique=True)
        db.follow_requests.create_index([("target", 1), ("status", 1), ("created_at", -1)])
        db.follow_requests.create_index([("requester", 1), ("status", 1)])
        # 38. Index untuk privasi post di db.posts
        db.posts.create_index([("privacy", 1)])
    except Exception as e:
        print(f"Warning saat inisialisasi index MongoDB: {e}")


# Inisialisasi index saat koneksi database disiapkan
init_indexes()


def check_connection():
    """Memeriksa apakah MongoDB dapat dihubungi."""
    client.admin.command("ping")
    return True


def is_following(follower: str, following: str) -> bool:
    """Memeriksa apakah follower sedang mengikuti following."""
    if not follower or not following:
        return False
    return bool(db.follows.find_one({"follower": follower, "following": following}))


def count_followers(username: str) -> int:
    """Menghitung jumlah follower dari seorang pengguna."""
    if not username:
        return 0
    return db.follows.count_documents({"following": username})


def count_following(username: str) -> int:
    """Menghitung jumlah akun yang diikuti oleh seorang pengguna."""
    if not username:
        return 0
    return db.follows.count_documents({"follower": username})


def is_bookmarked(username: str, post_id: str) -> bool:
    """Memeriksa apakah postingan telah di-bookmark oleh pengguna."""
    if not username or not post_id:
        return False
    return bool(db.bookmarks.find_one({"username": username, "post_id": str(post_id)}))


def count_bookmarks(username: str) -> int:
    """Menghitung jumlah postingan yang di-bookmark oleh seorang pengguna."""
    if not username:
        return 0
    return db.bookmarks.count_documents({"username": username})


def is_reposted(username: str, post_id: str) -> bool:
    """Memeriksa apakah pengguna telah merepost postingan ini."""
    if not username or not post_id:
        return False
    return bool(db.posts.find_one({
        'username': username,
        'original_post_id': str(post_id),
        'post_type': 'repost'
    }))


def count_reposts(post_id: str) -> int:
    """Menghitung jumlah repost dari suatu postingan."""
    if not post_id:
        return 0
    return db.posts.count_documents({
        'original_post_id': str(post_id),
        'post_type': 'repost'
    })


def extract_hashtags(text: str) -> list:
    """
    Mengekstrak hashtag dari teks secara aman dan case-insensitive:
    - Hanya mencocokkan hashtag valid: diawali '#', diikuti 1-50 karakter alfanumerik / underscore.
    - Menghindari potongan URL (seperti http://site.com/#section atau href="#anchor").
    - Mengembalikan list string lowercase unik tanpa simbol '#' (misal: ['linux', 'python']).
    """
    if not text or not isinstance(text, str):
        return []

    # Hapus URL dari teks terlebih dahulu agar fragmen URL '#foo' tidak dianggap hashtag
    cleaned_text = re.sub(r'https?://[^\s]+', '', text)

    # Ekstrak hashtag: diawali awal string atau bukan karakter alfanumerik/slash
    pattern = r'(?:^|[^\w/])#([a-zA-Z0-9_]{1,50})\b'
    matches = re.findall(pattern, cleaned_text)

    # Deduplikasi case-insensitive dengan mempertahankan urutan kemunculan pertama
    seen = set()
    result = []
    for tag in matches:
        tag_lower = tag.lower()
        if tag_lower not in seen:
            seen.add(tag_lower)
            result.append(tag_lower)
    return result


def get_trending_hashtags(limit: int = 10, period: str = 'all') -> list:
    """
    Mengambil daftar hashtag terpopuler berdasarkan jumlah kemunculan postingan.
    Mendukung filter periode: 'today', 'week', 'all'.
    """
    match_filter = {
        'hashtags': {'$exists': True, '$ne': []},
        'post_type': {'$ne': 'repost'},
        'community_visibility': {'$ne': 'private'}
    }
    if period == 'today':
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        match_filter['date'] = {'$gte': cutoff}
    elif period == 'week':
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        match_filter['date'] = {'$gte': cutoff}

    try:
        pipeline = [
            {'$match': match_filter},
            {'$unwind': '$hashtags'},
            {'$match': {'hashtags': {'$ne': ''}}},
            {'$group': {
                '_id': '$hashtags',
                'post_count': {'$sum': 1}
            }},
            {'$sort': {'post_count': -1}},
            {'$limit': max(1, min(limit, 50))}
        ]
        results = []
        for doc in db.posts.aggregate(pipeline):
            tag_name = doc['_id']
            results.append({
                'tag': tag_name,
                'display_tag': f"#{tag_name}",
                'post_count': doc['post_count'],
                'url': f"/explore/tags/{tag_name}"
            })
        return results
    except Exception as e:
        print(f"Error in get_trending_hashtags: {e}")
        return []


def get_suggested_users(current_username: str, limit: int = 5) -> list:
    """
    Menghasilkan rekomendasi pengguna (Discover People) untuk current_username:
    1. Pengguna yang diikuti oleh orang-orang yang di-follow current_username (mutual follow).
    2. Pengguna dengan jumlah follower terbanyak (Popular on Sweeter).
    3. Pengguna aktif lainnya di db.users.
    Pengecualian mutlak: current_username dan pengguna yang sudah di-follow.
    """
    if not current_username:
        return []

    my_following = set(db.follows.distinct('following', {'follower': current_username}))
    blocked_related = get_all_blocked_relation_usernames(current_username)
    muted_users = set(get_muted_usernames(current_username))
    exclude_users = my_following | {current_username} | blocked_related | muted_users

    suggested = []
    seen_usernames = set()

    # 1. Rekomendasi Mutual Follows (Followed by people you follow)
    if my_following:
        try:
            mutual_cursor = db.follows.aggregate([
                {'$match': {
                    'follower': {'$in': list(my_following)},
                    'following': {'$nin': list(exclude_users)}
                }},
                {'$group': {
                    '_id': '$following',
                    'count': {'$sum': 1},
                    'mutual_followers': {'$addToSet': '$follower'}
                }},
                {'$sort': {'count': -1}},
                {'$limit': limit}
            ])
            for item in mutual_cursor:
                u_name = item['_id']
                if u_name not in seen_usernames and u_name not in exclude_users:
                    user_doc = db.users.find_one({'username': u_name})
                    if user_doc:
                        seen_usernames.add(u_name)
                        mutuals = item.get('mutual_followers', [])
                        if len(mutuals) == 1:
                            reason = f"Followed by @{mutuals[0]}"
                        elif len(mutuals) > 1:
                            reason = f"Followed by @{mutuals[0]} and {len(mutuals)-1} other{'s' if len(mutuals)>2 else ''}"
                        else:
                            reason = "Followed by people you follow"

                        follower_count = count_followers(u_name)
                        suggested.append({
                            'username': u_name,
                            'profile_name': user_doc.get('profile_name', u_name),
                            'profile_pic_real': user_doc.get('profile_pic_real', 'profile_pics/profile_placeholder.png'),
                            'profile_info': user_doc.get('profile_info', ''),
                            'follower_count': follower_count,
                            'recommendation_reason': reason,
                            'is_following': False
                        })
                        if len(suggested) >= limit:
                            return suggested
        except Exception as e:
            print(f"Error computing mutual follow recommendations: {e}")

    # 2. Rekomendasi Akun Populer (Popular on Sweeter)
    try:
        popular_cursor = db.follows.aggregate([
            {'$match': {'following': {'$nin': list(exclude_users | seen_usernames)}}},
            {'$group': {'_id': '$following', 'follower_count': {'$sum': 1}}},
            {'$sort': {'follower_count': -1}},
            {'$limit': limit * 2}
        ])
        for item in popular_cursor:
            u_name = item['_id']
            if u_name not in seen_usernames and u_name not in exclude_users:
                user_doc = db.users.find_one({'username': u_name})
                if user_doc:
                    seen_usernames.add(u_name)
                    follower_count = item['follower_count']
                    suggested.append({
                        'username': u_name,
                        'profile_name': user_doc.get('profile_name', u_name),
                        'profile_pic_real': user_doc.get('profile_pic_real', 'profile_pics/profile_placeholder.png'),
                        'profile_info': user_doc.get('profile_info', ''),
                        'follower_count': follower_count,
                        'recommendation_reason': f"Popular on Sweeter · {follower_count} followers",
                        'is_following': False
                    })
                    if len(suggested) >= limit:
                        return suggested
    except Exception as e:
        print(f"Error computing popular user recommendations: {e}")

    # 3. Fallback: Pengguna aktif lain di db.users
    if len(suggested) < limit:
        remaining_needed = limit - len(suggested)
        other_users = db.users.find(
            {'username': {'$nin': list(exclude_users | seen_usernames)}},
            {'_id': 0, 'username': 1, 'profile_name': 1, 'profile_pic_real': 1, 'profile_info': 1}
        ).limit(remaining_needed)
        for u in other_users:
            u_name = u['username']
            follower_count = count_followers(u_name)
            suggested.append({
                'username': u_name,
                'profile_name': u.get('profile_name', u_name),
                'profile_pic_real': u.get('profile_pic_real', 'profile_pics/profile_placeholder.png'),
                'profile_info': u.get('profile_info', ''),
                'follower_count': follower_count,
                'recommendation_reason': "Active on Sweeter",
                'is_following': False
            })

    return suggested


def is_blocked(blocker: str, blocked: str) -> bool:
    """Memeriksa apakah blocker memblokir blocked."""
    if not blocker or not blocked:
        return False
    return bool(db.blocks.find_one({"blocker": blocker, "blocked": blocked}))


def has_block_relation(user_a: str, user_b: str) -> bool:
    """Memeriksa apakah terdapat relasi block dua arah (A blokir B ATAU B blokir A)."""
    if not user_a or not user_b:
        return False
    if user_a == user_b:
        return False
    return bool(db.blocks.find_one({
        "$or": [
            {"blocker": user_a, "blocked": user_b},
            {"blocker": user_b, "blocked": user_a}
        ]
    }))


def get_blocked_usernames(username: str) -> list:
    """Mengambil daftar username yang diblokir oleh pengguna."""
    if not username:
        return []
    return db.blocks.distinct("blocked", {"blocker": username})


def get_blocking_usernames(username: str) -> list:
    """Mengambil daftar username yang memblokir pengguna ini."""
    if not username:
        return []
    return db.blocks.distinct("blocker", {"blocked": username})


def get_all_blocked_relation_usernames(username: str) -> set:
    """Mengambil himpunan username yang diblokir ATAU memblokir pengguna ini."""
    if not username:
        return set()
    blocked = set(db.blocks.distinct("blocked", {"blocker": username}))
    blockers = set(db.blocks.distinct("blocker", {"blocked": username}))
    return blocked | blockers


def is_muted(muter: str, muted: str) -> bool:
    """Memeriksa apakah muter telah me-mute muted."""
    if not muter or not muted:
        return False
    return bool(db.mutes.find_one({"muter": muter, "muted": muted}))


def get_muted_usernames(username: str) -> list:
    """Mengambil daftar username yang di-mute oleh pengguna."""
    if not username:
        return []
    return db.mutes.distinct("muted", {"muter": username})


def is_private_account(username: str) -> bool:
    """Memeriksa apakah akun pengguna diatur sebagai akun privat."""
    if not username:
        return False
    user = db.users.find_one({"username": username}, {"is_private": 1})
    return bool(user and user.get("is_private") is True)


def is_account_deactivated(username: str) -> bool:
    """Memeriksa apakah akun pengguna sedang dinonaktifkan (deactivated)."""
    if not username:
        return False
    user = db.users.find_one({"username": username}, {"is_deactivated": 1})
    return bool(user and user.get("is_deactivated") is True)


def has_pending_follow_request(requester: str, target: str) -> bool:
    """Memeriksa apakah ada permintaan follow yang masih berstatus pending."""
    if not requester or not target:
        return False
    return bool(db.follow_requests.find_one({
        "requester": requester,
        "target": target,
        "status": "pending"
    }))


def get_pending_follow_requests_count(target: str) -> int:
    """Menghitung jumlah permintaan follow pending untuk target pengguna."""
    if not target:
        return 0
    return db.follow_requests.count_documents({
        "target": target,
        "status": "pending"
    })


def can_view_user_content(viewer: str, target_author: str) -> bool:
    """
    Menentukan apakah viewer berhak melihat konten target_author:
    1. Viewer adalah pemilik akun itu sendiri -> True
    2. Terdapat relasi blokir -> False
    3. Target author berstatus dinonaktifkan (deactivated) -> False
    4. Target author akun privat -> Hanya jika viewer adalah follower yang disetujui
    5. Target author akun publik -> True
    """
    if not target_author:
        return False
    if viewer and viewer == target_author:
        return True
    if viewer and has_block_relation(viewer, target_author):
        return False
    if is_account_deactivated(target_author):
        return False
    if is_private_account(target_author):
        if not viewer:
            return False
        return is_following(viewer, target_author)
    return True


