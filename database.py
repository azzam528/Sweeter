import os

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
