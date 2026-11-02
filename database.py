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
    except Exception as e:
        print(f"Warning saat inisialisasi index MongoDB: {e}")


# Inisialisasi index saat koneksi database disiapkan
init_indexes()


def check_connection():
    """Memeriksa apakah MongoDB dapat dihubungi."""
    client.admin.command("ping")
    return True
