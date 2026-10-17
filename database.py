
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


def check_connection():
    """Memeriksa apakah MongoDB dapat dihubungi."""
    client.admin.command("ping")
    return True
