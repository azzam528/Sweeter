import os
import re
import uuid
import json
import time
import threading
import hashlib
import jwt
import warnings
from collections import defaultdict, deque

from datetime import datetime, timedelta, timezone
from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from flask import (
    Flask,
    render_template,
    jsonify,
    request,
    redirect,
    url_for,
    send_from_directory,
    abort,
)

from werkzeug.utils import secure_filename
from dotenv import load_dotenv

from database import (
    db,
    check_connection,
    is_reposted,
    count_reposts,
    is_following,
    count_followers,
    count_following,
    extract_hashtags,
    get_trending_hashtags,
    get_suggested_users,
    is_blocked,
    has_block_relation,
    get_blocked_usernames,
    get_blocking_usernames,
    get_all_blocked_relation_usernames,
    is_muted,
    get_muted_usernames,
    is_private_account,
    is_account_deactivated,
    has_pending_follow_request,
    get_pending_follow_requests_count,
    can_view_user_content,
)
from notifications import (
    create_notification,
    get_unread_count,
    get_user_notifications,
    mark_notification_as_read,
    mark_all_notifications_as_read,
    cleanup_notifications_for_post,
)

load_dotenv()

app = Flask(__name__)

app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["UPLOAD_FOLDER"] = "./static/profile_pics"

# Konfigurasi Upload Media Postingan & Komunitas (dapat dikonfigurasi via env var untuk deployment/Docker)
DEFAULT_POST_UPLOAD_DIR = os.path.join(app.root_path, "uploads", "posts")
app.config["POST_UPLOAD_DIR"] = os.path.abspath(
    os.getenv("POST_UPLOAD_DIR") or DEFAULT_POST_UPLOAD_DIR
)
DEFAULT_COMMUNITY_UPLOAD_DIR = os.path.join(app.root_path, "uploads", "communities")
app.config["COMMUNITY_UPLOAD_DIR"] = os.path.abspath(
    os.getenv("COMMUNITY_UPLOAD_DIR") or DEFAULT_COMMUNITY_UPLOAD_DIR
)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB total request limit (4 x 5MB + overhead)

ALLOWED_POST_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
ALLOWED_POST_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}
MAX_POST_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB per gambar
MAX_POST_IMAGES_COUNT = 4
SAFE_POST_IMAGE_FILENAME_RE = re.compile(r"^[a-f0-9]{32}_[0-9]+\.(?:jpg|jpeg|png|webp)$")

# ==========================================
# CONFIGURATION & SECRET_KEY VALIDATION
# ==========================================

APP_ENV = os.getenv("APP_ENV", "development").lower()
IS_PRODUCTION = (APP_ENV == "production")

SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    raise RuntimeError(
        "CRITICAL: SECRET_KEY belum diatur di file .env. Aplikasi tidak dapat dijalankan tanpa SECRET_KEY."
    )

SECRET_KEY_BYTES = SECRET_KEY.encode("utf-8")
if len(SECRET_KEY_BYTES) < 32:
    warning_msg = (
        f"[SECURITY WARNING] Panjang SECRET_KEY ({len(SECRET_KEY_BYTES)} byte) kurang dari 32 byte "
        "yang direkomendasikan RFC 7518 Section 3.2 untuk HMAC-SHA256 (HS256). "
        "Untuk keamanan maksimal di production, silakan rotasi SECRET_KEY ke minimal 32 byte (256-bit). "
        "Panduan rotasi tersedia di docs/security-configuration.md."
    )
    if IS_PRODUCTION:
        raise RuntimeError(
            "CRITICAL: Pada environment production (APP_ENV=production), SECRET_KEY wajib memiliki panjang minimal 32 byte (256-bit)."
        )
    else:
        warnings.warn(warning_msg, UserWarning, stacklevel=2)

app.config["SECRET_KEY"] = SECRET_KEY


def get_current_user_from_token():
    """
    Mengambil dan memvalidasi token JWT dari cookie 'mytoken'.
    Mengembalikan payload dict jika valid, atau None jika tidak valid/kadaluarsa/hilang/suspended/deactivated/token_version mismatch.
    """
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return None
    try:
        payload = jwt.decode(
            token_receive,
            SECRET_KEY,
            algorithms=['HS256']
        )
        username = payload.get('id')
        if username:
            user_doc = db.users.find_one(
                {'username': username},
                {'is_suspended': 1, 'is_deactivated': 1, 'token_version': 1}
            )
            if not user_doc:
                return None
            if user_doc.get('is_suspended') is True:
                return None
            if user_doc.get('is_deactivated') is True:
                return None
            token_ver = payload.get('token_version')
            if token_ver is not None and token_ver < user_doc.get('token_version', 1):
                return None
        return payload
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError, Exception):
        return None


ADMIN_USERS_ENV = os.getenv("ADMIN_USERS", "admin").lower()
ADMIN_USERNAMES = {u.strip() for u in ADMIN_USERS_ENV.split(",") if u.strip()}


def is_admin_user(username: str) -> bool:
    """
    Memeriksa otorisasi platform administrator secara aman pada sisi server:
    1. Konfigurasi env var ADMIN_USERS (misal: 'admin,azzam')
    2. Flag database: is_admin: True atau role: 'admin' pada koleksi db.users
    Tidak pernah mempercayai parameter input atau klaim sepihak dari frontend.
    """
    if not username or not isinstance(username, str):
        return False
    username_clean = username.strip()
    if username_clean.lower() in ADMIN_USERNAMES:
        return True
    user_doc = db.users.find_one(
        {"username": username_clean},
        {"is_admin": 1, "role": 1, "is_suspended": 1}
    )
    if not user_doc or user_doc.get("is_suspended") is True:
        return False
    return bool(user_doc.get("is_admin") is True or user_doc.get("role") == "admin")


# Lanjutkan dengan seluruh route lama kamu di bawah ini.
# Jangan menghapus route yang sudah ada.


@app.route('/', methods=['GET'])
def home():
    token_receive = request.cookies.get('mytoken')
    try:
        payload = jwt.decode(
            token_receive,
            SECRET_KEY,
            algorithms=['HS256']
        )
        user_info = db.users.find_one({'username' : payload.get('id')})
        return render_template('post.html', user_info=user_info)
    except jwt.ExpiredSignatureError:
        msg ='your token has expired'
        return redirect(url_for('login', msg=msg))
    except (jwt.exceptions.DecodeError, jwt.exceptions.InvalidTokenError, jwt.exceptions.PyJWTError):
        msg ='There was a problem logging you in'
        return redirect(url_for('login', msg=msg))

@app.route('/old', methods=['GET'])
def old_home():
    """Route cadangan (backup) ke template index.html lama selama proses migrasi."""
    token_receive = request.cookies.get('mytoken')
    try:
        payload = jwt.decode(
            token_receive,
            SECRET_KEY,
            algorithms=['HS256']
        )
        user_info = db.users.find_one({'username' : payload.get('id')})
        return render_template('index.html', user_info=user_info)
    except jwt.ExpiredSignatureError:
        msg ='your token has expired'
        return redirect(url_for('login', msg=msg))
    except (jwt.exceptions.DecodeError, jwt.exceptions.InvalidTokenError, jwt.exceptions.PyJWTError):
        msg ='There was a problem logging you in'
        return redirect(url_for('login', msg=msg))
    
@app.route('/login',methods=['GET'])
def login():
    msg = request.args.get('msg')
    return render_template('login.html', msg=msg)

@app.route('/user/<username>', methods=['GET'])
def user(username):
    token_receive = request.cookies.get('mytoken')
    try:
        payload = jwt.decode(
            token_receive,
            SECRET_KEY,
            algorithms=['HS256']
        )
        current_username = payload.get('id')
        current_user = db.users.find_one(
            {'username': current_username},
            {'_id': False}
        )
        user_info = db.users.find_one(
            {'username': username},
            {'_id': False}
        )
        if not user_info:
            return render_template('404.html', username=username, current_user=current_user), 404

        status = (username == current_username)
        is_blocked_by_me = is_blocked(current_username, username) if not status else False
        is_blocking_me = is_blocked(username, current_username) if not status else False
        is_muted_by_me = is_muted(current_username, username) if not status else False
        is_admin = is_admin_user(current_username)
        is_deactivated = bool(user_info.get('is_deactivated'))

        # Jika akun target dinonaktifkan dan bukan pemilik sendiri atau admin, isolasi akses
        if is_deactivated and not status and not is_admin:
            return render_template('404.html', username=username, current_user=current_user), 404

        is_following = bool(db.follows.find_one({'follower': current_username, 'following': username}))
        has_requested = bool(db.follow_requests.find_one({'requester': current_username, 'target': username, 'status': 'pending'})) if not status else False
        is_private = bool(user_info.get('is_private'))
        is_private_locked = bool(is_private and not status and not is_following)

        show_follow_counts = bool(user_info.get('show_follow_counts', True))
        show_bio = bool(user_info.get('show_bio', True))

        post_count = 0 if (is_blocked_by_me or is_blocking_me or is_deactivated) else db.posts.count_documents({'username': username})
        follower_count = db.follows.count_documents({'following': username})
        following_count = db.follows.count_documents({'follower': username})
        return render_template(
            'user_profile.html',
            user_info=user_info,
            current_user=current_user,
            status=status,
            post_count=post_count,
            is_following=is_following,
            has_requested=has_requested,
            is_private=is_private,
            is_private_locked=is_private_locked,
            show_follow_counts=show_follow_counts,
            show_bio=show_bio,
            follower_count=follower_count,
            following_count=following_count,
            is_blocked_by_me=is_blocked_by_me,
            is_blocking_me=is_blocking_me,
            is_muted_by_me=is_muted_by_me,
            is_admin=is_admin
        )
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return redirect(url_for('home'))

@app.route('/user_old/<username>', methods=['GET'])
def old_user(username):
    """Route cadangan (backup) ke template user.html lama selama proses migrasi."""
    token_receive = request.cookies.get('mytoken')
    try:
        payload = jwt.decode(
            token_receive,
            SECRET_KEY,
            algorithms=['HS256']
        )
        user_info = db.users.find_one(
            {'username': username},
            {'_id': False}
        )
        if not user_info:
            return render_template('404.html', username=username), 404

        status = (username == payload.get('id'))
        is_following = bool(db.follows.find_one({'follower': payload.get('id'), 'following': username}))
        follower_count = db.follows.count_documents({'following': username})
        following_count = db.follows.count_documents({'follower': username})
        return render_template(
            'user.html',
            user_info=user_info,
            status=status,
            is_following=is_following,
            follower_count=follower_count,
            following_count=following_count
        )
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return redirect(url_for('home'))
    
@app.route("/sign_in", methods=["POST"])
def sign_in():
    # Sign in
    username_receive = request.form["username_give"]
    password_receive = request.form["password_give"]
    pw_hash = hashlib.sha256(password_receive.encode("utf-8")).hexdigest()
    result = db.users.find_one(
        {
            "username": username_receive,
            "password": pw_hash,
        }
    )
    if result:
        if result.get("is_suspended") is True:
            return jsonify(
                {
                    "result": "fail",
                    "msg": "Akun Anda telah ditangguhkan (suspended) oleh administrator karena pelanggaran aturan komunitas.",
                }
            ), 403

        # Jika akun sebelumnya dinonaktifkan sementara (deactivated), aktifkan kembali secara otomatis
        was_reactivated = False
        if result.get("is_deactivated") is True:
            db.users.update_one(
                {"username": username_receive},
                {"$set": {"is_deactivated": False, "deactivated_at": None}}
            )
            was_reactivated = True

        token_ver = result.get('token_version', 1)
        payload = {
            "id": username_receive,
            "token_version": token_ver,
            # token berlaku 24 jam (menggunakan datetime timezone UTC standar)
            "exp": datetime.now(timezone.utc) + timedelta(seconds=60 * 60 * 24),
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")

        response = jsonify(
            {
                "result": "success",
                "token": token,
                "was_reactivated": was_reactivated,
                "msg": "Welcome back! Your account has been reactivated." if was_reactivated else "Sign in successful"
            }
        )
        # Menetapkan cookie di response dengan SameSite=Lax dan secure sesuai environment
        response.set_cookie(
            "mytoken",
            token,
            max_age=60 * 60 * 24,
            path="/",
            samesite="Lax",
            secure=IS_PRODUCTION
        )
        return response
    # Let's also handle the case where the id and
    # password combination cannot be found
    else:
        return jsonify(
            {
                "result": "fail",
                "msg": "We could not find a user with that id/password combination",
            }
        )

@app.route("/sign_out", methods=["POST", "GET"])
def sign_out():
    """Endpoint logout server-side untuk menghapus cookie 'mytoken'."""
    response = redirect(url_for("login"))
    response.delete_cookie("mytoken", path="/")
    return response

@app.route("/sign_up/save", methods=["POST"])
def sign_up():
    username_receive = request.form['username_give']
    password_receive = request.form['password_give']
    password_hash = hashlib.sha256(password_receive.encode('utf-8')).hexdigest()
    now_iso = datetime.now(timezone.utc).isoformat()
    doc = {
        "username": username_receive,                               # id
        "password": password_hash,                                  # password
        "profile_name": username_receive,                           # user's name is set to their id by default
        "profile_pic": "",                                          # profile image file name
        "profile_pic_real": "profile_pics/profile_placeholder.png", # a default profile image
        "profile_info": "",                                         # a profile description
        "email": "",
        "is_private": False,
        "show_follow_counts": True,
        "show_bio": True,
        "default_post_privacy": "public",
        "is_deactivated": False,
        "token_version": 1,
        "created_at": now_iso
    }
    db.users.insert_one(doc)
    return jsonify({'result': 'success'})

@app.route('/sign_up/check_dup', methods=['POST'])
def check_dup():
    username_receive = request.form.get('username_give')
    user = db.users.find_one({'username': username_receive})
    print(user)
    exist = bool(user)
    return jsonify({'result':'success', 'exists' : exist})



@app.route('/update_profile', methods=['POST'])
def update_profile():
    token_receive = request.cookies.get('mytoken')
    try:
        # Dekode token dan ambil payload
        payload = jwt.decode(
            token_receive,
            SECRET_KEY,
            algorithms=['HS256']
        )
        username = payload.get('id')
        
        # Ambil data dari form
        name_receive = request.form.get('name_give')
        about_receive = request.form.get('about_give')
        
        # Data yang akan diupdate
        new_doc = {
            'profile_name': name_receive,
            'profile_info': about_receive,
        }
        
        # Periksa apakah ada file yang diupload
        if 'file_give' in request.files:
            file = request.files['file_give']
            if file.filename != '':
                # Membuat nama file yang lebih unik dengan timestamp
                extension = file.filename.split('.')[-1]
                filename = f"{username}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{extension}"
                file_path = f'/profile_pics/{filename}'
                
                # Pastikan folder untuk menyimpan file ada
                if not os.path.exists('./static/profile_pics'):
                    os.makedirs('./static/profile_pics')
                
                # Simpan file
                file.save('../static/' + file_path)
                
                # Update data gambar
                new_doc['profile_pic'] = filename
                new_doc['profile_pic_real'] = file_path
        
        # Update data di database
        db.users.update_one(
            {'username': username},
            {'$set': new_doc}
        )

        # Mengirimkan respon sukses
        return jsonify({
            'result': 'success',
            'msg': 'Your profile has been updated'
        })
    
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        # Jika token tidak valid atau sudah kadaluarsa, redirect ke home
        return redirect(url_for('home'))

@app.errorhandler(413)
def handle_request_entity_too_large(e):
    """Menangani payload upload yang melebihi batas MAX_CONTENT_LENGTH."""
    return jsonify({
        'result': 'fail',
        'msg': 'Total ukuran file yang diunggah melebihi batas maksimal server (25 MB).'
    }), 413


def _detect_image_format(data: bytes):
    """
    Memeriksa magic bytes konten file gambar secara murni (tanpa dependensi eksternal).
    Mengembalikan tuple (allowed_extensions_set, canonical_mime) atau None jika tidak valid.
    """
    if not data:
        return None
    # JPEG: FF D8 FF
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return ({"jpg", "jpeg"}, "image/jpeg")
    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return ({"png"}, "image/png")
    # WebP: RIFF....WEBP
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ({"webp"}, "image/webp")
    return None


def validate_post_image_file(file_storage):
    """
    Memvalidasi satu file gambar yang diunggah untuk postingan:
    - Nama file tidak kosong dan bebas dari komponen path traversal.
    - Ekstensi termasuk JPG, JPEG, PNG, atau WebP.
    - MIME type (jika dikirim) valid dan sesuai.
    - Ukuran > 0 byte dan <= 5 MB.
    - Konten magic bytes benar-benar gambar yang valid dan cocok dengan ekstensinya.
    Mengembalikan (data_bytes, ext, canonical_mime, size_bytes) atau melempar ValueError.
    """
    if not file_storage:
        raise ValueError("File gambar tidak ditemukan.")

    raw_filename = file_storage.filename or ""
    if not raw_filename.strip():
        raise ValueError("Nama file gambar tidak boleh kosong.")

    # Tolak upaya path traversal atau separator path mentah
    if ".." in raw_filename or "/" in raw_filename or "\\" in raw_filename or "\x00" in raw_filename:
        raise ValueError("Nama file tidak valid (karakter path tidak diizinkan).")

    cleaned_name = secure_filename(raw_filename)
    if not cleaned_name or "." not in cleaned_name:
        raise ValueError("Nama file atau ekstensi tidak valid.")

    ext = cleaned_name.rsplit(".", 1)[1].lower()
    if ext not in ALLOWED_POST_IMAGE_EXTENSIONS:
        raise ValueError("Format gambar tidak didukung. Gunakan JPG, JPEG, PNG, atau WebP.")

    declared_mime = (file_storage.mimetype or "").lower().strip()
    if declared_mime and declared_mime != "application/octet-stream":
        if declared_mime not in ALLOWED_POST_IMAGE_MIMES:
            raise ValueError("Tipe MIME gambar tidak didukung.")

    stream = file_storage.stream
    stream.seek(0)
    data = stream.read(MAX_POST_IMAGE_SIZE + 1)
    if not data or len(data) == 0:
        raise ValueError("File gambar kosong tidak diizinkan.")
    if len(data) > MAX_POST_IMAGE_SIZE:
        raise ValueError("Ukuran gambar melebihi batas maksimal 5 MB per gambar.")

    detected = _detect_image_format(data)
    if not detected:
        raise ValueError("Konten file bukan gambar JPG, PNG, atau WebP yang valid.")

    valid_exts, canonical_mime = detected
    if ext not in valid_exts:
        raise ValueError("Konten file tidak cocok dengan ekstensi gambar yang dipilih.")

    if declared_mime and declared_mime != "application/octet-stream" and declared_mime != canonical_mime:
        raise ValueError("Tipe MIME tidak sesuai dengan konten gambar.")

    return data, ext, canonical_mime, len(data)


def _save_validated_image_to_dir(data: bytes, ext: str, mime_type: str, target_dir: str, url_prefix: str) -> dict:
    """Helper terpusat untuk menyimpan byte gambar tervalidasi ke direktori target secara aman."""
    upload_dir = os.path.abspath(target_dir)
    os.makedirs(upload_dir, exist_ok=True)

    ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    filename = f"{uuid.uuid4().hex}_{ts_ms}.{ext}"
    if not SAFE_POST_IMAGE_FILENAME_RE.match(filename):
        raise ValueError("Gagal membuat nama file yang aman.")

    target_path = os.path.abspath(os.path.join(upload_dir, filename))
    if os.path.commonpath([upload_dir, target_path]) != upload_dir:
        raise ValueError("Path penyimpanan tidak valid.")

    with open(target_path, "wb") as f:
        f.write(data)

    return {
        "filename": filename,
        "url": f"{url_prefix}/{filename}",
        "mime_type": mime_type,
        "size": len(data),
    }


def save_post_image_file(data: bytes, ext: str, mime_type: str) -> dict:
    """Menyimpan byte gambar tervalidasi ke POST_UPLOAD_DIR dengan nama unik yang aman."""
    return _save_validated_image_to_dir(data, ext, mime_type, app.config["POST_UPLOAD_DIR"], "/uploads/posts")


def save_community_image_file(data: bytes, ext: str, mime_type: str) -> dict:
    """Menyimpan gambar profil atau sampul komunitas tervalidasi ke COMMUNITY_UPLOAD_DIR."""
    return _save_validated_image_to_dir(data, ext, mime_type, app.config["COMMUNITY_UPLOAD_DIR"], "/uploads/communities")


def _safe_remove_image_file(filename: str, target_dir: str) -> bool:
    """Menghapus file gambar secara aman dari target_dir setelah memastikan tidak ada path traversal."""
    if not filename or not isinstance(filename, str) or not SAFE_POST_IMAGE_FILENAME_RE.match(filename):
        return False
    upload_dir = os.path.abspath(target_dir)
    target_path = os.path.abspath(os.path.join(upload_dir, filename))
    if os.path.commonpath([upload_dir, target_path]) != upload_dir:
        return False
    try:
        if os.path.isfile(target_path):
            os.remove(target_path)
            return True
    except OSError:
        return False
    return False


def safe_delete_post_image(filename: str, check_db_refs: bool = True) -> bool:
    """
    Menghapus file gambar post dari disk secara aman setelah memvalidasi nama file
    dan memastikan tidak ada dokumen postingan/draft lain yang masih mereferensikannya.
    """
    if not filename or not isinstance(filename, str) or not SAFE_POST_IMAGE_FILENAME_RE.match(filename):
        return False

    if check_db_refs:
        try:
            if db.posts.count_documents({"images.filename": filename}) > 0:
                return False
            if hasattr(db, "drafts") and db.drafts.count_documents({"images.filename": filename}) > 0:
                return False
        except Exception:
            return False

    return _safe_remove_image_file(filename, app.config["POST_UPLOAD_DIR"])


def safe_delete_community_image(filename: str, check_db_refs: bool = True) -> bool:
    """Menghapus gambar profil/sampul komunitas dari disk jika tidak lagi direferensikan."""
    if not filename or not isinstance(filename, str) or not SAFE_POST_IMAGE_FILENAME_RE.match(filename):
        return False

    if check_db_refs:
        try:
            if hasattr(db, "communities") and db.communities.count_documents({
                "$or": [
                    {"profile_image.filename": filename},
                    {"cover_image.filename": filename},
                ]
            }) > 0:
                return False
        except Exception:
            return False

    return _safe_remove_image_file(filename, app.config["COMMUNITY_UPLOAD_DIR"])


def _serve_safe_upload_dir(filename: str, target_dir: str):
    """Menyajikan file upload gambar secara aman dengan validasi regex dan pencegahan path traversal."""
    if not filename or not SAFE_POST_IMAGE_FILENAME_RE.match(filename):
        abort(404)

    upload_dir = os.path.abspath(target_dir)
    target_path = os.path.abspath(os.path.join(upload_dir, filename))
    if os.path.commonpath([upload_dir, target_path]) != upload_dir:
        abort(404)
    if not os.path.isfile(target_path):
        abort(404)

    response = send_from_directory(upload_dir, filename)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.route('/uploads/posts/<path:filename>', methods=['GET'])
def serve_post_image(filename):
    """Menyajikan gambar postingan secara aman tanpa membuka akses ke direktori lain."""
    return _serve_safe_upload_dir(filename, app.config["POST_UPLOAD_DIR"])


@app.route('/uploads/communities/<path:filename>', methods=['GET'])
def serve_community_image(filename):
    """Menyajikan gambar profil dan sampul komunitas secara aman."""
    return _serve_safe_upload_dir(filename, app.config["COMMUNITY_UPLOAD_DIR"])


def _get_community_membership(community_id: str, username: str):
    """Mengambil dokumen keanggotaan user pada suatu komunitas jika ada."""
    if not community_id or not username or not hasattr(db, "community_members"):
        return None
    return db.community_members.find_one({"community_id": str(community_id), "user_id": username})


def _is_community_member(community_id: str, username: str) -> bool:
    """Memeriksa apakah user terdaftar sebagai anggota komunitas."""
    return bool(_get_community_membership(community_id, username))


def _get_user_joined_community_ids(username: str) -> list:
    """Mengembalikan daftar community_id yang diikuti user."""
    if not username or not hasattr(db, "community_members"):
        return []
    return list(db.community_members.distinct("community_id", {"user_id": username}))


def _user_can_access_post(post_doc: dict, current_username: str = None) -> bool:
    """
    Memastikan postingan dapat diakses oleh current_username:
    1. Pemilik postingan (author) selalu berhak mengakses postingannya sendiri.
    2. Postingan dari akun yang memiliki relasi block (diblokir atau memblokir) tidak dapat diakses.
    3. Postingan dari akun yang dinonaktifkan (deactivated) tidak dapat diakses.
    4. Post privacy:
       - 'private': hanya pemilik postingan yang boleh mengakses.
       - 'followers': hanya author dan follower yang disetujui (is_following).
       - 'public': dapat diakses publik (asalkan akun author bukan akun privat).
    5. Account privacy: jika akun author adalah akun privat, hanya author dan follower yang disetujui.
    6. Community privacy: jika post berada dalam komunitas private, hanya anggota komunitas yang dapat mengakses.
    """
    if not post_doc:
        return False

    post_author = post_doc.get("username")
    # Pemilik postingan selalu berhak mengakses kontennya sendiri
    if current_username and post_author == current_username:
        return True

    # Isolasi relasi block dua arah
    if current_username and post_author:
        if has_block_relation(current_username, post_author):
            return False

    # Isolasi akun author yang dinonaktifkan
    if is_account_deactivated(post_author):
        return False

    # Pemeriksaan Privasi Postingan
    post_privacy = post_doc.get("privacy", "public")
    if post_privacy == "private":
        return False
    elif post_privacy == "followers":
        if not current_username or not is_following(current_username, post_author):
            return False

    # Pemeriksaan Privasi Akun Pembuat Postingan
    if is_private_account(post_author):
        if not current_username or not is_following(current_username, post_author):
            return False

    # Pemeriksaan Privasi Komunitas
    if post_doc.get("community_visibility") == "private":
        cid = post_doc.get("community_id")
        if not cid or not current_username:
            return False
        return _is_community_member(str(cid), current_username)

    return True


def _private_post_visibility_filter(current_username: str = None) -> dict:
    """Filter MongoDB untuk menyaring postingan komunitas private agar hanya terlihat oleh anggotanya."""
    joined_ids = _get_user_joined_community_ids(current_username) if current_username else []
    if joined_ids:
        return {
            "$or": [
                {"community_visibility": {"$ne": "private"}},
                {"community_id": {"$in": joined_ids}},
            ]
        }
    return {"community_visibility": {"$ne": "private"}}


def _create_published_post(user_info: dict, comment_text: str, date_str: str = None, images_list: list = None, community: dict = None, privacy: str = 'public') -> dict:
    """
    Helper terpusat untuk memvalidasi dan membuat dokumen postingan publik/komunitas di db.posts.
    Digunakan bersama oleh POST /posting, POST /api/communities/<id>/posts, dan POST /api/drafts/<draft_id>/publish.
    """
    comment_clean = (comment_text or '').strip() if isinstance(comment_text, str) else ''
    images_clean = list(images_list) if isinstance(images_list, list) else []

    if not comment_clean and len(images_clean) == 0:
        raise ValueError('Isi postingan atau minimal satu gambar wajib diisi.')

    if len(comment_clean) > 5000:
        raise ValueError('Panjang postingan melebihi batas maksimal 5000 karakter.')

    if len(images_clean) > MAX_POST_IMAGES_COUNT:
        raise ValueError(f'Maksimal {MAX_POST_IMAGES_COUNT} gambar dalam satu postingan.')

    privacy_clean = privacy if privacy in ('public', 'followers', 'private') else 'public'
    final_date = date_str or datetime.now(timezone.utc).isoformat()
    tags = extract_hashtags(comment_clean)
    doc = {
        'username': user_info.get('username'),
        'profile_name': user_info.get('profile_name'),
        'profile_pic_real': user_info.get('profile_pic_real'),
        'comment': comment_clean,
        'date': final_date,
        'hashtags': tags,
        'images': images_clean,
        'privacy': privacy_clean,
    }
    if community:
        doc['community_id'] = str(community['_id'])
        doc['community_name'] = community.get('name', '')
        doc['community_slug'] = community.get('slug', '')
        doc['community_visibility'] = community.get('visibility', 'public')

    res = db.posts.insert_one(doc)
    doc['_id'] = str(res.inserted_id)
    return doc


@app.route('/posting', methods=['POST'])
def posting():
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401
    try:
        payload = jwt.decode(
            token_receive,
            SECRET_KEY,
            algorithms=['HS256']
        )
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    user_info = db.users.find_one({'username': payload.get('id')})
    if not user_info:
        return jsonify({'result': 'fail', 'msg': 'User profile not found'}), 404

    comment_receive = (request.form.get('comment_give') or request.form.get('comment') or '').strip()
    date_receive = request.form.get('date_give') or datetime.now(timezone.utc).isoformat()
    privacy_receive = (request.form.get('privacy_give') or request.form.get('privacy') or user_info.get('default_post_privacy') or 'public').strip().lower()

    # Cek target komunitas opsional (jika user memposting ke dalam Circle/Community)
    community_id_raw = (request.form.get('community_id') or '').strip()
    community_slug_raw = (request.form.get('community_slug') or '').strip().lower()
    target_community = None
    if community_id_raw or community_slug_raw:
        if not hasattr(db, 'communities'):
            return jsonify({'result': 'fail', 'msg': 'Community module unavailable'}), 500
        if community_id_raw:
            if not ObjectId.is_valid(community_id_raw):
                return jsonify({'result': 'fail', 'msg': 'Invalid community ID format'}), 400
            target_community = db.communities.find_one({'_id': ObjectId(community_id_raw)})
        else:
            target_community = db.communities.find_one({'slug': community_slug_raw})
        if not target_community:
            return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404
        if not _is_community_member(str(target_community['_id']), user_info.get('username')):
            return jsonify({'result': 'fail', 'msg': 'Anda harus bergabung dengan komunitas ini untuk membuat postingan di dalamnya.'}), 403

    raw_files = []
    for key in ('images', 'images[]', 'file_give'):
        raw_files.extend(request.files.getlist(key))

    if len(raw_files) > MAX_POST_IMAGES_COUNT:
        return jsonify({
            'result': 'fail',
            'msg': f'Maksimal {MAX_POST_IMAGES_COUNT} gambar dalam satu postingan.'
        }), 400

    if not comment_receive and len(raw_files) == 0:
        return jsonify({
            'result': 'fail',
            'msg': 'Isi postingan atau minimal satu gambar wajib diisi.'
        }), 400

    if len(comment_receive) > 5000:
        return jsonify({
            'result': 'fail',
            'msg': 'Panjang postingan melebihi batas maksimal 5000 karakter.'
        }), 400

    # Validasi seluruh gambar terlebih dahulu sebelum menulis file ke disk (atomik)
    validated_payloads = []
    for file_item in raw_files:
        try:
            validated_payloads.append(validate_post_image_file(file_item))
        except ValueError as err:
            return jsonify({'result': 'fail', 'msg': str(err)}), 400

    saved_images = []
    try:
        for data_bytes, ext, mime_type, _size in validated_payloads:
            saved_images.append(save_post_image_file(data_bytes, ext, mime_type))

        created_doc = _create_published_post(
            user_info=user_info,
            comment_text=comment_receive,
            date_str=date_receive,
            images_list=saved_images,
            community=target_community,
            privacy=privacy_receive,
        )
        return jsonify({
            'result': 'success',
            'msg': 'Posting successfull',
            'post_id': created_doc['_id'],
            'images': saved_images,
            'community_id': created_doc.get('community_id'),
        })
    except ValueError as verr:
        for img in saved_images:
            safe_delete_post_image(img.get('filename'), check_db_refs=False)
        return jsonify({'result': 'fail', 'msg': str(verr)}), 400
    except Exception as exc:
        # Rollback file yang sudah ditulis jika terjadi kegagalan simpan/database
        for img in saved_images:
            safe_delete_post_image(img.get('filename'), check_db_refs=False)
        return jsonify({
            'result': 'fail',
            'msg': f'Gagal menyimpan postingan: {exc}'
        }), 500    
    
    
def enrich_posts_metadata(posts, current_username, is_profile_view_for=None):
    """
    Memperkaya daftar dokumen post dengan:
    1. Batch comments count
    2. Batch reactions count (heart, stars, thumbs)
    3. Status reaksi pengguna saat ini (heart_by_me, stars_by_me, thumbs_by_me)
    4. Status bookmark pengguna saat ini (is_bookmarked)
    5. Status repost pengguna saat ini (repost_by_me)
    6. Batch repost count (count_repost)
    7. Hak akses kepemilikan postingan (is_mine)
    8. Indikator editan postingan (is_edited)
    9. Resolusi postingan asli untuk repost dan quote posts (original_post)
    """
    post_ids = [str(post['_id']) for post in posts]

    # Kumpulkan juga ID postingan asli dari postingan quote / repost
    orig_post_ids = [
        str(post['original_post_id'])
        for post in posts
        if post.get('original_post_id') and ObjectId.is_valid(post.get('original_post_id'))
    ]
    all_query_ids = list(set(post_ids + orig_post_ids))

    comment_counts = {}
    heart_counts = {}
    stars_counts = {}
    thumbs_counts = {}
    repost_counts = {}
    my_hearts = set()
    my_stars = set()
    my_thumbs = set()
    my_bookmarks = set()
    my_reposts = set()
    orig_posts_map = {}

    if all_query_ids:
        # 1. Ambil dokumen postingan asli untuk repost dan quote
        if orig_post_ids:
            orig_obj_ids = [ObjectId(pid) for pid in set(orig_post_ids)]
            for op in db.posts.find({'_id': {'$in': orig_obj_ids}}):
                orig_posts_map[str(op['_id'])] = op

        # 2. Batch count komentar
        pipeline_comments = [
            {'$match': {'post_id': {'$in': all_query_ids}}},
            {'$group': {'_id': '$post_id', 'count': {'$sum': 1}}}
        ]
        for item in db.comments.aggregate(pipeline_comments):
            comment_counts[item['_id']] = item['count']

        # 3. Batch count reactions
        for item in db.likes.aggregate([
            {'$match': {'post_id': {'$in': all_query_ids}, 'type': 'heart'}},
            {'$group': {'_id': '$post_id', 'count': {'$sum': 1}}}
        ]):
            heart_counts[item['_id']] = item['count']

        for item in db.stars.aggregate([
            {'$match': {'post_id': {'$in': all_query_ids}, 'type': 'stars'}},
            {'$group': {'_id': '$post_id', 'count': {'$sum': 1}}}
        ]):
            stars_counts[item['_id']] = item['count']

        for item in db.thumbs.aggregate([
            {'$match': {'post_id': {'$in': all_query_ids}, 'type': 'thumbs'}},
            {'$group': {'_id': '$post_id', 'count': {'$sum': 1}}}
        ]):
            thumbs_counts[item['_id']] = item['count']

        # 4. Batch count reposts
        for item in db.posts.aggregate([
            {'$match': {'original_post_id': {'$in': all_query_ids}, 'post_type': 'repost'}},
            {'$group': {'_id': '$original_post_id', 'count': {'$sum': 1}}}
        ]):
            repost_counts[item['_id']] = item['count']

        # 5. Status pengguna saat ini
        if current_username:
            my_hearts = set(db.likes.distinct('post_id', {
                'post_id': {'$in': all_query_ids},
                'type': 'heart',
                'username': current_username
            }))
            my_stars = set(db.stars.distinct('post_id', {
                'post_id': {'$in': all_query_ids},
                'type': 'stars',
                'username': current_username
            }))
            my_thumbs = set(db.thumbs.distinct('post_id', {
                'post_id': {'$in': all_query_ids},
                'type': 'thumbs',
                'username': current_username
            }))
            my_bookmarks = set(db.bookmarks.distinct('post_id', {
                'post_id': {'$in': all_query_ids},
                'username': current_username
            }))
            my_reposts = set(db.posts.distinct('original_post_id', {
                'original_post_id': {'$in': all_query_ids},
                'post_type': 'repost',
                'username': current_username
            }))

    # Enrich original posts map first so nested objects have full counts
    for opid, op in orig_posts_map.items():
        op['_id'] = opid
        op['count_heart'] = heart_counts.get(opid, 0)
        op['heart_by_me'] = opid in my_hearts
        op['count_stars'] = stars_counts.get(opid, 0)
        op['stars_by_me'] = opid in my_stars
        op['count_thumbs'] = thumbs_counts.get(opid, 0)
        op['thumbs_by_me'] = opid in my_thumbs
        op['count_comments'] = comment_counts.get(opid, 0)
        op['count_repost'] = repost_counts.get(opid, 0)
        op['repost_by_me'] = opid in my_reposts
        op['is_bookmarked'] = opid in my_bookmarks
        op['is_mine'] = (op.get('username') == current_username)
        op['is_edited'] = bool(op.get('is_edited', False))
        op['images'] = op.get('images') if isinstance(op.get('images'), list) else []

    for post in posts:
        pid = str(post['_id'])
        post['_id'] = pid
        post['post_type'] = post.get('post_type', 'original')
        post['count_heart'] = heart_counts.get(pid, 0)
        post['heart_by_me'] = pid in my_hearts
        post['count_stars'] = stars_counts.get(pid, 0)
        post['stars_by_me'] = pid in my_stars
        post['count_thumbs'] = thumbs_counts.get(pid, 0)
        post['thumbs_by_me'] = pid in my_thumbs
        post['count_comments'] = comment_counts.get(pid, 0)
        post['count_repost'] = repost_counts.get(pid, 0)
        post['repost_by_me'] = pid in my_reposts
        post['is_bookmarked'] = pid in my_bookmarks
        post['is_mine'] = (post.get('username') == current_username)
        post['is_edited'] = bool(post.get('is_edited', False))
        post['images'] = post.get('images') if isinstance(post.get('images'), list) else []
        post['hashtags'] = post.get('hashtags') if ('hashtags' in post and post.get('hashtags') is not None) else extract_hashtags(post.get('comment', ''))

        if post.get('original_post_id'):
            orig_id = str(post['original_post_id'])
            if orig_id in orig_posts_map:
                post['original_post'] = dict(orig_posts_map[orig_id])
            else:
                post['original_post'] = {'_id': orig_id, 'is_deleted': True}
        else:
            post['original_post'] = None

    # Filter konsistensi visibilitas untuk akun yang diblokir & di-mute
    if current_username:
        blocked_related = get_all_blocked_relation_usernames(current_username)
        muted_users = set(get_muted_usernames(current_username))
        cleaned_posts = []
        for post in posts:
            p_author = post.get('username')
            # Jika author postingan ini diblokir / memblokir, lewati
            if p_author in blocked_related:
                continue

            # Jika author di-mute, hanya sembunyikan jika BUKAN sedang melihat profil spesifik author tersebut
            if p_author in muted_users and p_author != is_profile_view_for:
                continue

            # Pemeriksaan hak akses postingan (Post Privacy, Account Privacy, Status Deaktif, Komunitas)
            if not _user_can_access_post(post, current_username):
                continue

            # Jika merupakan repost, periksa author postingan asli
            if post.get('post_type') == 'repost':
                orig_p = post.get('original_post')
                if orig_p:
                    orig_author = orig_p.get('username')
                    if orig_author in blocked_related or (orig_author in muted_users and orig_author != is_profile_view_for):
                        continue
            # Jika merupakan quote, samarkan konten asli jika author asli diblokir
            elif post.get('post_type') == 'quote':
                orig_p = post.get('original_post')
                if orig_p and orig_p.get('username') in blocked_related:
                    post['original_post'] = {'_id': orig_p.get('_id'), 'is_deleted': True}

            cleaned_posts.append(post)
        posts = cleaned_posts

    return posts


@app.route('/get_posts', methods=['GET'])
def get_posts():
    """
    Endpoint pengambilan feed postingan dengan dukungan:
    1. Personalized Timeline (Following + Current User) secara default
    2. Explore / Global feed (tab=explore)
    3. Profile feed (username_give=<username>)
    4. Keyset Cursor Pagination (cursor=<last_post_id>, limit=20)
    5. Batch Aggregation tanpa N+1 query loop
    """
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(
            token_receive,
            SECRET_KEY,
            algorithms=['HS256']
        )
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    # 1. Parsing & validasi parameter
    username_receive = (request.args.get('username_give') or request.args.get('username') or '').strip()
    tab = request.args.get('tab', '').lower().strip()
    cursor = request.args.get('cursor', '').strip()
    limit_raw = request.args.get('limit', '20').strip()

    try:
        limit = int(limit_raw)
        if limit <= 0 or limit > 50:
            limit = 20
    except ValueError:
        return jsonify({'result': 'fail', 'msg': 'Invalid limit parameter'}), 400

    # 2. Penentuan filter pengguna (Author Filter) & perlindungan privasi komunitas private
    visibility_filter = _private_post_visibility_filter(current_username)
    blocked_related = get_all_blocked_relation_usernames(current_username)
    muted_users = set(get_muted_usernames(current_username))
    excluded_authors = blocked_related | muted_users

    if username_receive:
        # Mode Profil: jika terdapat relasi block, isolasi postingan (jangan tampilkan)
        if has_block_relation(current_username, username_receive):
            return jsonify({
                'result': 'success',
                'msg': 'Postingan tidak tersedia karena pembatasan blokir',
                'posts': [],
                'next_cursor': None,
                'has_more': False,
                'tab': 'profile'
            }), 200

        # Jika akun author dinonaktifkan (deactivated)
        if is_account_deactivated(username_receive):
            return jsonify({
                'result': 'success',
                'msg': 'Akun ini sedang dinonaktifkan',
                'posts': [],
                'next_cursor': None,
                'has_more': False,
                'tab': 'profile'
            }), 200

        # Jika akun privat dan bukan milik sendiri dan belum menjadi follower yang disetujui
        if is_private_account(username_receive) and (current_username != username_receive) and not is_following(current_username, username_receive):
            return jsonify({
                'result': 'success',
                'msg': 'Akun ini bersifat privat. Ikuti akun ini untuk melihat postingannya.',
                'posts': [],
                'is_private_locked': True,
                'next_cursor': None,
                'has_more': False,
                'tab': 'profile'
            }), 200

        # Jika melihat profil orang lain (sebagai follower): jangan tampilkan postingan 'private' milik mereka
        if current_username != username_receive:
            query = {
                '$and': [
                    {'username': username_receive},
                    {'$or': [
                        {'privacy': {'$in': ['public', 'followers']}},
                        {'privacy': {'$exists': False}}
                    ]},
                    visibility_filter
                ]
            }
        else:
            query = {'$and': [{'username': username_receive}, visibility_filter]}
    elif tab in ['following', 'timeline']:
        # Mode Personalized Timeline: postingan diri sendiri + akun yang diikuti (tanpa yang diblokir, dimute, atau dinonaktifkan)
        deactivated_users = set(db.users.distinct('username', {'is_deactivated': True}))
        following_users = db.follows.distinct('following', {'follower': current_username})
        allowed_authors = [u for u in ([current_username] + following_users) if u not in (excluded_authors | deactivated_users)]
        timeline_privacy_filter = {
            '$or': [
                {'username': current_username},
                {'privacy': {'$in': ['public', 'followers']}},
                {'privacy': {'$exists': False}}
            ]
        }
        query = {'$and': [{'username': {'$in': allowed_authors}}, timeline_privacy_filter, visibility_filter]}
    else:
        # Mode Explore / All / Default: postingan publik (tanpa akun diblokir/dimute, bukan akun privat, bukan followers-only, bukan deactivated)
        private_users = set(db.users.distinct('username', {'is_private': True}))
        deactivated_users = set(db.users.distinct('username', {'is_deactivated': True}))
        explore_excluded = excluded_authors | private_users | deactivated_users
        public_privacy_filter = {'$or': [{'privacy': 'public'}, {'privacy': {'$exists': False}}]}
        if explore_excluded:
            query = {'$and': [{'username': {'$nin': list(explore_excluded)}}, public_privacy_filter, visibility_filter]}
        else:
            query = {'$and': [public_privacy_filter, visibility_filter]}

    # 3. Penanganan Cursor Pagination (Keyset Pagination)
    if cursor:
        if ObjectId.is_valid(cursor):
            anchor_post = db.posts.find_one({'_id': ObjectId(cursor)})
            if not anchor_post:
                return jsonify({'result': 'fail', 'msg': 'Cursor not found'}), 404
            cursor_date = anchor_post.get('date', '')
            cursor_id = anchor_post['_id']
            pagination_filter = {
                '$or': [
                    {'date': {'$lt': cursor_date}},
                    {'date': cursor_date, '_id': {'$lt': cursor_id}}
                ]
            }
            if query:
                query = {'$and': [query, pagination_filter]}
            else:
                query = pagination_filter
        elif cursor.count('-') >= 2 or cursor.count('.') >= 2:
            pagination_filter = {'date': {'$lt': cursor}}
            if query:
                query = {'$and': [query, pagination_filter]}
            else:
                query = pagination_filter
        else:
            return jsonify({'result': 'fail', 'msg': 'Invalid cursor parameter'}), 400

    # 4. Eksekusi query dengan pengurutan kronologis terbaru (Newest First)
    posts = list(db.posts.find(query).sort([('date', -1), ('_id', -1)]).limit(limit))

    # 5. Memperkaya metadata postingan tanpa N+1 query loop
    posts = enrich_posts_metadata(posts, current_username, is_profile_view_for=username_receive)

    has_more = (len(posts) == limit)
    next_cursor = str(posts[-1]['_id']) if has_more else None

    return jsonify({
        'result': 'success',
        'msg': 'Successfully fetched posts',
        'posts': posts,
        'next_cursor': next_cursor,
        'has_more': has_more,
        'is_private_locked': False,
        'tab': tab if not username_receive else 'profile'
    }), 200    
    
    
@app.route('/update_like' , methods=['POST'])
def update_like():
    token_receive = request.cookies.get('mytoken')
    try:
        payload = jwt.decode(
            token_receive,
            SECRET_KEY,
            algorithms=['HS256']
        )
        user_info = db.users.find_one({'username' : payload.get('id')})
        post_id_receive = request.form.get('post_id_give')
        type_receive = request.form.get('type_give')
        action_receive = request.form.get('action_give')
        if post_id_receive and ObjectId.is_valid(post_id_receive):
            target_post_check = db.posts.find_one({'_id': ObjectId(post_id_receive)})
            if target_post_check and not _user_can_access_post(target_post_check, user_info.get('username') if user_info else None):
                return jsonify({'result': 'fail', 'msg': 'Anda tidak memiliki akses ke postingan komunitas privat ini.'}), 403
        doc = {
            'post_id' : post_id_receive,
            'username' : user_info.get('username'),
            'type' : type_receive,
        }
        if action_receive == 'like':
            db.likes.insert_one(doc)
        else:
            db.likes.delete_one(doc) 
            
        if action_receive == 'stars':
            db.stars.insert_one(doc)
        else:
            db.stars.delete_one(doc) 
            
        if action_receive == 'thumbs':
            db.thumbs.insert_one(doc)
        else:
            db.thumbs.delete_one(doc) 

        # Notifikasi reaksi (like/stars/thumbs)
        if action_receive in ['like', 'stars', 'thumbs']:
            if post_id_receive and ObjectId.is_valid(post_id_receive):
                target_post = db.posts.find_one({'_id': ObjectId(post_id_receive)})
                if target_post and target_post.get('username') != user_info.get('username'):
                    create_notification(
                        recipient=target_post.get('username'),
                        actor=user_info.get('username'),
                        notif_type='reaction',
                        post_id=post_id_receive,
                        action_type=type_receive
                    ) 
        
        count = db.likes.count_documents({
            'post_id' : post_id_receive,
            'type' : type_receive
        }) 
        count_stars = db.stars.count_documents({ 
            'post_id' : post_id_receive,
            'type' : type_receive
        }) 
        count_thumbs = db.thumbs.count_documents({ 
            'post_id' : post_id_receive,
            'type' : type_receive
        }) 
        
        return jsonify({
            'result' : 'success',
            'msg' :'updated!',
            'count' : count,
            'count_stars' : count_stars,
            'count_thumbs' : count_thumbs
        })
           
               
        
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return redirect(url_for('home'))    


# ==========================================
# COMMENT & REPLY SYSTEM API ENDPOINTS
# ==========================================

@app.route('/create_comment', methods=['POST'])
def create_comment():
    """
    Membuat komentar utama atau balasan (reply/nested reply) pada postingan.
    Mengautentikasi pengguna melalui JWT pada cookie 'mytoken'.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({
            'result': 'fail',
            'msg': 'Authentication token is missing, expired, or invalid'
        }), 401

    username = payload.get('id')
    user_info = db.users.find_one({'username': username})
    if not user_info:
        return jsonify({
            'result': 'fail',
            'msg': 'User profile not found'
        }), 404

    post_id_receive = request.form.get('post_id_give', '').strip()
    content_receive = request.form.get('content_give', '').strip()
    parent_id_receive = request.form.get('parent_comment_id_give')
    if parent_id_receive is not None:
        parent_id_receive = parent_id_receive.strip()
        if parent_id_receive == '' or parent_id_receive.lower() == 'null':
            parent_id_receive = None

    # 1. Validasi format dan keberadaan post_id
    if not post_id_receive or not ObjectId.is_valid(post_id_receive):
        return jsonify({
            'result': 'fail',
            'msg': 'Invalid post ID format'
        }), 400

    target_post = db.posts.find_one({'_id': ObjectId(post_id_receive)})
    if not target_post:
        return jsonify({
            'result': 'fail',
            'msg': 'Target post not found'
        }), 404

    if not _user_can_access_post(target_post, username):
        return jsonify({
            'result': 'fail',
            'msg': 'Anda tidak memiliki akses ke postingan ini.'
        }), 403

    if has_block_relation(username, target_post.get('username')):
        return jsonify({
            'result': 'fail',
            'msg': 'Cannot comment on this post due to block restrictions'
        }), 403

    # 2. Validasi konten komentar
    if not content_receive:
        return jsonify({
            'result': 'fail',
            'msg': 'Comment content cannot be empty'
        }), 400

    if len(content_receive) > 2000:
        return jsonify({
            'result': 'fail',
            'msg': 'Comment content exceeds maximum length of 2000 characters'
        }), 400

    # 3. Validasi parent_comment_id jika merupakan reply
    parent_comment_id = None
    if parent_id_receive:
        if not ObjectId.is_valid(parent_id_receive):
            return jsonify({
                'result': 'fail',
                'msg': 'Invalid parent comment ID format'
            }), 400

        parent_comment = db.comments.find_one({'_id': ObjectId(parent_id_receive)})
        if not parent_comment:
            return jsonify({
                'result': 'fail',
                'msg': 'Parent comment not found'
            }), 404

        # Pastikan komentar induk berasal dari postingan yang sama
        if str(parent_comment.get('post_id')) != str(post_id_receive):
            return jsonify({
                'result': 'fail',
                'msg': 'Parent comment does not belong to the specified post'
            }), 400

        if has_block_relation(username, parent_comment.get('username')):
            return jsonify({
                'result': 'fail',
                'msg': 'Cannot reply to this comment due to block restrictions'
            }), 403

        parent_comment_id = str(parent_comment['_id'])

    # 4. Buat dokumen komentar baru
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    doc = {
        'post_id': str(post_id_receive),
        'parent_comment_id': parent_comment_id,
        'username': username,
        'profile_name': user_info.get('profile_name', username),
        'profile_pic_real': user_info.get('profile_pic_real', 'profile_pics/profile_placeholder.png'),
        'content': content_receive,
        'created_at': now_iso,
        'updated_at': None,
        'is_deleted': False
    }

    result = db.comments.insert_one(doc)
    doc['_id'] = str(result.inserted_id)
    doc['is_mine'] = True

    # Notifikasi untuk komentar dan balasan
    new_comment_id = doc['_id']
    post_author = target_post.get('username')

    if parent_comment_id:
        parent_author = parent_comment.get('username')
        if parent_author and parent_author != username:
            create_notification(
                recipient=parent_author,
                actor=username,
                notif_type='reply',
                post_id=str(post_id_receive),
                comment_id=new_comment_id
            )
        if post_author and post_author != username and post_author != parent_author:
            create_notification(
                recipient=post_author,
                actor=username,
                notif_type='comment',
                post_id=str(post_id_receive),
                comment_id=new_comment_id
            )
    else:
        if post_author and post_author != username:
            create_notification(
                recipient=post_author,
                actor=username,
                notif_type='comment',
                post_id=str(post_id_receive),
                comment_id=new_comment_id
            )

    return jsonify({
        'result': 'success',
        'msg': 'Comment created successfully',
        'comment': doc
    }), 201


@app.route('/get_comments', methods=['GET'])
def get_comments():
    """
    Mengambil seluruh komentar dari suatu postingan, diurutkan kronologis.
    Memeriksa token JWT untuk menentukan flag 'is_mine' bagi pengguna aktif.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({
            'result': 'fail',
            'msg': 'Authentication token is missing, expired, or invalid'
        }), 401

    current_user = payload.get('id')
    post_id_receive = request.args.get('post_id_give', '').strip()

    # 1. Validasi format dan keberadaan post_id
    if not post_id_receive or not ObjectId.is_valid(post_id_receive):
        return jsonify({
            'result': 'fail',
            'msg': 'Invalid post ID format'
        }), 400

    target_post = db.posts.find_one({'_id': ObjectId(post_id_receive)})
    if not target_post:
        return jsonify({
            'result': 'fail',
            'msg': 'Target post not found'
        }), 404

    if not _user_can_access_post(target_post, current_user):
        return jsonify({
            'result': 'fail',
            'msg': 'Anda tidak memiliki akses ke postingan komunitas privat ini.'
        }), 403

    # 2. Ambil komentar terurut ascending berdasarkan waktu pembuatan (sembunyikan dari pengguna yang diblokir)
    blocked_related = get_all_blocked_relation_usernames(current_user) if current_user else set()
    comments_cursor = db.comments.find({'post_id': str(post_id_receive)}).sort('created_at', 1)
    comments_list = []
    for c in comments_cursor:
        if c.get('username') in blocked_related:
            continue
        is_deleted = bool(c.get('is_deleted', False))
        is_mine = (c.get('username') == current_user)
        # Sembunyikan konten asli jika komentar telah dihapus (soft delete)
        content_display = "[Komentar ini telah dihapus oleh pengguna]" if is_deleted else c.get('content', '')

        comments_list.append({
            '_id': str(c['_id']),
            'post_id': str(c.get('post_id')),
            'parent_comment_id': str(c.get('parent_comment_id')) if c.get('parent_comment_id') else None,
            'username': c.get('username'),
            'profile_name': c.get('profile_name'),
            'profile_pic_real': c.get('profile_pic_real'),
            'content': content_display,
            'created_at': c.get('created_at'),
            'updated_at': c.get('updated_at'),
            'is_deleted': is_deleted,
            'is_mine': is_mine
        })

    return jsonify({
        'result': 'success',
        'msg': 'Comments fetched successfully',
        'comments': comments_list
    }), 200


@app.route('/delete_comment', methods=['POST'])
def delete_comment():
    """
    Menghapus komentar milik pengguna aktif menggunakan soft delete (is_deleted: True).
    Mempertahankan integritas relasi nested reply.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({
            'result': 'fail',
            'msg': 'Authentication token is missing, expired, or invalid'
        }), 401

    current_user = payload.get('id')
    comment_id_receive = request.form.get('comment_id_give', '').strip()

    # 1. Validasi comment_id
    if not comment_id_receive or not ObjectId.is_valid(comment_id_receive):
        return jsonify({
            'result': 'fail',
            'msg': 'Invalid comment ID format'
        }), 400

    comment = db.comments.find_one({'_id': ObjectId(comment_id_receive)})
    if not comment:
        return jsonify({
            'result': 'fail',
            'msg': 'Comment not found'
        }), 404

    # 2. Otorisasi kepemilikan komentar
    if comment.get('username') != current_user:
        return jsonify({
            'result': 'fail',
            'msg': 'You do not have permission to delete this comment'
        }), 403

    # 3. Cek apakah sudah pernah dihapus
    if comment.get('is_deleted', False):
        return jsonify({
            'result': 'fail',
            'msg': 'Comment is already deleted'
        }), 400

    # 4. Soft delete
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    db.comments.update_one(
        {'_id': ObjectId(comment_id_receive)},
        {
            '$set': {
                'is_deleted': True,
                'content': '[Komentar ini telah dihapus oleh pengguna]',
                'updated_at': now_iso
            }
        }
    )

    return jsonify({
        'result': 'success',
        'msg': 'Comment deleted successfully'
    }), 200


@app.route('/about',methods=['GET'])
def about():
    return render_template('about.html')
    
@app.route('/secret',methods=['GET'])
def secret():
    token_receive = request.cookies.get('mytoken')
    try:
        payload = jwt.decode(
            token_receive,
            SECRET_KEY,
            algorithms=['HS256']
        )
        user_info = db.users.find_one({'username': payload.get('id')})
        return render_template('secret.html' , user_info=user_info)
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return redirect(url_for('home'))  


# ==========================================
# FOLLOW & UNFOLLOW SUBSYSTEM (SWEETER V2)
# ==========================================

def _extract_target_username(param_username=None):
    """Mengekstrak target username dari route parameter, JSON body, atau form-data."""
    if param_username and isinstance(param_username, str) and param_username.strip():
        return param_username.strip()

    if request.is_json:
        json_data = request.get_json(silent=True) or {}
        val = json_data.get('target_username') or json_data.get('target_username_give')
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    form_val = request.form.get('target_username_give') or request.form.get('target_username')
    if form_val and isinstance(form_val, str) and form_val.strip():
        return form_val.strip()

    arg_val = request.args.get('target_username') or request.args.get('target_username_give')
    if arg_val and isinstance(arg_val, str) and arg_val.strip():
        return arg_val.strip()

    return None


@app.route('/api/follow', methods=['POST'])
@app.route('/api/users/<target_username>/follow', methods=['POST'])
def follow_user(target_username=None):
    """
    Endpoint untuk mengikuti (follow) pengguna lain.
    Menerima target_username via URL parameter, JSON, atau form-data.
    """
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    target = _extract_target_username(target_username)
    if not target:
        return jsonify({'result': 'fail', 'msg': 'Target username is required'}), 400

    # Aturan 1: Tidak dapat mengikuti diri sendiri
    if current_username == target:
        return jsonify({'result': 'fail', 'msg': 'You cannot follow yourself'}), 400

    # Aturan 2: Target user harus terdaftar di db.users
    target_user = db.users.find_one({'username': target})
    if not target_user:
        return jsonify({'result': 'fail', 'msg': 'User not found'}), 404

    # Aturan 3: Cegah follow jika terdapat relasi block
    if has_block_relation(current_username, target):
        return jsonify({
            'result': 'fail',
            'msg': 'Cannot follow this user due to block restrictions'
        }), 403

    # Aturan 4: Jika target adalah akun privat, alihkan ke sistem Follow Request
    if target_user.get('is_private') is True:
        already_following = db.follows.find_one({'follower': current_username, 'following': target})
        if already_following:
            follower_count = db.follows.count_documents({'following': target})
            following_count = db.follows.count_documents({'follower': current_username})
            return jsonify({
                'result': 'fail',
                'msg': 'You are already following this user',
                'is_following': True,
                'is_requested': False,
                'follower_count': follower_count,
                'following_count': following_count
            }), 409

        existing_req = db.follow_requests.find_one({
            'requester': current_username,
            'target': target,
            'status': 'pending'
        })
        if existing_req:
            follower_count = db.follows.count_documents({'following': target})
            following_count = db.follows.count_documents({'follower': current_username})
            return jsonify({
                'result': 'fail',
                'msg': 'Permintaan mengikuti sudah dikirim dan sedang menunggu persetujuan.',
                'is_following': False,
                'is_requested': True,
                'follower_count': follower_count,
                'following_count': following_count
            }), 409

        now_iso = datetime.now(timezone.utc).isoformat()
        db.follow_requests.update_one(
            {'requester': current_username, 'target': target},
            {
                '$set': {
                    'status': 'pending',
                    'updated_at': now_iso
                },
                '$setOnInsert': {
                    'created_at': now_iso
                }
            },
            upsert=True
        )

        create_notification(
            recipient=target,
            actor=current_username,
            notif_type='follow_request'
        )

        follower_count = db.follows.count_documents({'following': target})
        following_count = db.follows.count_documents({'follower': current_username})
        return jsonify({
            'result': 'success',
            'msg': f'Permintaan mengikuti telah dikirim ke @{target}',
            'is_following': False,
            'is_requested': True,
            'follower_count': follower_count,
            'following_count': following_count
        }), 200

    # Aturan 5: Cegah duplicate follow (race-safe & idempotent)
    existing = db.follows.find_one({'follower': current_username, 'following': target})
    if existing:
        follower_count = db.follows.count_documents({'following': target})
        following_count = db.follows.count_documents({'follower': current_username})
        return jsonify({
            'result': 'fail',
            'msg': 'You are already following this user',
            'is_following': True,
            'is_requested': False,
            'follower_count': follower_count,
            'following_count': following_count
        }), 409

    now = datetime.now(timezone.utc).isoformat()
    try:
        db.follows.insert_one({
            'follower': current_username,
            'following': target,
            'created_at': now
        })
    except DuplicateKeyError:
        follower_count = db.follows.count_documents({'following': target})
        following_count = db.follows.count_documents({'follower': current_username})
        return jsonify({
            'result': 'fail',
            'msg': 'You are already following this user',
            'is_following': True,
            'is_requested': False,
            'follower_count': follower_count,
            'following_count': following_count
        }), 409

    follower_count = db.follows.count_documents({'following': target})
    following_count = db.follows.count_documents({'follower': current_username})

    # Notifikasi follow
    create_notification(
        recipient=target,
        actor=current_username,
        notif_type='follow'
    )

    return jsonify({
        'result': 'success',
        'msg': f'Successfully followed {target}',
        'is_following': True,
        'is_requested': False,
        'follower_count': follower_count,
        'following_count': following_count
    }), 200


@app.route('/api/unfollow', methods=['POST'])
@app.route('/api/users/<target_username>/unfollow', methods=['POST'])
def unfollow_user(target_username=None):
    """
    Endpoint untuk berhenti mengikuti (unfollow) pengguna lain.
    Idempotent & aman meskipun relasi follow belum pernah ada.
    """
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    target = _extract_target_username(target_username)
    if not target:
        return jsonify({'result': 'fail', 'msg': 'Target username is required'}), 400

    if current_username == target:
        return jsonify({'result': 'fail', 'msg': 'You cannot unfollow yourself'}), 400

    # Hapus relasi follow secara aman dan batalkan permintaan follow jika ada
    db.follows.delete_one({'follower': current_username, 'following': target})
    db.follow_requests.delete_many({'requester': current_username, 'target': target})

    follower_count = db.follows.count_documents({'following': target})
    following_count = db.follows.count_documents({'follower': current_username})

    return jsonify({
        'result': 'success',
        'msg': f'Successfully unfollowed {target}',
        'is_following': False,
        'follower_count': follower_count,
        'following_count': following_count
    }), 200


@app.route('/api/follow_status', methods=['GET'])
def follow_status():
    """
    Memeriksa status follow antara pengguna saat ini dan target username.
    """
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    target = _extract_target_username()
    if not target:
        return jsonify({'result': 'fail', 'msg': 'Target username is required'}), 400

    target_user = db.users.find_one({'username': target})
    if not target_user:
        return jsonify({'result': 'fail', 'msg': 'User not found'}), 404

    is_following = bool(db.follows.find_one({'follower': current_username, 'following': target}))
    has_requested = has_pending_follow_request(current_username, target)
    is_private_val = is_private_account(target)
    follower_count = db.follows.count_documents({'following': target})
    following_count = db.follows.count_documents({'follower': target})

    return jsonify({
        'result': 'success',
        'target_username': target,
        'is_following': is_following,
        'is_requested': has_requested,
        'is_private': is_private_val,
        'follower_count': follower_count,
        'following_count': following_count
    }), 200


@app.route('/api/users/<username>/followers', methods=['GET'])
def get_user_followers_api(username):
    """
    API JSON untuk mengambil daftar akun yang mengikuti pengguna tertentu.
    """
    target_user = db.users.find_one({'username': username})
    if not target_user:
        return jsonify({'result': 'fail', 'msg': 'User not found'}), 404

    # Identifikasi current_user jika ada cookie mytoken
    token_receive = request.cookies.get('mytoken')
    current_username = None
    if token_receive:
        try:
            payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
            current_username = payload.get('id')
        except Exception:
            current_username = None

    if current_username and has_block_relation(current_username, username):
        return jsonify({'result': 'fail', 'msg': 'Cannot access followers list due to block restrictions'}), 403

    if target_user.get('is_private') and current_username != username:
        is_following_target = bool(current_username and is_following(current_username, username))
        if not is_following_target and not is_admin_user(current_username):
            return jsonify({'result': 'fail', 'msg': 'This account is private. Follow to view followers.'}), 403

    blocked_related = get_all_blocked_relation_usernames(current_username) if current_username else set()
    follow_docs = list(db.follows.find({'following': username}).sort('created_at', -1))
    follower_usernames = [d['follower'] for d in follow_docs if d['follower'] not in blocked_related]

    user_docs = {
        u['username']: u
        for u in db.users.find({'username': {'$in': follower_usernames}}, {'password': 0})
    }

    # Kumpulkan akun yang diikuti oleh current_user untuk penanda status is_following
    my_followings = set()
    if current_username:
        my_follow_docs = db.follows.find({
            'follower': current_username,
            'following': {'$in': follower_usernames}
        })
        my_followings = {f['following'] for f in my_follow_docs}

    followers_list = []
    for uname in follower_usernames:
        u = user_docs.get(uname)
        if u:
            followers_list.append({
                'username': u.get('username'),
                'profile_name': u.get('profile_name', u.get('username')),
                'profile_pic_real': u.get('profile_pic_real', 'profile_pics/profile_placeholder.png'),
                'profile_info': u.get('profile_info', ''),
                'is_following': uname in my_followings,
                'is_self': (uname == current_username)
            })

    return jsonify({
        'result': 'success',
        'username': username,
        'total': len(followers_list),
        'users': followers_list
    }), 200


@app.route('/api/users/<username>/following', methods=['GET'])
def get_user_following_api(username):
    """
    API JSON untuk mengambil daftar akun yang diikuti oleh pengguna tertentu.
    """
    target_user = db.users.find_one({'username': username})
    if not target_user:
        return jsonify({'result': 'fail', 'msg': 'User not found'}), 404

    token_receive = request.cookies.get('mytoken')
    current_username = None
    if token_receive:
        try:
            payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
            current_username = payload.get('id')
        except Exception:
            current_username = None

    if current_username and has_block_relation(current_username, username):
        return jsonify({'result': 'fail', 'msg': 'Cannot access following list due to block restrictions'}), 403

    if target_user.get('is_private') and current_username != username:
        is_following_target = bool(current_username and is_following(current_username, username))
        if not is_following_target and not is_admin_user(current_username):
            return jsonify({'result': 'fail', 'msg': 'This account is private. Follow to view following.'}), 403

    blocked_related = get_all_blocked_relation_usernames(current_username) if current_username else set()
    follow_docs = list(db.follows.find({'follower': username}).sort('created_at', -1))
    following_usernames = [d['following'] for d in follow_docs if d['following'] not in blocked_related]

    user_docs = {
        u['username']: u
        for u in db.users.find({'username': {'$in': following_usernames}}, {'password': 0})
    }

    my_followings = set()
    if current_username:
        my_follow_docs = db.follows.find({
            'follower': current_username,
            'following': {'$in': following_usernames}
        })
        my_followings = {f['following'] for f in my_follow_docs}

    following_list = []
    for uname in following_usernames:
        u = user_docs.get(uname)
        if u:
            following_list.append({
                'username': u.get('username'),
                'profile_name': u.get('profile_name', u.get('username')),
                'profile_pic_real': u.get('profile_pic_real', 'profile_pics/profile_placeholder.png'),
                'profile_info': u.get('profile_info', ''),
                'is_following': uname in my_followings,
                'is_self': (uname == current_username)
            })

    return jsonify({
        'result': 'success',
        'username': username,
        'total': len(following_list),
        'users': following_list
    }), 200


@app.route('/user/<username>/followers', methods=['GET'])
def user_followers_page(username):
    """Halaman daftar pengikut (followers) seorang pengguna."""
    token_receive = request.cookies.get('mytoken')
    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
        current_user = db.users.find_one({'username': current_username}, {'_id': False})
        user_info = db.users.find_one({'username': username}, {'_id': False})
        if not user_info:
            return render_template('404.html', username=username, current_user=current_user), 404

        if has_block_relation(current_username, username):
            return redirect(url_for('user', username=username))

        follower_count = db.follows.count_documents({'following': username})
        following_count = db.follows.count_documents({'follower': username})
        is_following = bool(db.follows.find_one({'follower': current_username, 'following': username}))

        return render_template(
            'user_follows.html',
            user_info=user_info,
            current_user=current_user,
            status=(username == current_username),
            active_tab='followers',
            follower_count=follower_count,
            following_count=following_count,
            is_following=is_following
        )
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return redirect(url_for('home'))


@app.route('/user/<username>/following', methods=['GET'])
def user_following_page(username):
    """Halaman daftar akun yang diikuti (following) oleh seorang pengguna."""
    token_receive = request.cookies.get('mytoken')
    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
        current_user = db.users.find_one({'username': current_username}, {'_id': False})
        user_info = db.users.find_one({'username': username}, {'_id': False})
        if not user_info:
            return render_template('404.html', username=username, current_user=current_user), 404

        if has_block_relation(current_username, username):
            return redirect(url_for('user', username=username))

        follower_count = db.follows.count_documents({'following': username})
        following_count = db.follows.count_documents({'follower': username})
        is_following = bool(db.follows.find_one({'follower': current_username, 'following': username}))

        return render_template(
            'user_follows.html',
            user_info=user_info,
            current_user=current_user,
            status=(username == current_username),
            active_tab='following',
            follower_count=follower_count,
            following_count=following_count,
            is_following=is_following
        )
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return redirect(url_for('home'))  


# ==========================================
# FOLLOW REQUESTS SUBSYSTEM
# ==========================================

@app.route('/api/follow_requests', methods=['GET'])
def get_incoming_follow_requests():
    """
    Mengambil daftar permintaan follow masuk untuk akun pengguna saat ini (yang berstatus 'pending').
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    req_docs = list(db.follow_requests.find({'target': current_username, 'status': 'pending'}).sort('created_at', -1))
    requester_names = [r['requester'] for r in req_docs]

    user_docs = {
        u['username']: u
        for u in db.users.find({'username': {'$in': requester_names}}, {'password': 0})
    }

    requests_list = []
    for r in req_docs:
        uname = r['requester']
        u = user_docs.get(uname)
        requests_list.append({
            'id': str(r['_id']),
            'requester': uname,
            'profile_name': u.get('profile_name', uname) if u else uname,
            'profile_pic_real': u.get('profile_pic_real', 'profile_pics/profile_placeholder.png') if u else 'profile_pics/profile_placeholder.png',
            'profile_info': u.get('profile_info', '') if u else '',
            'created_at': r.get('created_at', '')
        })

    return jsonify({
        'result': 'success',
        'total': len(requests_list),
        'requests': requests_list,
        'follow_requests': requests_list
    }), 200


@app.route('/api/follow_requests/<requester_username>/accept', methods=['POST'])
def accept_follow_request(requester_username):
    """
    Menyetujui permintaan follow dari requester_username.
    Membuat relasi di db.follows dan mengirimkan notifikasi.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    req = db.follow_requests.find_one({
        'requester': requester_username,
        'target': current_username,
        'status': 'pending'
    })
    if not req:
        return jsonify({'result': 'fail', 'msg': 'Permintaan follow tidak ditemukan atau sudah diproses'}), 404

    db.follow_requests.delete_one({'_id': req['_id']})

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        db.follows.insert_one({
            'follower': requester_username,
            'following': current_username,
            'created_at': now_iso
        })
    except DuplicateKeyError:
        pass

    create_notification(
        recipient=requester_username,
        actor=current_username,
        notif_type='follow_request_accepted'
    )

    follower_count = db.follows.count_documents({'following': current_username})
    return jsonify({
        'result': 'success',
        'msg': f'Permintaan mengikuti dari @{requester_username} telah disetujui',
        'follower_count': follower_count
    }), 200


@app.route('/api/follow_requests/<requester_username>/reject', methods=['POST'])
def reject_follow_request(requester_username):
    """
    Menolak permintaan follow dari requester_username.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    req = db.follow_requests.find_one({
        'requester': requester_username,
        'target': current_username,
        'status': 'pending'
    })
    if not req:
        return jsonify({'result': 'fail', 'msg': 'Permintaan follow tidak ditemukan atau sudah diproses'}), 404

    db.follow_requests.delete_one({'_id': req['_id']})

    return jsonify({
        'result': 'success',
        'msg': f'Permintaan mengikuti dari @{requester_username} telah ditolak'
    }), 200


@app.route('/api/follow_requests/<target_username>/cancel', methods=['POST'])
@app.route('/api/follow_requests/cancel', methods=['POST'])
def cancel_follow_request(target_username=None):
    """
    Membatalkan permintaan follow yang telah dikirim oleh pengguna saat ini kepada target.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    target = _extract_target_username(target_username)
    if not target:
        return jsonify({'result': 'fail', 'msg': 'Target username is required'}), 400

    db.follow_requests.delete_many({
        'requester': current_username,
        'target': target,
        'status': 'pending'
    })

    db.notifications.delete_many({
        'recipient': target,
        'actor': current_username,
        'type': 'follow_request'
    })

    follower_count = db.follows.count_documents({'following': target})
    following_count = db.follows.count_documents({'follower': current_username})

    return jsonify({
        'result': 'success',
        'msg': f'Permintaan mengikuti ke @{target} telah dibatalkan',
        'is_requested': False,
        'is_following': False,
        'follower_count': follower_count,
        'following_count': following_count
    }), 200


@app.route('/api/followers/<follower_username>/remove', methods=['POST'])
def remove_follower(follower_username):
    """
    Menghapus follower dari akun pengguna saat ini (mencabut akses terhadap konten privat).
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    if not follower_username:
        return jsonify({'result': 'fail', 'msg': 'Follower username is required'}), 400

    db.follows.delete_one({'follower': follower_username, 'following': current_username})
    follower_count = db.follows.count_documents({'following': current_username})

    return jsonify({
        'result': 'success',
        'msg': f'@{follower_username} telah dihapus dari daftar pengikut Anda',
        'follower_count': follower_count
    }), 200


# ==========================================
# ACCOUNT & PRIVACY SETTINGS SUBSYSTEM
# ==========================================

EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')
USERNAME_REGEX = re.compile(r'^[a-zA-Z0-9_]{3,30}$')
RESERVED_USERNAMES = {'admin', 'settings', 'login', 'logout', 'sign_in', 'sign_out', 'sign_up', 'api', 'about', 'explore', 'notifications', 'community', 'communities', 'blocked', 'muted'}


@app.route('/settings', methods=['GET'])
def settings_page():
    """Halaman Pengaturan Akun, Privasi, Keamanan, dan Manajemen Akun."""
    payload = get_current_user_from_token()
    if not payload:
        return redirect(url_for('login'))

    current_username = payload.get('id')
    user_info = db.users.find_one({'username': current_username}, {'password': 0})
    if not user_info:
        return redirect(url_for('login'))

    unread_count = get_unread_count(current_username)
    is_admin = is_admin_user(current_username)
    pending_requests_count = get_pending_follow_requests_count(current_username)

    return render_template(
        'settings.html',
        user_info=user_info,
        unread_count=unread_count,
        is_admin=is_admin,
        pending_requests_count=pending_requests_count
    )


@app.route('/api/account/settings', methods=['GET'])
def get_account_settings_api():
    """Mengambil data pengaturan akun dan privasi saat ini bagi pengguna yang login."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    user_doc = db.users.find_one({'username': current_username}, {'password': 0})
    if not user_doc:
        return jsonify({'result': 'fail', 'msg': 'User not found'}), 404

    return jsonify({
        'result': 'success',
        'settings': {
            'username': user_doc.get('username'),
            'profile_name': user_doc.get('profile_name', user_doc.get('username')),
            'profile_info': user_doc.get('profile_info', ''),
            'profile_pic_real': user_doc.get('profile_pic_real', 'profile_pics/profile_placeholder.png'),
            'email': user_doc.get('email', ''),
            'is_private': bool(user_doc.get('is_private', False)),
            'show_follow_counts': bool(user_doc.get('show_follow_counts', True)),
            'show_bio': bool(user_doc.get('show_bio', True)),
            'default_post_privacy': user_doc.get('default_post_privacy', 'public'),
            'created_at': user_doc.get('created_at', ''),
            'pending_requests_count': get_pending_follow_requests_count(current_username)
        }
    }), 200


@app.route('/api/account/profile', methods=['POST'])
def update_account_profile():
    """
    Memperbarui profil akun:
    - Display Name (profile_name)
    - Bio (profile_info)
    - Email (validasi format dan keunikan)
    - Profile Picture
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    data = request.get_json(silent=True) or request.form or {}

    display_name = data.get('profile_name') or data.get('display_name') or data.get('name_give')
    bio = data.get('profile_info') or data.get('bio') or data.get('about_give')
    email = data.get('email')

    update_fields = {}

    if display_name is not None:
        name_clean = str(display_name).strip()
        if len(name_clean) > 50:
            return jsonify({'result': 'fail', 'msg': 'Display name cannot exceed 50 characters'}), 400
        if not name_clean:
            return jsonify({'result': 'fail', 'msg': 'Display name cannot be empty'}), 400
        update_fields['profile_name'] = name_clean

    if bio is not None:
        bio_clean = str(bio).strip()
        if len(bio_clean) > 500:
            return jsonify({'result': 'fail', 'msg': 'Bio cannot exceed 500 characters'}), 400
        update_fields['profile_info'] = bio_clean

    if email is not None:
        email_clean = str(email).strip().lower()
        if email_clean:
            if not EMAIL_REGEX.match(email_clean):
                return jsonify({'result': 'fail', 'msg': 'Format email tidak valid'}), 400
            duplicate = db.users.find_one({'email': email_clean, 'username': {'$ne': current_username}})
            if duplicate:
                return jsonify({'result': 'fail', 'msg': 'Email sudah digunakan oleh akun lain'}), 409
        update_fields['email'] = email_clean

    if 'file_give' in request.files or 'profile_image' in request.files:
        file = request.files.get('file_give') or request.files.get('profile_image')
        if file and file.filename != '':
            raw_bytes = file.read()
            detected = _detect_image_format(raw_bytes)
            if not detected:
                return jsonify({'result': 'fail', 'msg': 'Format gambar tidak didukung (gunakan JPG, PNG, GIF, atau WebP)'}), 400
            ext = file.filename.split('.')[-1].lower()
            filename = f"{current_username}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}"
            file_path = f"/profile_pics/{filename}"
            target_dir = os.path.join(app.root_path, 'static', 'profile_pics')
            os.makedirs(target_dir, exist_ok=True)
            with open(os.path.join(target_dir, filename), 'wb') as f:
                f.write(raw_bytes)
            update_fields['profile_pic'] = filename
            update_fields['profile_pic_real'] = file_path

    if update_fields:
        db.users.update_one({'username': current_username}, {'$set': update_fields})

    updated_user = db.users.find_one({'username': current_username}, {'password': 0})
    return jsonify({
        'result': 'success',
        'msg': 'Profil akun berhasil diperbarui',
        'user': {
            'username': updated_user.get('username'),
            'profile_name': updated_user.get('profile_name'),
            'profile_info': updated_user.get('profile_info'),
            'profile_pic_real': updated_user.get('profile_pic_real'),
            'email': updated_user.get('email', '')
        }
    }), 200


@app.route('/api/account/change_username', methods=['POST'])
def change_username():
    """
    Mengubah username pengguna dengan validasi format dan keunikan menyeluruh,
    serta melakukan cascading update ke seluruh koleksi terkait dan menerbitkan token JWT baru.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    data = request.get_json(silent=True) or request.form or {}
    new_username = (data.get('new_username') or '').strip().lower()

    if not new_username:
        return jsonify({'result': 'fail', 'msg': 'New username is required'}), 400

    if new_username == current_username:
        return jsonify({'result': 'fail', 'msg': 'Username baru sama dengan username saat ini'}), 400

    if not USERNAME_REGEX.match(new_username):
        return jsonify({'result': 'fail', 'msg': 'Username harus terdiri dari 3-30 karakter alfanumerik atau garis bawah (_)'}), 400

    if new_username in RESERVED_USERNAMES:
        return jsonify({'result': 'fail', 'msg': 'Username ini dicadangkan untuk sistem'}), 400

    if db.users.find_one({'username': new_username}):
        return jsonify({'result': 'fail', 'msg': 'Username sudah digunakan'}), 409

    db.users.update_one({'username': current_username}, {'$set': {'username': new_username}})
    db.posts.update_many({'username': current_username}, {'$set': {'username': new_username}})
    db.comments.update_many({'username': current_username}, {'$set': {'username': new_username}})
    db.follows.update_many({'follower': current_username}, {'$set': {'follower': new_username}})
    db.follows.update_many({'following': current_username}, {'$set': {'following': new_username}})
    db.follow_requests.update_many({'requester': current_username}, {'$set': {'requester': new_username}})
    db.follow_requests.update_many({'target': current_username}, {'$set': {'target': new_username}})
    db.bookmarks.update_many({'username': current_username}, {'$set': {'username': new_username}})
    db.blocks.update_many({'blocker': current_username}, {'$set': {'blocker': new_username}})
    db.blocks.update_many({'blocked': current_username}, {'$set': {'blocked': new_username}})
    db.mutes.update_many({'muter': current_username}, {'$set': {'muter': new_username}})
    db.mutes.update_many({'muted': current_username}, {'$set': {'muted': new_username}})
    db.notifications.update_many({'recipient': current_username}, {'$set': {'recipient': new_username}})
    db.notifications.update_many({'actor': current_username}, {'$set': {'actor': new_username}})
    db.reports.update_many({'reporter_id': current_username}, {'$set': {'reporter_id': new_username}})
    db.reports.update_many({'target_author': current_username}, {'$set': {'target_author': new_username}})
    db.reports.update_many({'target_type': 'user', 'target_id': current_username}, {'$set': {'target_id': new_username}})
    if hasattr(db, 'community_members'):
        db.community_members.update_many({'user_id': current_username}, {'$set': {'user_id': new_username}})

    user_doc = db.users.find_one({'username': new_username})
    new_payload = {
        'id': new_username,
        'token_version': user_doc.get('token_version', 1),
        'exp': datetime.now(timezone.utc) + timedelta(seconds=60 * 60 * 24)
    }
    new_token = jwt.encode(new_payload, SECRET_KEY, algorithm='HS256')

    response = jsonify({
        'result': 'success',
        'msg': f'Username berhasil diubah menjadi @{new_username}',
        'new_username': new_username,
        'token': new_token
    })
    response.set_cookie('mytoken', new_token, max_age=60 * 60 * 24, path='/', samesite='Lax', secure=IS_PRODUCTION)
    return response, 200


@app.route('/api/account/change_password', methods=['POST'])
def change_password():
    """
    Mengubah password pengguna dengan memverifikasi password lama terlebih dahulu.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    user_doc = db.users.find_one({'username': current_username})
    if not user_doc:
        return jsonify({'result': 'fail', 'msg': 'User not found'}), 404

    data = request.get_json(silent=True) or request.form or {}
    old_password = data.get('old_password') or data.get('current_password') or ''
    new_password = data.get('new_password') or ''
    confirm_password = data.get('confirm_password')

    if not old_password or not new_password:
        return jsonify({'result': 'fail', 'msg': 'Password lama dan password baru wajib diisi'}), 400

    old_pw_hash = hashlib.sha256(old_password.encode('utf-8')).hexdigest()
    if old_pw_hash != user_doc.get('password'):
        return jsonify({'result': 'fail', 'msg': 'Password saat ini tidak sesuai'}), 400

    if len(new_password) < 6:
        return jsonify({'result': 'fail', 'msg': 'Password baru minimal harus 6 karakter'}), 400

    if confirm_password is not None and confirm_password != new_password:
        return jsonify({'result': 'fail', 'msg': 'Konfirmasi password baru tidak cocok'}), 400

    new_pw_hash = hashlib.sha256(new_password.encode('utf-8')).hexdigest()
    new_token_ver = user_doc.get('token_version', 1) + 1

    db.users.update_one(
        {'username': current_username},
        {
            '$set': {
                'password': new_pw_hash,
                'token_version': new_token_ver,
                'password_updated_at': datetime.now(timezone.utc).isoformat()
            }
        }
    )

    new_payload = {
        'id': current_username,
        'token_version': new_token_ver,
        'exp': datetime.now(timezone.utc) + timedelta(seconds=60 * 60 * 24)
    }
    new_token = jwt.encode(new_payload, SECRET_KEY, algorithm='HS256')

    response = jsonify({
        'result': 'success',
        'msg': 'Password berhasil diperbarui',
        'token': new_token
    })
    response.set_cookie('mytoken', new_token, max_age=60 * 60 * 24, path='/', samesite='Lax', secure=IS_PRODUCTION)
    return response, 200


@app.route('/api/account/privacy', methods=['POST'])
def update_privacy_settings():
    """
    Mengubah pengaturan privasi akun:
    - is_private (True/False)
    - show_follow_counts (True/False)
    - show_bio (True/False)
    - default_post_privacy ('public', 'followers', 'private')
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    data = request.get_json(silent=True) or request.form or {}

    update_fields = {}

    if 'is_private' in data:
        val = data.get('is_private')
        if isinstance(val, str):
            val = val.lower() in ('true', '1', 'yes')
        update_fields['is_private'] = bool(val)

    if 'show_follow_counts' in data:
        val = data.get('show_follow_counts')
        if isinstance(val, str):
            val = val.lower() in ('true', '1', 'yes')
        update_fields['show_follow_counts'] = bool(val)

    if 'show_bio' in data:
        val = data.get('show_bio')
        if isinstance(val, str):
            val = val.lower() in ('true', '1', 'yes')
        update_fields['show_bio'] = bool(val)

    if 'default_post_privacy' in data:
        val = str(data.get('default_post_privacy')).strip().lower()
        if val in ('public', 'followers', 'private'):
            update_fields['default_post_privacy'] = val

    if update_fields:
        db.users.update_one({'username': current_username}, {'$set': update_fields})

    updated_user = db.users.find_one({'username': current_username}, {'password': 0})
    return jsonify({
        'result': 'success',
        'msg': 'Pengaturan privasi berhasil diperbarui',
        'privacy': {
            'is_private': bool(updated_user.get('is_private', False)),
            'show_follow_counts': bool(updated_user.get('show_follow_counts', True)),
            'show_bio': bool(updated_user.get('show_bio', True)),
            'default_post_privacy': updated_user.get('default_post_privacy', 'public')
        }
    }), 200


@app.route('/api/account/deactivate', methods=['POST'])
def deactivate_account():
    """
    Menonaktifkan akun secara sementara. Memerlukan konfirmasi password.
    Menghapus cookie sesi aktif.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    user_doc = db.users.find_one({'username': current_username})
    if not user_doc:
        return jsonify({'result': 'fail', 'msg': 'User not found'}), 404

    data = request.get_json(silent=True) or request.form or {}
    password = data.get('password') or ''
    if not password:
        return jsonify({'result': 'fail', 'msg': 'Password konfirmasi wajib diisi'}), 400

    pw_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
    if pw_hash != user_doc.get('password'):
        return jsonify({'result': 'fail', 'msg': 'Password tidak sesuai'}), 400

    now_iso = datetime.now(timezone.utc).isoformat()
    new_token_ver = user_doc.get('token_version', 1) + 1
    db.users.update_one(
        {'username': current_username},
        {
            '$set': {
                'is_deactivated': True,
                'deactivated_at': now_iso,
                'token_version': new_token_ver
            }
        }
    )

    response = jsonify({
        'result': 'success',
        'msg': 'Akun Anda berhasil dinonaktifkan sementara. Anda dapat mengaktifkannya kembali kapan saja dengan masuk kembali.'
    })
    response.delete_cookie('mytoken', path='/')
    return response, 200


@app.route('/api/account/reactivate', methods=['POST'])
def reactivate_account():
    """
    Mengaktifkan kembali akun yang sedang dinonaktifkan.
    """
    data = request.get_json(silent=True) or request.form or {}
    username = (data.get('username') or data.get('username_give') or '').strip()
    password = data.get('password') or data.get('password_give') or ''

    if not username or not password:
        return jsonify({'result': 'fail', 'msg': 'Username dan password wajib diisi'}), 400

    pw_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
    user_doc = db.users.find_one({'username': username, 'password': pw_hash})
    if not user_doc:
        return jsonify({'result': 'fail', 'msg': 'Username atau password tidak sesuai'}), 401

    if user_doc.get('is_suspended') is True:
        return jsonify({'result': 'fail', 'msg': 'Akun Anda sedang ditangguhkan oleh administrator'}), 403

    db.users.update_one(
        {'username': username},
        {
            '$set': {
                'is_deactivated': False,
                'deactivated_at': None
            }
        }
    )

    token_ver = user_doc.get('token_version', 1)
    payload = {
        'id': username,
        'token_version': token_ver,
        'exp': datetime.now(timezone.utc) + timedelta(seconds=60 * 60 * 24)
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm='HS256')

    response = jsonify({
        'result': 'success',
        'msg': 'Akun Anda berhasil diaktifkan kembali. Selamat datang kembali di Sweeter!',
        'token': token
    })
    response.set_cookie('mytoken', token, max_age=60 * 60 * 24, path='/', samesite='Lax', secure=IS_PRODUCTION)
    return response, 200


@app.route('/api/account/delete', methods=['POST'])
def delete_account():
    """
    Menghapus akun secara permanen beserta seluruh relasi data terkait
    (posts, comments, replies, reactions, bookmarks, follows, follow requests, blocks, mutes, notifications).
    Memerlukan konfirmasi password.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    user_doc = db.users.find_one({'username': current_username})
    if not user_doc:
        return jsonify({'result': 'fail', 'msg': 'User not found'}), 404

    data = request.get_json(silent=True) or request.form or {}
    password = data.get('password') or ''
    if not password:
        return jsonify({'result': 'fail', 'msg': 'Password konfirmasi wajib diisi untuk menghapus akun'}), 400

    pw_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
    if pw_hash != user_doc.get('password'):
        return jsonify({'result': 'fail', 'msg': 'Password tidak sesuai'}), 400

    # 1. Hapus gambar-gambar fisik postingan milik user
    user_posts = list(db.posts.find({'username': current_username}))
    for p in user_posts:
        if p.get('post_type') != 'repost':
            for img in (p.get('images') or []):
                if isinstance(img, dict) and img.get('filename'):
                    safe_delete_post_image(img['filename'], check_db_refs=False)

    post_ids = [str(p['_id']) for p in user_posts]

    # 2. Hapus seluruh data postingan & reposts terkait
    db.posts.delete_many({'username': current_username})
    if post_ids:
        db.posts.delete_many({'original_post_id': {'$in': post_ids}})
        db.comments.delete_many({'post_id': {'$in': post_ids}})
        db.bookmarks.delete_many({'post_id': {'$in': post_ids}})
        db.likes.delete_many({'post_id': {'$in': post_ids}})
        db.stars.delete_many({'post_id': {'$in': post_ids}})
        db.thumbs.delete_many({'post_id': {'$in': post_ids}})
        for pid in post_ids:
            cleanup_notifications_for_post(pid)

    # 3. Hapus interaksi yang dilakukan oleh user
    db.comments.delete_many({'username': current_username})
    db.bookmarks.delete_many({'username': current_username})
    db.likes.delete_many({'username': current_username})
    db.stars.delete_many({'username': current_username})
    db.thumbs.delete_many({'username': current_username})

    # 4. Hapus relasi follow & follow requests
    db.follows.delete_many({'$or': [{'follower': current_username}, {'following': current_username}]})
    db.follow_requests.delete_many({'$or': [{'requester': current_username}, {'target': current_username}]})

    # 5. Hapus relasi block & mute
    db.blocks.delete_many({'$or': [{'blocker': current_username}, {'blocked': current_username}]})
    db.mutes.delete_many({'$or': [{'muter': current_username}, {'muted': current_username}]})

    # 6. Hapus notifikasi
    db.notifications.delete_many({'$or': [{'recipient': current_username}, {'actor': current_username}]})

    # 7. Hapus keanggotaan komunitas jika ada
    if hasattr(db, 'community_members'):
        db.community_members.delete_many({'user_id': current_username})

    # 8. Hapus dokumen akun user
    db.users.delete_one({'username': current_username})

    response = jsonify({
        'result': 'success',
        'msg': 'Akun Anda dan seluruh data terkait telah berhasil dihapus secara permanen.'
    })
    response.delete_cookie('mytoken', path='/')
    return response, 200


@app.route('/api/account/logout_all_sessions', methods=['POST'])
def logout_all_sessions():
    """
    Mengakhiri seluruh sesi aktif di semua perangkat dengan menginkrementasikan token_version.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    user_doc = db.users.find_one({'username': current_username})
    if not user_doc:
        return jsonify({'result': 'fail', 'msg': 'User not found'}), 404

    new_version = user_doc.get('token_version', 1) + 1
    db.users.update_one({'username': current_username}, {'$set': {'token_version': new_version}})

    response = jsonify({
        'result': 'success',
        'msg': 'Berhasil keluar dari seluruh sesi di semua perangkat.'
    })
    response.delete_cookie('mytoken', path='/')
    return response, 200


# ==========================================
# BLOCK, MUTE, REPORT & MODERATION SUBSYSTEM
# ==========================================

VALID_REPORT_REASONS = {
    'spam': 'Spam',
    'harassment': 'Harassment or Bullying',
    'hate_speech': 'Hate Speech',
    'inappropriate_content': 'Inappropriate Content',
    'impersonation': 'Impersonation',
    'misinformation': 'Misinformation',
    'other': 'Other'
}


@app.route('/api/block', methods=['POST'])
@app.route('/api/users/<target_username>/block', methods=['POST'])
def block_user(target_username=None):
    """
    Endpoint untuk memblokir pengguna lain.
    Ketika User A memblokir User B:
    - User B tidak dapat mengikuti User A.
    - User A dan User B otomatis saling berhenti mengikuti jika sebelumnya memiliki relasi follow.
    - Konten, interaksi, profil, komentar, dan notifikasi saling terisolasi.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    target = _extract_target_username(target_username)
    if not target:
        return jsonify({'result': 'fail', 'msg': 'Target username is required'}), 400

    if current_username == target:
        return jsonify({'result': 'fail', 'msg': 'You cannot block yourself'}), 400

    target_user = db.users.find_one({'username': target})
    if not target_user:
        return jsonify({'result': 'fail', 'msg': 'User not found'}), 404

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        db.blocks.insert_one({
            'blocker': current_username,
            'blocked': target,
            'created_at': now_iso
        })
    except DuplicateKeyError:
        pass  # Idempotent

    # Otomatis saling berhenti mengikuti (mutual unfollow)
    db.follows.delete_many({
        '$or': [
            {'follower': current_username, 'following': target},
            {'follower': target, 'following': current_username}
        ]
    })

    return jsonify({
        'result': 'success',
        'msg': f'Successfully blocked @{target}',
        'target_username': target,
        'is_blocked': True
    }), 200


@app.route('/api/unblock', methods=['POST'])
@app.route('/api/users/<target_username>/unblock', methods=['POST'])
def unblock_user(target_username=None):
    """
    Endpoint untuk membuka blokir (unblock) pengguna lain.
    Unblock tidak otomatis mengembalikan relasi follow sebelumnya.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    target = _extract_target_username(target_username)
    if not target:
        return jsonify({'result': 'fail', 'msg': 'Target username is required'}), 400

    if current_username == target:
        return jsonify({'result': 'fail', 'msg': 'You cannot unblock yourself'}), 400

    db.blocks.delete_one({'blocker': current_username, 'blocked': target})

    return jsonify({
        'result': 'success',
        'msg': f'Successfully unblocked @{target}',
        'target_username': target,
        'is_blocked': False
    }), 200


@app.route('/api/blocked_users', methods=['GET'])
def get_blocked_users_api():
    """Mengambil daftar akun yang diblokir oleh pengguna aktif."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    block_docs = list(db.blocks.find({'blocker': current_username}).sort('created_at', -1))
    blocked_usernames = [b['blocked'] for b in block_docs]

    user_docs = {
        u['username']: u
        for u in db.users.find({'username': {'$in': blocked_usernames}}, {'password': 0})
    }

    blocked_list = []
    for b in block_docs:
        uname = b['blocked']
        u = user_docs.get(uname)
        blocked_list.append({
            'username': uname,
            'profile_name': u.get('profile_name', uname) if u else uname,
            'profile_pic_real': u.get('profile_pic_real', 'profile_pics/profile_placeholder.png') if u else 'profile_pics/profile_placeholder.png',
            'profile_info': u.get('profile_info', '') if u else '',
            'blocked_at': b.get('created_at', '')
        })

    return jsonify({
        'result': 'success',
        'total': len(blocked_list),
        'blocked_users': blocked_list
    }), 200


@app.route('/blocked', methods=['GET'])
def blocked_users_page():
    """Halaman pengaturan manajemen akun yang diblokir."""
    payload = get_current_user_from_token()
    if not payload:
        return redirect(url_for('login'))

    current_username = payload.get('id')
    user_info = db.users.find_one({'username': current_username})
    if not user_info:
        return redirect(url_for('login'))

    unread_count = get_unread_count(current_username)
    is_admin = is_admin_user(current_username)
    return render_template(
        'blocked_users.html',
        user_info=user_info,
        unread_count=unread_count,
        is_admin=is_admin
    )


@app.route('/api/mute', methods=['POST'])
@app.route('/api/users/<target_username>/mute', methods=['POST'])
def mute_user(target_username=None):
    """
    Endpoint untuk me-mute pengguna lain.
    Mute menyembunyikan postingan, repost, quote, notifikasi dari timeline tanpa mengubah relasi follow.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    target = _extract_target_username(target_username)
    if not target:
        return jsonify({'result': 'fail', 'msg': 'Target username is required'}), 400

    if current_username == target:
        return jsonify({'result': 'fail', 'msg': 'You cannot mute yourself'}), 400

    target_user = db.users.find_one({'username': target})
    if not target_user:
        return jsonify({'result': 'fail', 'msg': 'User not found'}), 404

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        db.mutes.insert_one({
            'muter': current_username,
            'muted': target,
            'created_at': now_iso
        })
    except DuplicateKeyError:
        pass  # Idempotent

    return jsonify({
        'result': 'success',
        'msg': f'Successfully muted @{target}',
        'target_username': target,
        'is_muted': True
    }), 200


@app.route('/api/unmute', methods=['POST'])
@app.route('/api/users/<target_username>/unmute', methods=['POST'])
def unmute_user(target_username=None):
    """
    Endpoint untuk membuka mute (unmute) pengguna lain.
    Unmute mengembalikan visibilitas konten pengguna tanpa mengubah relasi follow atau status block.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    target = _extract_target_username(target_username)
    if not target:
        return jsonify({'result': 'fail', 'msg': 'Target username is required'}), 400

    if current_username == target:
        return jsonify({'result': 'fail', 'msg': 'You cannot unmute yourself'}), 400

    db.mutes.delete_one({'muter': current_username, 'muted': target})

    return jsonify({
        'result': 'success',
        'msg': f'Successfully unmuted @{target}',
        'target_username': target,
        'is_muted': False
    }), 200


@app.route('/api/muted_users', methods=['GET'])
def get_muted_users_api():
    """Mengambil daftar akun yang di-mute oleh pengguna aktif."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    mute_docs = list(db.mutes.find({'muter': current_username}).sort('created_at', -1))
    muted_usernames = [m['muted'] for m in mute_docs]

    user_docs = {
        u['username']: u
        for u in db.users.find({'username': {'$in': muted_usernames}}, {'password': 0})
    }

    muted_list = []
    for m in mute_docs:
        uname = m['muted']
        u = user_docs.get(uname)
        muted_list.append({
            'username': uname,
            'profile_name': u.get('profile_name', uname) if u else uname,
            'profile_pic_real': u.get('profile_pic_real', 'profile_pics/profile_placeholder.png') if u else 'profile_pics/profile_placeholder.png',
            'profile_info': u.get('profile_info', '') if u else '',
            'muted_at': m.get('created_at', '')
        })

    return jsonify({
        'result': 'success',
        'total': len(muted_list),
        'muted_users': muted_list
    }), 200


@app.route('/muted', methods=['GET'])
def muted_users_page():
    """Halaman pengaturan manajemen akun yang di-mute."""
    payload = get_current_user_from_token()
    if not payload:
        return redirect(url_for('login'))

    current_username = payload.get('id')
    user_info = db.users.find_one({'username': current_username})
    if not user_info:
        return redirect(url_for('login'))

    unread_count = get_unread_count(current_username)
    is_admin = is_admin_user(current_username)
    return render_template(
        'muted_users.html',
        user_info=user_info,
        unread_count=unread_count,
        is_admin=is_admin
    )


@app.route('/api/users/<target_username>/relationship', methods=['GET'])
def user_relationship_status(target_username=None):
    """
    Memeriksa status interaksi sosial antara pengguna saat ini dan target:
    - is_following
    - is_followed_by
    - is_blocked_by_me
    - is_blocking_me
    - is_muted_by_me
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    target = _extract_target_username(target_username)
    if not target:
        return jsonify({'result': 'fail', 'msg': 'Target username is required'}), 400

    target_user = db.users.find_one({'username': target})
    if not target_user:
        return jsonify({'result': 'fail', 'msg': 'User not found'}), 404

    is_following_val = is_following(current_username, target)
    is_followed_by_val = is_following(target, current_username)
    is_blocked_by_me_val = is_blocked(current_username, target)
    is_blocking_me_val = is_blocked(target, current_username)
    is_muted_by_me_val = is_muted(current_username, target)

    return jsonify({
        'result': 'success',
        'current_username': current_username,
        'target_username': target,
        'is_following': is_following_val,
        'is_followed_by': is_followed_by_val,
        'is_blocked_by_me': is_blocked_by_me_val,
        'is_blocking_me': is_blocking_me_val,
        'is_muted_by_me': is_muted_by_me_val
    }), 200


@app.route('/api/reports', methods=['POST'])
def submit_report():
    """
    Endpoint pelaporan konten atau profil yang melanggar aturan komunitas:
    - Target: user, post, comment, reply
    - Alasan: spam, harassment, hate_speech, inappropriate_content, impersonation, misinformation, other
    - Validasi keberadaan target, batas panjang deskripsi, proteksi duplikasi, dan keamanan XSS.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')

    # Ekstraksi input dari JSON atau Form-data
    target_type = None
    target_id = None
    reason = None
    description = ""

    if request.is_json:
        data = request.get_json(silent=True) or {}
        target_type = data.get('target_type')
        target_id = data.get('target_id')
        reason = data.get('reason')
        description = data.get('description', '')
    else:
        target_type = request.form.get('target_type')
        target_id = request.form.get('target_id')
        reason = request.form.get('reason')
        description = request.form.get('description', '')

    target_type = str(target_type).strip().lower() if target_type else ''
    target_id = str(target_id).strip() if target_id else ''
    reason = str(reason).strip().lower() if reason else ''
    description = str(description).strip() if description else ''

    # Validasi kelengkapan parameter
    if not target_type or target_type not in ('user', 'post', 'comment', 'reply'):
        return jsonify({'result': 'fail', 'msg': 'Invalid target_type. Must be user, post, comment, or reply'}), 400

    if not target_id:
        return jsonify({'result': 'fail', 'msg': 'target_id is required'}), 400

    if reason not in VALID_REPORT_REASONS:
        return jsonify({
            'result': 'fail',
            'msg': f'Invalid report reason. Must be one of: {", ".join(VALID_REPORT_REASONS.keys())}'
        }), 400

    if len(description) > 1000:
        return jsonify({'result': 'fail', 'msg': 'Description exceeds maximum length of 1000 characters'}), 400

    # Validasi keberadaan target & cegah pelaporan terhadap konten diri sendiri
    target_author = None
    snapshot = {}

    if target_type == 'user':
        if target_id == current_username:
            return jsonify({'result': 'fail', 'msg': 'You cannot report yourself'}), 400
        target_user = db.users.find_one({'username': target_id})
        if not target_user:
            return jsonify({'result': 'fail', 'msg': 'Target user not found'}), 404
        target_author = target_id
        snapshot = {
            'username': target_id,
            'profile_name': target_user.get('profile_name', target_id),
            'profile_info': target_user.get('profile_info', '')
        }
    elif target_type == 'post':
        if not ObjectId.is_valid(target_id):
            return jsonify({'result': 'fail', 'msg': 'Invalid post ID format'}), 400
        target_post = db.posts.find_one({'_id': ObjectId(target_id)})
        if not target_post:
            return jsonify({'result': 'fail', 'msg': 'Target post not found'}), 404
        if target_post.get('username') == current_username:
            return jsonify({'result': 'fail', 'msg': 'You cannot report your own post'}), 400
        target_author = target_post.get('username')
        snapshot = {
            'author': target_author,
            'comment': target_post.get('comment', ''),
            'post_type': target_post.get('post_type', 'original'),
            'date': target_post.get('date', ''),
            'image_count': len(target_post.get('images') or [])
        }
    elif target_type in ('comment', 'reply'):
        if not ObjectId.is_valid(target_id):
            return jsonify({'result': 'fail', 'msg': 'Invalid comment ID format'}), 400
        target_comment = db.comments.find_one({'_id': ObjectId(target_id)})
        if not target_comment:
            return jsonify({'result': 'fail', 'msg': 'Target comment not found'}), 404
        if target_comment.get('username') == current_username:
            return jsonify({'result': 'fail', 'msg': 'You cannot report your own comment'}), 400
        target_author = target_comment.get('username')
        snapshot = {
            'author': target_author,
            'post_id': str(target_comment.get('post_id')),
            'parent_comment_id': str(target_comment.get('parent_comment_id')) if target_comment.get('parent_comment_id') else None,
            'content': target_comment.get('content', ''),
            'created_at': target_comment.get('created_at', '')
        }

    # Pencegahan laporan duplikat yang sedang aktif
    existing_report = db.reports.find_one({
        'reporter_id': current_username,
        'target_type': target_type,
        'target_id': target_id,
        'status': {'$in': ['pending', 'under_review']}
    })
    if existing_report:
        return jsonify({
            'result': 'fail',
            'msg': 'You have already submitted a report for this target that is currently under review.'
        }), 409

    now_iso = datetime.now(timezone.utc).isoformat()
    report_doc = {
        'reporter_id': current_username,
        'target_type': target_type,
        'target_id': target_id,
        'target_author': target_author,
        'target_snapshot': snapshot,
        'reason': reason,
        'reason_label': VALID_REPORT_REASONS[reason],
        'description': description,
        'status': 'pending',  # pending, under_review, resolved, rejected
        'created_at': now_iso,
        'updated_at': now_iso,
        'assigned_admin': None,
        'moderation_notes': [],
        'resolved_by': None,
        'resolved_at': None
    }

    insert_res = db.reports.insert_one(report_doc)
    return jsonify({
        'result': 'success',
        'msg': 'Report submitted successfully. Thank you for helping keep Sweeter safe.',
        'report_id': str(insert_res.inserted_id)
    }), 201


# ==========================================
# ADMIN MODERATION DASHBOARD & ACTIONS
# ==========================================

@app.route('/admin/reports', methods=['GET'])
def admin_reports_page():
    """Halaman Dashboard Moderasi Khusus Administrator."""
    payload = get_current_user_from_token()
    if not payload:
        return redirect(url_for('login'))

    current_username = payload.get('id')
    if not is_admin_user(current_username):
        return render_template('404.html', username=current_username), 403

    user_info = db.users.find_one({'username': current_username})
    unread_count = get_unread_count(current_username)
    return render_template(
        'admin_reports.html',
        user_info=user_info,
        unread_count=unread_count,
        is_admin=True
    )


@app.route('/api/admin/reports', methods=['GET'])
def get_admin_reports_api():
    """
    API JSON untuk mengambil daftar laporan dan metrik moderasi bagi Administrator.
    Memeriksa otorisasi administrator secara ketat pada sisi server.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    if not is_admin_user(current_username):
        return jsonify({'result': 'fail', 'msg': 'Administrator privileges required'}), 403

    status_filter = request.args.get('status', 'all').strip().lower()
    reason_filter = request.args.get('reason', '').strip().lower()
    target_type_filter = request.args.get('target_type', '').strip().lower()
    limit_raw = request.args.get('limit', '50').strip()
    page_raw = request.args.get('page', '1').strip()

    try:
        limit = max(1, min(int(limit_raw), 100))
    except ValueError:
        limit = 50

    try:
        page = max(1, int(page_raw))
    except ValueError:
        page = 1

    skip = (page - 1) * limit

    query = {}
    if status_filter and status_filter != 'all':
        query['status'] = status_filter
    if reason_filter:
        query['reason'] = reason_filter
    if target_type_filter:
        query['target_type'] = target_type_filter

    total_count = db.reports.count_documents(query)
    reports_cursor = db.reports.find(query).sort('created_at', -1).skip(skip).limit(limit)

    reports_list = []
    for r in reports_cursor:
        r_id = str(r['_id'])
        r['_id'] = r_id
        reports_list.append(r)

    # Statistik global untuk dashboard admin
    stats = {
        'total': db.reports.count_documents({}),
        'pending': db.reports.count_documents({'status': 'pending'}),
        'under_review': db.reports.count_documents({'status': 'under_review'}),
        'resolved': db.reports.count_documents({'status': 'resolved'}),
        'rejected': db.reports.count_documents({'status': 'rejected'}),
    }

    return jsonify({
        'result': 'success',
        'reports': reports_list,
        'stats': stats,
        'metrics': stats,
        'total': total_count,
        'page': page,
        'limit': limit
    }), 200


@app.route('/api/admin/reports/<report_id>/status', methods=['POST'])
def update_report_status(report_id):
    """
    Mengubah status laporan (pending, under_review, resolved, rejected)
    serta menambahkan catatan moderasi secara aman.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    if not is_admin_user(current_username):
        return jsonify({'result': 'fail', 'msg': 'Administrator privileges required'}), 403

    if not report_id or not ObjectId.is_valid(report_id):
        return jsonify({'result': 'fail', 'msg': 'Invalid report ID format'}), 400

    report = db.reports.find_one({'_id': ObjectId(report_id)})
    if not report:
        return jsonify({'result': 'fail', 'msg': 'Report not found'}), 404

    data = request.get_json(silent=True) or request.form or {}
    new_status = (data.get('status') or '').strip().lower()
    notes = (data.get('notes') or '').strip()

    if new_status not in ('pending', 'under_review', 'resolved', 'rejected'):
        return jsonify({'result': 'fail', 'msg': 'Invalid status. Must be pending, under_review, resolved, or rejected'}), 400

    now_iso = datetime.now(timezone.utc).isoformat()
    update_doc = {
        'status': new_status,
        'updated_at': now_iso
    }
    if new_status in ('resolved', 'rejected'):
        update_doc['resolved_by'] = current_username
        update_doc['resolved_at'] = now_iso

    note_entry = None
    if notes:
        note_entry = {
            'admin': current_username,
            'note': notes,
            'timestamp': now_iso,
            'status_at_note': new_status
        }
        db.reports.update_one(
            {'_id': ObjectId(report_id)},
            {
                '$set': update_doc,
                '$push': {'moderation_notes': note_entry}
            }
        )
    else:
        db.reports.update_one({'_id': ObjectId(report_id)}, {'$set': update_doc})

    # Simpan jejak audit log
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr or '')
    db.moderation_logs.insert_one({
        'admin_id': current_username,
        'report_id': report_id,
        'target_type': report.get('target_type'),
        'target_id': report.get('target_id'),
        'action': f'change_status_to_{new_status}',
        'reason': notes or f'Status updated to {new_status}',
        'timestamp': now_iso,
        'ip_address': client_ip
    })

    return jsonify({
        'result': 'success',
        'msg': f'Report status updated to {new_status}',
        'report_id': report_id,
        'status': new_status
    }), 200


@app.route('/api/admin/reports/<report_id>/action', methods=['POST'])
def take_moderation_action(report_id):
    """
    Eksekusi tindakan moderasi langsung oleh Administrator:
    - dismiss: tolak laporan (rejected)
    - resolve: selesaikan laporan (resolved)
    - remove_post: hapus postingan target dengan cascading cleanup menyeluruh
    - remove_comment: soft-delete komentar/reply target
    - suspend_user: tangguhkan akun pengguna yang dilaporkan
    - unsuspend_user: pulihkan akun yang ditangguhkan
    Mencatat jejak audit log lengkap untuk setiap aksi.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    if not is_admin_user(current_username):
        return jsonify({'result': 'fail', 'msg': 'Administrator privileges required'}), 403

    if not report_id or not ObjectId.is_valid(report_id):
        return jsonify({'result': 'fail', 'msg': 'Invalid report ID format'}), 400

    report = db.reports.find_one({'_id': ObjectId(report_id)})
    if not report:
        return jsonify({'result': 'fail', 'msg': 'Report not found'}), 404

    data = request.get_json(silent=True) or request.form or {}
    action = (data.get('action') or '').strip().lower()
    if action == 'dismiss_report':
        action = 'dismiss'
    reason = (data.get('reason') or data.get('notes') or '').strip()

    if not action or action not in ('dismiss', 'resolve', 'remove_post', 'remove_comment', 'suspend_user', 'unsuspend_user'):
        return jsonify({
            'result': 'fail',
            'msg': 'Invalid action. Must be dismiss, resolve, remove_post, remove_comment, suspend_user, or unsuspend_user'
        }), 400

    now_iso = datetime.now(timezone.utc).isoformat()
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr or '')
    target_type = report.get('target_type')
    target_id = report.get('target_id')

    # 1. Action: dismiss (Tolak laporan)
    if action == 'dismiss':
        db.reports.update_one(
            {'_id': ObjectId(report_id)},
            {
                '$set': {
                    'status': 'rejected',
                    'updated_at': now_iso,
                    'resolved_by': current_username,
                    'resolved_at': now_iso
                },
                '$push': {
                    'moderation_notes': {
                        'admin': current_username,
                        'note': reason or 'Report dismissed by administrator',
                        'timestamp': now_iso,
                        'action': 'dismiss'
                    }
                }
            }
        )

    # 2. Action: resolve (Selesaikan laporan tanpa menghapus)
    elif action == 'resolve':
        db.reports.update_one(
            {'_id': ObjectId(report_id)},
            {
                '$set': {
                    'status': 'resolved',
                    'updated_at': now_iso,
                    'resolved_by': current_username,
                    'resolved_at': now_iso
                },
                '$push': {
                    'moderation_notes': {
                        'admin': current_username,
                        'note': reason or 'Report resolved by administrator',
                        'timestamp': now_iso,
                        'action': 'resolve'
                    }
                }
            }
        )

    # 3. Action: remove_post (Hapus postingan dengan cascading cleanup)
    elif action == 'remove_post':
        if target_type != 'post' or not ObjectId.is_valid(target_id):
            return jsonify({'result': 'fail', 'msg': 'Target is not a valid post'}), 400

        target_post = db.posts.find_one({'_id': ObjectId(target_id)})
        if target_post:
            # Hapus dokumen postingan
            db.posts.delete_one({'_id': ObjectId(target_id)})
            # Bersihkan referensi terkait (bookmarks, reactions, comments, reposts, notifications, images)
            db.bookmarks.delete_many({'post_id': target_id})
            db.likes.delete_many({'post_id': target_id})
            db.stars.delete_many({'post_id': target_id})
            db.thumbs.delete_many({'post_id': target_id})
            db.comments.delete_many({'post_id': target_id})
            db.posts.delete_many({'original_post_id': target_id, 'post_type': 'repost'})
            cleanup_notifications_for_post(target_id)

            if target_post.get('post_type') != 'repost':
                for img in (target_post.get('images') or []):
                    if isinstance(img, dict) and img.get('filename'):
                        safe_delete_post_image(img['filename'], check_db_refs=True)

        db.reports.update_one(
            {'_id': ObjectId(report_id)},
            {
                '$set': {
                    'status': 'resolved',
                    'updated_at': now_iso,
                    'resolved_by': current_username,
                    'resolved_at': now_iso
                },
                '$push': {
                    'moderation_notes': {
                        'admin': current_username,
                        'note': reason or 'Target post was removed by administrator',
                        'timestamp': now_iso,
                        'action': 'remove_post'
                    }
                }
            }
        )

    # 4. Action: remove_comment (Soft-delete komentar/reply)
    elif action == 'remove_comment':
        if target_type not in ('comment', 'reply') or not ObjectId.is_valid(target_id):
            return jsonify({'result': 'fail', 'msg': 'Target is not a valid comment'}), 400

        db.comments.update_one(
            {'_id': ObjectId(target_id)},
            {
                '$set': {
                    'is_deleted': True,
                    'content': '[Komentar ini telah dihapus oleh tim moderasi]',
                    'updated_at': now_iso
                }
            }
        )

        db.reports.update_one(
            {'_id': ObjectId(report_id)},
            {
                '$set': {
                    'status': 'resolved',
                    'updated_at': now_iso,
                    'resolved_by': current_username,
                    'resolved_at': now_iso
                },
                '$push': {
                    'moderation_notes': {
                        'admin': current_username,
                        'note': reason or 'Target comment was removed by administrator',
                        'timestamp': now_iso,
                        'action': 'remove_comment'
                    }
                }
            }
        )

    # 5. Action: suspend_user (Tangguhkan akun pengguna)
    elif action == 'suspend_user':
        target_uname = report.get('target_author') or (target_id if target_type == 'user' else None)
        if not target_uname:
            return jsonify({'result': 'fail', 'msg': 'Cannot identify target user to suspend'}), 400

        db.users.update_one(
            {'username': target_uname},
            {
                '$set': {
                    'is_suspended': True,
                    'suspended_at': now_iso,
                    'suspended_by': current_username,
                    'suspension_reason': reason or 'Suspended due to community violation'
                }
            }
        )

        db.reports.update_one(
            {'_id': ObjectId(report_id)},
            {
                '$set': {
                    'status': 'resolved',
                    'updated_at': now_iso,
                    'resolved_by': current_username,
                    'resolved_at': now_iso
                },
                '$push': {
                    'moderation_notes': {
                        'admin': current_username,
                        'note': reason or f'User @{target_uname} was suspended by administrator',
                        'timestamp': now_iso,
                        'action': 'suspend_user'
                    }
                }
            }
        )

    # 6. Action: unsuspend_user (Pulihkan penangguhan akun)
    elif action == 'unsuspend_user':
        target_uname = report.get('target_author') or (target_id if target_type == 'user' else None)
        if not target_uname:
            return jsonify({'result': 'fail', 'msg': 'Cannot identify target user to unsuspend'}), 400

        db.users.update_one(
            {'username': target_uname},
            {
                '$set': {
                    'is_suspended': False,
                    'unsuspended_at': now_iso,
                    'unsuspended_by': current_username
                }
            }
        )

        db.reports.update_one(
            {'_id': ObjectId(report_id)},
            {
                '$set': {
                    'updated_at': now_iso
                },
                '$push': {
                    'moderation_notes': {
                        'admin': current_username,
                        'note': reason or f'User @{target_uname} suspension was lifted by administrator',
                        'timestamp': now_iso,
                        'action': 'unsuspend_user'
                    }
                }
            }
        )

    # Catat audit log untuk setiap tindakan
    db.moderation_logs.insert_one({
        'admin': current_username,
        'admin_id': current_username,
        'report_id': report_id,
        'target_type': target_type,
        'target_id': target_id,
        'action': action,
        'reason': reason or f'Executed moderation action: {action}',
        'timestamp': now_iso,
        'ip_address': client_ip
    })

    return jsonify({
        'result': 'success',
        'msg': f'Action {action} executed successfully',
        'report_id': report_id,
        'action': action
    }), 200


@app.route('/api/admin/audit_logs', methods=['GET'])
def get_admin_audit_logs():
    """Mengambil riwayat audit log tindakan moderasi admin."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    if not is_admin_user(current_username):
        return jsonify({'result': 'fail', 'msg': 'Administrator privileges required'}), 403

    limit_raw = request.args.get('limit', '50').strip()
    try:
        limit = max(1, min(int(limit_raw), 100))
    except ValueError:
        limit = 50

    logs_cursor = db.moderation_logs.find().sort('timestamp', -1).limit(limit)
    logs_list = []
    for l in logs_cursor:
        l['_id'] = str(l['_id'])
        logs_list.append(l)

    return jsonify({
        'result': 'success',
        'logs': logs_list,
        'audit_logs': logs_list,
        'total': len(logs_list)
    }), 200


# ==========================================
# BOOKMARKS & POST MANAGEMENT (SWEETER V2)
# ==========================================

def _extract_post_id(param_post_id=None):
    """Mengekstrak post_id dari URL parameter, form-data, atau JSON body."""
    if param_post_id and isinstance(param_post_id, str) and param_post_id.strip():
        return param_post_id.strip()

    if request.is_json:
        json_data = request.get_json(silent=True) or {}
        val = json_data.get('post_id') or json_data.get('post_id_give')
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    form_val = request.form.get('post_id_give') or request.form.get('post_id')
    if form_val and isinstance(form_val, str) and form_val.strip():
        return form_val.strip()

    arg_val = request.args.get('post_id') or request.args.get('post_id_give')
    if arg_val and isinstance(arg_val, str) and arg_val.strip():
        return arg_val.strip()

    return None


@app.route('/api/bookmark', methods=['POST'])
@app.route('/api/posts/<post_id>/bookmark', methods=['POST'])
def bookmark_post(post_id=None):
    """
    Endpoint untuk menyimpan (bookmark) suatu postingan.
    Mencegah duplikasi bookmark secara race-safe melalui compound unique index.
    """
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    target_pid = _extract_post_id(post_id)
    if not target_pid:
        return jsonify({'result': 'fail', 'msg': 'Post ID is required'}), 400

    if not ObjectId.is_valid(target_pid):
        return jsonify({'result': 'fail', 'msg': 'Invalid post ID format'}), 400

    # Pastikan postingan ada di db.posts dan user berhak mengaksesnya
    post_doc = db.posts.find_one({'_id': ObjectId(target_pid)})
    if not post_doc:
        return jsonify({'result': 'fail', 'msg': 'Post not found'}), 404
    if not _user_can_access_post(post_doc, current_username):
        return jsonify({'result': 'fail', 'msg': 'Anda tidak memiliki akses ke postingan komunitas privat ini.'}), 403

    # Cegah duplikasi bookmark
    existing = db.bookmarks.find_one({'username': current_username, 'post_id': target_pid})
    if existing:
        return jsonify({
            'result': 'fail',
            'msg': 'Post already bookmarked',
            'is_bookmarked': True,
            'post_id': target_pid
        }), 409

    now = datetime.now(timezone.utc).isoformat()
    try:
        db.bookmarks.insert_one({
            'username': current_username,
            'post_id': target_pid,
            'created_at': now
        })
    except DuplicateKeyError:
        return jsonify({
            'result': 'fail',
            'msg': 'Post already bookmarked',
            'is_bookmarked': True,
            'post_id': target_pid
        }), 409

    return jsonify({
        'result': 'success',
        'msg': 'Post bookmarked successfully',
        'is_bookmarked': True,
        'post_id': target_pid
    }), 200


@app.route('/api/unbookmark', methods=['POST'])
@app.route('/api/posts/<post_id>/unbookmark', methods=['POST'])
def unbookmark_post(post_id=None):
    """
    Endpoint untuk menghapus bookmark postingan.
    Idempotent dan aman meskipun bookmark belum pernah disimpan.
    """
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    target_pid = _extract_post_id(post_id)
    if not target_pid:
        return jsonify({'result': 'fail', 'msg': 'Post ID is required'}), 400

    if not ObjectId.is_valid(target_pid):
        return jsonify({'result': 'fail', 'msg': 'Invalid post ID format'}), 400

    db.bookmarks.delete_one({'username': current_username, 'post_id': target_pid})

    return jsonify({
        'result': 'success',
        'msg': 'Bookmark removed successfully',
        'is_bookmarked': False,
        'post_id': target_pid
    }), 200


@app.route('/api/bookmarks', methods=['GET'])
def get_bookmarks_api():
    """
    Mengambil daftar postingan yang di-bookmark oleh pengguna saat ini (Private).
    Mendukung keyset cursor pagination dan menangani postingan yang telah dihapus.
    """
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    cursor = request.args.get('cursor', '').strip()
    limit_raw = request.args.get('limit', '20').strip()

    try:
        limit = int(limit_raw)
        if limit <= 0 or limit > 50:
            limit = 20
    except ValueError:
        return jsonify({'result': 'fail', 'msg': 'Invalid limit parameter'}), 400

    b_query = {'username': current_username}
    if cursor:
        if ObjectId.is_valid(cursor):
            anchor = db.bookmarks.find_one({'_id': ObjectId(cursor)})
            if anchor:
                c_date = anchor.get('created_at', '')
                c_id = anchor['_id']
                b_query['$or'] = [
                    {'created_at': {'$lt': c_date}},
                    {'created_at': c_date, '_id': {'$lt': c_id}}
                ]
            else:
                return jsonify({'result': 'fail', 'msg': 'Cursor not found'}), 404
        elif cursor.count('-') >= 2 or cursor.count('.') >= 2:
            b_query['created_at'] = {'$lt': cursor}
        else:
            return jsonify({'result': 'fail', 'msg': 'Invalid cursor parameter'}), 400

    bookmark_docs = list(db.bookmarks.find(b_query).sort([('created_at', -1), ('_id', -1)]).limit(limit))

    # Ambil postingan terkait dari db.posts
    post_ids = [b['post_id'] for b in bookmark_docs if ObjectId.is_valid(b.get('post_id', ''))]
    post_obj_ids = [ObjectId(pid) for pid in post_ids]
    posts_map = {str(p['_id']): p for p in db.posts.find({'_id': {'$in': post_obj_ids}})}

    valid_posts = []
    stale_bookmark_ids = []
    for b in bookmark_docs:
        pid = b['post_id']
        if pid in posts_map:
            valid_posts.append(posts_map[pid])
        else:
            # Postingan telah dihapus: tandai stale bookmark untuk dibersihkan
            stale_bookmark_ids.append(b['_id'])

    # Pembersihan bookmark yang usang (postingan sudah tidak ada)
    if stale_bookmark_ids:
        db.bookmarks.delete_many({'_id': {'$in': stale_bookmark_ids}})

    # Perkaya postingan dengan metadata komentar, reaksi, kepemilikan, dan bookmark
    valid_posts = enrich_posts_metadata(valid_posts, current_username)

    has_more = (len(bookmark_docs) == limit)
    next_cursor = str(bookmark_docs[-1]['_id']) if has_more else None

    return jsonify({
        'result': 'success',
        'msg': 'Successfully fetched bookmarked posts',
        'posts': valid_posts,
        'next_cursor': next_cursor,
        'has_more': has_more
    }), 200


@app.route('/bookmarks', methods=['GET'])
def bookmarks_page():
    """Halaman web daftar postingan tersimpan (Bookmarks)."""
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return redirect(url_for('login'))

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
        user_info = db.users.find_one({'username': current_username}, {'_id': False})
        if not user_info:
            return redirect(url_for('login'))

        bookmark_count = db.bookmarks.count_documents({'username': current_username})
        return render_template(
            'bookmarks.html',
            user_info=user_info,
            current_user=user_info,
            bookmark_count=bookmark_count
        )
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return redirect(url_for('login'))


# ==========================================
# NOTIFICATION SYSTEM (SWEETER V2)
# ==========================================

@app.route('/api/notifications', methods=['GET'])
def get_notifications_api():
    """
    Mengambil daftar notifikasi milik pengguna saat ini dengan pagination keyset cursor.
    Diperkaya dengan profil actor dan snippet target.
    """
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    cursor = request.args.get('cursor', '').strip()
    limit = request.args.get('limit', '20').strip()

    data = get_user_notifications(current_username, cursor=cursor, limit=limit)
    return jsonify({
        'result': 'success',
        'notifications': data['notifications'],
        'next_cursor': data['next_cursor'],
        'has_more': data['has_more'],
        'unread_count': data['unread_count']
    }), 200


@app.route('/api/notifications/unread_count', methods=['GET'])
def get_notifications_unread_count_api():
    """Mengambil jumlah notifikasi belum dibaca untuk pengguna aktif."""
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    count = get_unread_count(current_username)
    return jsonify({
        'result': 'success',
        'unread_count': count
    }), 200


@app.route('/api/notifications/<notification_id>/read', methods=['POST'])
def mark_notification_read_api(notification_id):
    """
    Menandai satu notifikasi sebagai telah dibaca.
    Memeriksa otentikasi, validitas ID, dan otorisasi kepemilikan (403 jika bukan pemilik).
    """
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    res = mark_notification_as_read(notification_id, current_username)
    if res['code'] != 200:
        return jsonify({'result': 'fail', 'msg': res['msg']}), res['code']

    return jsonify({
        'result': 'success',
        'msg': res['msg'],
        'unread_count': res['unread_count']
    }), 200


@app.route('/api/notifications/read_all', methods=['POST'])
def mark_all_notifications_read_api():
    """Menandai seluruh notifikasi milik pengguna aktif sebagai telah dibaca."""
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    res = mark_all_notifications_as_read(current_username)
    return jsonify({
        'result': 'success',
        'msg': res['msg'],
        'modified_count': res['modified_count'],
        'unread_count': 0
    }), 200


@app.route('/notifications', methods=['GET'])
def notifications_page():
    """Halaman web Notification Center."""
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return redirect(url_for('login'))

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
        user_info = db.users.find_one({'username': current_username}, {'_id': False})
        if not user_info:
            return redirect(url_for('login'))

        unread_count = get_unread_count(current_username)
        return render_template(
            'notifications.html',
            user_info=user_info,
            current_user=user_info,
            unread_count=unread_count
        )
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return redirect(url_for('login'))


@app.route('/api/posts/<post_id>/edit', methods=['POST'])
@app.route('/edit_post', methods=['POST'])
def edit_post(post_id=None):
    """
    Endpoint pengeditan isi postingan milik pengguna sendiri.
    Memeriksa hak kepemilikan (403 jika bukan pemilik) dan validasi konten.
    """
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    target_pid = _extract_post_id(post_id)
    if not target_pid:
        return jsonify({'result': 'fail', 'msg': 'Post ID is required'}), 400

    if not ObjectId.is_valid(target_pid):
        return jsonify({'result': 'fail', 'msg': 'Invalid post ID format'}), 400

    post_doc = db.posts.find_one({'_id': ObjectId(target_pid)})
    if not post_doc:
        return jsonify({'result': 'fail', 'msg': 'Post not found'}), 404

    # Otorisasi kepemilikan postingan
    if post_doc.get('username') != current_username:
        return jsonify({'result': 'fail', 'msg': 'You do not have permission to edit this post'}), 403

    # Validasi konten & gambar baru
    new_content = None
    json_body = None
    if request.is_json:
        json_body = request.get_json(silent=True) or {}
        new_content = json_body.get('comment')
    if new_content is None:
        new_content = request.form.get('comment_give')
        if new_content is None:
            new_content = request.form.get('comment')

    if new_content is None:
        new_content = ''
    if not isinstance(new_content, str):
        return jsonify({'result': 'fail', 'msg': 'Invalid post content'}), 400
    new_content = new_content.strip()

    if len(new_content) > 5000:
        return jsonify({'result': 'fail', 'msg': 'Post content exceeds maximum length'}), 400

    existing_images = post_doc.get('images') if isinstance(post_doc.get('images'), list) else []

    # Cek apakah request menyertakan pengelolaan gambar (keep_images atau file baru)
    new_files = []
    for key in ('images', 'images[]', 'file_give'):
        new_files.extend(request.files.getlist(key))

    has_keep_param = (
        (json_body is not None and 'keep_images' in json_body)
        or ('keep_images' in request.form)
    )

    if has_keep_param or new_files:
        if json_body is not None and 'keep_images' in json_body:
            raw_keep = json_body.get('keep_images') or []
            keep_filenames = set(raw_keep if isinstance(raw_keep, list) else [])
        elif 'keep_images' in request.form:
            keep_list = request.form.getlist('keep_images')
            if len(keep_list) == 1 and keep_list[0].strip().startswith('['):
                import json as _json
                try:
                    parsed_keep = _json.loads(keep_list[0])
                    keep_filenames = set(parsed_keep if isinstance(parsed_keep, list) else [])
                except Exception:
                    return jsonify({'result': 'fail', 'msg': 'Invalid keep_images format'}), 400
            else:
                keep_filenames = set(x.strip() for x in keep_list if x.strip())
        else:
            keep_filenames = {img.get('filename') for img in existing_images if img.get('filename')}

        kept_images = [img for img in existing_images if img.get('filename') in keep_filenames]
        removed_filenames = [
            img.get('filename') for img in existing_images
            if img.get('filename') and img.get('filename') not in keep_filenames
        ]
    else:
        kept_images = list(existing_images)
        removed_filenames = []

    if len(kept_images) + len(new_files) > MAX_POST_IMAGES_COUNT:
        return jsonify({
            'result': 'fail',
            'msg': f'Maksimal {MAX_POST_IMAGES_COUNT} gambar dalam satu postingan.'
        }), 400

    if not new_content and (len(kept_images) + len(new_files)) == 0:
        return jsonify({'result': 'fail', 'msg': 'Post content cannot be empty'}), 400

    validated_new = []
    for file_item in new_files:
        try:
            validated_new.append(validate_post_image_file(file_item))
        except ValueError as err:
            return jsonify({'result': 'fail', 'msg': str(err)}), 400

    saved_new_images = []
    try:
        for data_bytes, ext, mime_type, _size in validated_new:
            saved_new_images.append(save_post_image_file(data_bytes, ext, mime_type))

        final_images = kept_images + saved_new_images
        tags = extract_hashtags(new_content)
        now = datetime.now(timezone.utc).isoformat()
        db.posts.update_one(
            {'_id': ObjectId(target_pid)},
            {'$set': {
                'comment': new_content,
                'hashtags': tags,
                'images': final_images,
                'is_edited': True,
                'updated_at': now
            }}
        )
    except Exception as exc:
        # Rollback gambar baru jika update DB gagal, dan biarkan gambar lama tetap aman
        for img in saved_new_images:
            safe_delete_post_image(img.get('filename'), check_db_refs=False)
        return jsonify({'result': 'fail', 'msg': f'Failed to update post: {exc}'}), 500

    # Hapus gambar lama yang sudah tidak dipakai hanya setelah update berhasil disimpan
    for old_fname in removed_filenames:
        safe_delete_post_image(old_fname, check_db_refs=True)

    return jsonify({
        'result': 'success',
        'msg': 'Post updated successfully',
        'post_id': target_pid,
        'comment': new_content,
        'images': final_images,
        'is_edited': True
    }), 200


@app.route('/api/posts/<post_id>/delete', methods=['POST'])
@app.route('/delete_post', methods=['POST'])
def delete_post_endpoint(post_id=None):
    """
    Endpoint penghapusan postingan milik pengguna sendiri.
    Menghapus dokumen post dan membersihkan data referensi terkait (bookmarks, reactions, comments, images).
    """
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    target_pid = _extract_post_id(post_id)
    if not target_pid:
        return jsonify({'result': 'fail', 'msg': 'Post ID is required'}), 400

    if not ObjectId.is_valid(target_pid):
        return jsonify({'result': 'fail', 'msg': 'Invalid post ID format'}), 400

    post_doc = db.posts.find_one({'_id': ObjectId(target_pid)})
    if not post_doc:
        return jsonify({'result': 'fail', 'msg': 'Post not found'}), 404

    # Otorisasi kepemilikan postingan
    if post_doc.get('username') != current_username:
        return jsonify({'result': 'fail', 'msg': 'You do not have permission to delete this post'}), 403

    # Hapus dokumen post dari database
    db.posts.delete_one({'_id': ObjectId(target_pid)})

    # Bersihkan relasi dan dependensi postingan
    db.bookmarks.delete_many({'post_id': target_pid})
    db.likes.delete_many({'post_id': target_pid})
    db.stars.delete_many({'post_id': target_pid})
    db.thumbs.delete_many({'post_id': target_pid})
    db.comments.delete_many({'post_id': target_pid})
    db.posts.delete_many({'original_post_id': target_pid, 'post_type': 'repost'})
    cleanup_notifications_for_post(target_pid)

    # Hapus file gambar milik post ini jika bukan repost dan tidak direferensikan post lain
    if post_doc.get('post_type') != 'repost':
        for img in (post_doc.get('images') or []):
            if isinstance(img, dict) and img.get('filename'):
                safe_delete_post_image(img['filename'], check_db_refs=True)

    return jsonify({
        'result': 'success',
        'msg': 'Post deleted successfully',
        'post_id': target_pid
    }), 200


@app.route('/api/posts/<post_id>', methods=['GET'])
def get_single_post_api(post_id):
    """Mengambil detail satu postingan beserta metadata lengkap (JSON)."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    if not post_id or not ObjectId.is_valid(post_id):
        return jsonify({'result': 'fail', 'msg': 'Invalid post ID format'}), 400

    post_doc = db.posts.find_one({'_id': ObjectId(post_id)})
    if not post_doc:
        return jsonify({'result': 'fail', 'msg': 'Post not found'}), 404

    if not _user_can_access_post(post_doc, payload.get('id')):
        return jsonify({'result': 'fail', 'msg': 'Postingan ini berasal dari komunitas privat.'}), 403

    enriched = enrich_posts_metadata([post_doc], payload.get('id'))
    return jsonify({
        'result': 'success',
        'post': enriched[0]
    }), 200


@app.route('/post/<post_id>', methods=['GET'])
def post_detail_page(post_id):
    """Halaman detail satu postingan (mendukung rich media, komentar, dan lightbox)."""
    payload = get_current_user_from_token()
    if not payload:
        return redirect(url_for('login'))

    current_username = payload.get('id')
    user_info = db.users.find_one({'username': current_username}, {'_id': False})
    if not user_info:
        return redirect(url_for('login'))

    if not post_id or not ObjectId.is_valid(post_id):
        return render_template('404.html', username=post_id, current_user=user_info), 404

    post_doc = db.posts.find_one({'_id': ObjectId(post_id)})
    if not post_doc or not _user_can_access_post(post_doc, current_username):
        return render_template('404.html', username=post_id, current_user=user_info), 404

    return render_template(
        'post_detail.html',
        user_info=user_info,
        current_user=user_info,
        post_id=post_id,
    )


@app.route('/api/posts/<post_id>/repost', methods=['POST'])
@app.route('/api/repost', methods=['POST'])
def repost_post(post_id=None):
    """
    Endpoint untuk melakukan repost postingan pengguna lain.
    Mencegah self-repost (400) dan duplicate repost (409).
    Mengirimkan notifikasi ke pembuat postingan asli.
    """
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    target_pid = _extract_post_id(post_id)
    if not target_pid:
        return jsonify({'result': 'fail', 'msg': 'Post ID is required'}), 400

    if not ObjectId.is_valid(target_pid):
        return jsonify({'result': 'fail', 'msg': 'Invalid post ID format'}), 400

    post_doc = db.posts.find_one({'_id': ObjectId(target_pid)})
    if not post_doc:
        return jsonify({'result': 'fail', 'msg': 'Post not found'}), 404

    # Jika target adalah repost, arahkan ke postingan asli
    actual_pid = target_pid
    actual_post = post_doc
    if post_doc.get('post_type') == 'repost' and post_doc.get('original_post_id'):
        actual_pid = post_doc.get('original_post_id')
        actual_post = db.posts.find_one({'_id': ObjectId(actual_pid)})
        if not actual_post:
            return jsonify({'result': 'fail', 'msg': 'Original post not found'}), 404

    # Cegah repost postingan milik sendiri atau dari komunitas privat non-anggota
    if not _user_can_access_post(actual_post, current_username):
        return jsonify({'result': 'fail', 'msg': 'Anda tidak dapat mengakses postingan komunitas privat ini.'}), 403
    if actual_post.get('username') == current_username:
        return jsonify({'result': 'fail', 'msg': 'Cannot repost your own post'}), 400

    # Cegah duplicate repost
    existing = db.posts.find_one({
        'username': current_username,
        'original_post_id': actual_pid,
        'post_type': 'repost'
    })
    if existing:
        return jsonify({
            'result': 'fail',
            'msg': 'Post already reposted',
            'is_reposted': True,
            'count_repost': count_reposts(actual_pid),
            'post_id': actual_pid
        }), 409

    user_info = db.users.find_one({'username': current_username}) or {}
    now = datetime.now(timezone.utc).isoformat()
    repost_doc = {
        'username': current_username,
        'profile_name': user_info.get('profile_name', current_username),
        'profile_pic_real': user_info.get('profile_pic_real', 'profile_pics/profile_placeholder.png'),
        'date': now,
        'post_type': 'repost',
        'original_post_id': actual_pid,
        'comment': ''
    }

    try:
        db.posts.insert_one(repost_doc)
    except DuplicateKeyError:
        return jsonify({
            'result': 'fail',
            'msg': 'Post already reposted',
            'is_reposted': True,
            'count_repost': count_reposts(actual_pid),
            'post_id': actual_pid
        }), 409

    # Kirim notifikasi ke pemilik postingan asli
    create_notification(
        recipient=actual_post.get('username'),
        actor=current_username,
        notif_type='repost',
        post_id=actual_pid
    )

    return jsonify({
        'result': 'success',
        'msg': 'Post reposted successfully',
        'is_reposted': True,
        'count_repost': count_reposts(actual_pid),
        'post_id': actual_pid
    }), 200


@app.route('/api/posts/<post_id>/unrepost', methods=['POST'])
@app.route('/api/unrepost', methods=['POST'])
def unrepost_post(post_id=None):
    """
    Endpoint untuk membatalkan repost suatu postingan.
    Bersifat idempotent dan aman jika repost tidak ditemukan.
    """
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    target_pid = _extract_post_id(post_id)
    if not target_pid:
        return jsonify({'result': 'fail', 'msg': 'Post ID is required'}), 400

    if not ObjectId.is_valid(target_pid):
        return jsonify({'result': 'fail', 'msg': 'Invalid post ID format'}), 400

    post_doc = db.posts.find_one({'_id': ObjectId(target_pid)})
    actual_pid = target_pid
    if post_doc and post_doc.get('post_type') == 'repost' and post_doc.get('original_post_id'):
        actual_pid = post_doc.get('original_post_id')

    # Hapus dokumen repost oleh pengguna ini
    db.posts.delete_many({
        'username': current_username,
        'original_post_id': actual_pid,
        'post_type': 'repost'
    })

    return jsonify({
        'result': 'success',
        'msg': 'Repost removed successfully',
        'is_reposted': False,
        'count_repost': count_reposts(actual_pid),
        'post_id': actual_pid
    }), 200


@app.route('/api/posts/<post_id>/quote', methods=['POST'])
@app.route('/api/quote', methods=['POST'])
def quote_post(post_id=None):
    """
    Endpoint untuk membuat quote post (membagikan postingan dengan komentar sendiri).
    Validasi batas panjang komentar (1-5000 karakter).
    Mengirimkan notifikasi quote ke pembuat postingan asli.
    """
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    target_pid = _extract_post_id(post_id)
    if not target_pid:
        return jsonify({'result': 'fail', 'msg': 'Post ID is required'}), 400

    if not ObjectId.is_valid(target_pid):
        return jsonify({'result': 'fail', 'msg': 'Invalid post ID format'}), 400

    post_doc = db.posts.find_one({'_id': ObjectId(target_pid)})
    if not post_doc:
        return jsonify({'result': 'fail', 'msg': 'Post not found'}), 404

    actual_pid = target_pid
    actual_post = post_doc
    if post_doc.get('post_type') == 'repost' and post_doc.get('original_post_id'):
        actual_pid = post_doc.get('original_post_id')
        actual_post = db.posts.find_one({'_id': ObjectId(actual_pid)})
        if not actual_post:
            return jsonify({'result': 'fail', 'msg': 'Original post not found'}), 404

    if not _user_can_access_post(actual_post, current_username):
        return jsonify({'result': 'fail', 'msg': 'Anda tidak dapat mengutip postingan dari komunitas privat.'}), 403

    # Ambil komentar kutipan dari JSON atau form-data
    content = None
    if request.is_json:
        content = (request.get_json(silent=True) or {}).get('comment')
    if not content:
        content = request.form.get('comment_give') or request.form.get('comment')

    if not content or not isinstance(content, str) or not content.strip():
        return jsonify({'result': 'fail', 'msg': 'Quote comment cannot be empty'}), 400

    content = content.strip()
    if len(content) > 5000:
        return jsonify({'result': 'fail', 'msg': 'Quote comment exceeds maximum length'}), 400

    user_info = db.users.find_one({'username': current_username}) or {}
    now = datetime.now(timezone.utc).isoformat()
    tags = extract_hashtags(content)
    quote_doc = {
        'username': current_username,
        'profile_name': user_info.get('profile_name', current_username),
        'profile_pic_real': user_info.get('profile_pic_real', 'profile_pics/profile_placeholder.png'),
        'comment': content,
        'date': now,
        'post_type': 'quote',
        'original_post_id': actual_pid,
        'hashtags': tags
    }

    res = db.posts.insert_one(quote_doc)
    new_quote_id = str(res.inserted_id)

    # Kirim notifikasi quote ke pembuat postingan asli
    create_notification(
        recipient=actual_post.get('username'),
        actor=current_username,
        notif_type='quote',
        post_id=actual_pid
    )

    return jsonify({
        'result': 'success',
        'msg': 'Quote post created successfully',
        'post_id': new_quote_id,
        'original_post_id': actual_pid,
        'comment': content
    }), 201  


def calculate_trending_score(post, now=None, period='all') -> float:
    """
    Menghitung skor trending sebuah postingan:
    - Bobot interaksi:
      Reaksi (heart, stars, thumbs) = 2.0 poin
      Komentar & balasan = 3.5 poin
      Repost / pembagian = 4.0 poin
      Bookmark = 2.5 poin
    - Faktor Recency / Time Decay:
      T = usia postingan dalam jam (minimum 0.1 jam)
      Gravity G: 'today'=1.2, 'week'=1.0, 'all'=0.7
      Score = (Total Engagement + 1.0) / (T + 2.0)^G
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # 1. Hitung total poin interaksi
    reactions = (
        (post.get('count_heart', 0) * 2.0)
        + (post.get('count_stars', 0) * 2.0)
        + (post.get('count_thumbs', 0) * 2.0)
    )
    comments = post.get('count_comments', 0) * 3.5
    reposts = post.get('count_repost', 0) * 4.0
    bookmarks = (1 if post.get('is_bookmarked') else 0) * 2.5

    total_engagement = reactions + comments + reposts + bookmarks

    # 2. Hitung usia postingan dalam jam
    post_date_str = post.get('date')
    hours_age = 0.5
    if post_date_str:
        try:
            if post_date_str.endswith('Z'):
                post_dt = datetime.fromisoformat(post_date_str.replace('Z', '+00:00'))
            else:
                post_dt = datetime.fromisoformat(post_date_str)
            if post_dt.tzinfo is None:
                post_dt = post_dt.replace(tzinfo=timezone.utc)
            delta = now - post_dt
            hours_age = max(0.1, delta.total_seconds() / 3600.0)
        except Exception:
            hours_age = 1.0

    # 3. Penalti gravitasi berdasarkan periode waktu
    gravity = 0.7
    if period == 'today':
        gravity = 1.2
    elif period == 'week':
        gravity = 1.0

    score = (total_engagement + 1.0) / ((hours_age + 2.0) ** gravity)
    return round(score, 4)


@app.route('/explore', methods=['GET'])
def explore_page():
    """Halaman beranda Explore & Trending."""
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return redirect(url_for('login'))

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
        user_info = db.users.find_one({'username': current_username})
        if not user_info:
            return redirect(url_for('login'))

        unread_count = get_unread_count(current_username)
        trending_tags = get_trending_hashtags(limit=5)
        suggested_users = get_suggested_users(current_username, limit=4)

        return render_template(
            'explore.html',
            user_info=user_info,
            unread_count=unread_count,
            trending_tags=trending_tags,
            suggested_users=suggested_users
        )
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return redirect(url_for('login'))


@app.route('/explore/tags/<tag>', methods=['GET'])
def topic_page(tag):
    """Halaman Topik / Hashtag spesifik."""
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return redirect(url_for('login'))

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
        user_info = db.users.find_one({'username': current_username})
        if not user_info:
            return redirect(url_for('login'))

        clean_tag = tag.strip().lower().lstrip('#')
        total_posts = db.posts.count_documents({'hashtags': clean_tag, 'post_type': {'$ne': 'repost'}})
        unread_count = get_unread_count(current_username)
        trending_tags = get_trending_hashtags(limit=5)

        return render_template(
            'topic.html',
            user_info=user_info,
            tag=clean_tag,
            display_tag=f"#{clean_tag}",
            total_posts=total_posts,
            unread_count=unread_count,
            trending_tags=trending_tags
        )
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return redirect(url_for('login'))


@app.route('/api/trending', methods=['GET'])
def get_trending_posts():
    """
    Endpoint untuk mengambil daftar postingan trending dengan dukungan filter periode:
    - today: 24 jam terakhir (gravity 1.2)
    - week: 7 hari terakhir (gravity 1.0)
    - all: seluruh waktu dengan time decay (gravity 0.7)
    """
    token_receive = request.cookies.get('mytoken')
    current_username = None
    if token_receive:
        try:
            payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
            current_username = payload.get('id')
        except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
            pass

    period = request.args.get('period', 'all').lower().strip()
    if period not in ['today', 'week', 'all']:
        period = 'all'

    limit_raw = request.args.get('limit', '20').strip()
    try:
        limit = max(1, min(int(limit_raw), 50))
    except ValueError:
        limit = 20

    now = datetime.now(timezone.utc)
    match_query = {'post_type': {'$ne': 'repost'}, 'community_visibility': {'$ne': 'private'}}
    if current_username:
        blocked_or_muted = get_all_blocked_relation_usernames(current_username) | set(get_muted_usernames(current_username))
        if blocked_or_muted:
            match_query['username'] = {'$nin': list(blocked_or_muted)}

    if period == 'today':
        cutoff = (now - timedelta(days=1)).isoformat()
        match_query['date'] = {'$gte': cutoff}
    elif period == 'week':
        cutoff = (now - timedelta(days=7)).isoformat()
        match_query['date'] = {'$gte': cutoff}

    candidate_posts = list(db.posts.find(match_query).sort([('date', -1)]).limit(100))
    if not candidate_posts:
        return jsonify({
            'result': 'success',
            'posts': [],
            'period': period,
            'total': 0
        }), 200

    enriched = enrich_posts_metadata(candidate_posts, current_username)
    for p in enriched:
        p['trending_score'] = calculate_trending_score(p, now=now, period=period)

    # Urutkan berdasarkan skor trending tertinggi
    enriched.sort(key=lambda p: (p['trending_score'], p.get('date', '')), reverse=True)
    trending_posts = enriched[:limit]

    return jsonify({
        'result': 'success',
        'posts': trending_posts,
        'period': period,
        'total': len(trending_posts)
    }), 200


@app.route('/api/trending/hashtags', methods=['GET'])
def get_trending_hashtags_api():
    """Mengembalikan daftar hashtag yang sedang trending beserta jumlah postingannya."""
    period = request.args.get('period', 'all').lower().strip()
    limit_raw = request.args.get('limit', '10').strip()
    try:
        limit = max(1, min(int(limit_raw), 30))
    except ValueError:
        limit = 10

    hashtags = get_trending_hashtags(limit=limit, period=period)
    return jsonify({
        'result': 'success',
        'hashtags': hashtags,
        'period': period,
        'total': len(hashtags)
    }), 200


@app.route('/api/discover/people', methods=['GET'])
def get_discover_people_api():
    """
    Mengembalikan daftar rekomendasi pengguna (suggested users) untuk current_username.
    Memeriksa otentikasi JWT.
    """
    token_receive = request.cookies.get('mytoken')
    if not token_receive:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
        current_username = payload.get('id')
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return jsonify({'result': 'fail', 'msg': 'Session expired or invalid'}), 401

    limit_raw = request.args.get('limit', '5').strip()
    try:
        limit = max(1, min(int(limit_raw), 20))
    except ValueError:
        limit = 5

    people = get_suggested_users(current_username=current_username, limit=limit)
    return jsonify({
        'result': 'success',
        'users': people,
        'total': len(people)
    }), 200


@app.route('/api/tags/<tag>/posts', methods=['GET'])
def get_tag_posts_api(tag):
    """
    Mengambil postingan yang memiliki hashtag tertentu.
    Mendukung pengurutan 'trending' atau 'latest', serta pagination cursor.
    """
    token_receive = request.cookies.get('mytoken')
    current_username = None
    if token_receive:
        try:
            payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
            current_username = payload.get('id')
        except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
            pass

    if not tag or not isinstance(tag, str) or not tag.strip():
        return jsonify({'result': 'fail', 'msg': 'Invalid hashtag'}), 400

    clean_tag = tag.strip().lower().lstrip('#')
    sort_mode = request.args.get('sort', 'trending').lower().strip()
    limit_raw = request.args.get('limit', '20').strip()
    cursor = request.args.get('cursor', '').strip()

    try:
        limit = max(1, min(int(limit_raw), 50))
    except ValueError:
        limit = 20

    query = {'hashtags': clean_tag, 'post_type': {'$ne': 'repost'}, 'community_visibility': {'$ne': 'private'}}
    if current_username:
        blocked_or_muted = get_all_blocked_relation_usernames(current_username) | set(get_muted_usernames(current_username))
        if blocked_or_muted:
            query['username'] = {'$nin': list(blocked_or_muted)}
    total_count = db.posts.count_documents(query)

    if sort_mode == 'latest':
        if cursor and ObjectId.is_valid(cursor):
            anchor = db.posts.find_one({'_id': ObjectId(cursor)})
            if anchor:
                query = {
                    '$and': [
                        query,
                        {'$or': [
                            {'date': {'$lt': anchor.get('date', '')}},
                            {'date': anchor.get('date', ''), '_id': {'$lt': anchor['_id']}}
                        ]}
                    ]
                }
        posts = list(db.posts.find(query).sort([('date', -1), ('_id', -1)]).limit(limit))
        enriched = enrich_posts_metadata(posts, current_username)
        has_more = (len(posts) == limit)
        next_cursor = str(posts[-1]['_id']) if has_more else None
        return jsonify({
            'result': 'success',
            'tag': clean_tag,
            'posts': enriched,
            'total_count': total_count,
            'has_more': has_more,
            'next_cursor': next_cursor,
            'sort': 'latest'
        }), 200
    else:
        # Trending sort
        posts = list(db.posts.find(query).sort([('date', -1)]).limit(100))
        enriched = enrich_posts_metadata(posts, current_username)
        now = datetime.now(timezone.utc)
        for p in enriched:
            p['trending_score'] = calculate_trending_score(p, now=now, period='all')
        enriched.sort(key=lambda p: (p['trending_score'], p.get('date', '')), reverse=True)
        results = enriched[:limit]
        return jsonify({
            'result': 'success',
            'tag': clean_tag,
            'posts': results,
            'total_count': total_count,
            'has_more': False,
            'next_cursor': None,
            'sort': 'trending'
        }), 200


@app.route('/api/search', methods=['GET'])
def search_api():
    """
    Pencarian menyeluruh di Sweeter:
    - User (berdasarkan username dan profile_name)
    - Postingan (berdasarkan teks comment)
    - Hashtag (berdasarkan nama tag)
    Dilengkapi pengamanan regex escaping untuk mencegah ReDoS & injection.
    """
    token_receive = request.cookies.get('mytoken')
    current_username = None
    if token_receive:
        try:
            payload = jwt.decode(token_receive, SECRET_KEY, algorithms=['HS256'])
            current_username = payload.get('id')
        except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
            pass

    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'result': 'fail', 'msg': 'Search query cannot be empty'}), 400

    if len(q) > 100:
        return jsonify({'result': 'fail', 'msg': 'Search query exceeds maximum length of 100 characters'}), 400

    search_type = request.args.get('type', 'all').lower().strip()
    limit_raw = request.args.get('limit', '20').strip()
    try:
        limit = max(1, min(int(limit_raw), 50))
    except ValueError:
        limit = 20

    safe_q = re.escape(q)
    results = {'result': 'success', 'query': q, 'type': search_type, 'users': [], 'posts': [], 'hashtags': []}
    blocked_or_muted = get_all_blocked_relation_usernames(current_username) | set(get_muted_usernames(current_username)) if current_username else set()

    # 1. Search Users
    if search_type in ['all', 'users']:
        clean_user_q = re.escape(q.lstrip('@').strip())
        user_matches = list(db.users.find({
            '$or': [
                {'username': {'$regex': clean_user_q, '$options': 'i'}},
                {'profile_name': {'$regex': clean_user_q, '$options': 'i'}}
            ]
        }, {'password': 0}).limit(limit * 2))

        for u in user_matches:
            uname = u['username']
            if uname in blocked_or_muted:
                continue
            results['users'].append({
                'username': uname,
                'profile_name': u.get('profile_name', uname),
                'profile_pic_real': u.get('profile_pic_real', 'profile_pics/profile_placeholder.png'),
                'profile_info': u.get('profile_info', ''),
                'follower_count': count_followers(uname),
                'is_following': is_following(current_username, uname) if current_username else False,
                'is_mine': (uname == current_username)
            })
            if len(results['users']) >= limit:
                break

    # 2. Search Hashtags (tanpa membocorkan postingan komunitas private)
    if search_type in ['all', 'tags'] or q.startswith('#'):
        clean_tag_q = q.lstrip('#').lower().strip()
        if clean_tag_q:
            safe_tag_q = re.escape(clean_tag_q)
            pipeline = [
                {'$match': {'hashtags': {'$regex': safe_tag_q, '$options': 'i'}, 'post_type': {'$ne': 'repost'}, 'community_visibility': {'$ne': 'private'}}},
                {'$unwind': '$hashtags'},
                {'$match': {'hashtags': {'$regex': safe_tag_q, '$options': 'i'}}},
                {'$group': {'_id': '$hashtags', 'post_count': {'$sum': 1}}},
                {'$sort': {'post_count': -1}},
                {'$limit': limit}
            ]
            tag_matches = list(db.posts.aggregate(pipeline))
            for t in tag_matches:
                t_name = t['_id']
                results['hashtags'].append({
                    'tag': t_name,
                    'display_tag': f"#{t_name}",
                    'post_count': t['post_count'],
                    'url': f"/explore/tags/{t_name}"
                })

    # 3. Search Posts
    if search_type in ['all', 'posts']:
        post_query = {'comment': {'$regex': safe_q, '$options': 'i'}, 'post_type': {'$ne': 'repost'}, 'community_visibility': {'$ne': 'private'}}
        if blocked_or_muted:
            post_query['username'] = {'$nin': list(blocked_or_muted)}
        post_matches = list(db.posts.find(post_query).sort([('date', -1)]).limit(limit))
        results['posts'] = enrich_posts_metadata(post_matches, current_username)

    return jsonify(results), 200  


# ==========================================
# DRAFT POSTS & AUTOSAVE SUBSYSTEM
# ==========================================

DRAFT_RATE_LIMIT_MAX = int(os.getenv("DRAFT_RATE_LIMIT_MAX", "60"))
DRAFT_RATE_LIMIT_WINDOW = int(os.getenv("DRAFT_RATE_LIMIT_WINDOW", "60"))
_draft_rate_buckets = defaultdict(deque)
_draft_rate_lock = threading.Lock()


def _check_draft_rate_limit(username: str) -> bool:
    """Sliding-window per-user rate limiter untuk melindungi endpoint draft/autosave dari penyalahgunaan."""
    if app.config.get("DISABLE_DRAFT_RATE_LIMIT"):
        return True
    now_ts = time.monotonic()
    cutoff = now_ts - DRAFT_RATE_LIMIT_WINDOW
    with _draft_rate_lock:
        bucket = _draft_rate_buckets[username]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= DRAFT_RATE_LIMIT_MAX:
            return False
        bucket.append(now_ts)
    return True


def _serialize_draft(doc: dict) -> dict:
    """Mengubah dokumen MongoDB draft menjadi dictionary siap-JSON yang aman."""
    if not doc:
        return None
    return {
        '_id': str(doc['_id']),
        'user_id': doc.get('user_id', ''),
        'content': doc.get('content', ''),
        'images': doc.get('images') if isinstance(doc.get('images'), list) else [],
        'version': int(doc.get('version', 1)),
        'client_draft_key': doc.get('client_draft_key'),
        'created_at': doc.get('created_at', ''),
        'updated_at': doc.get('updated_at', ''),
        'last_saved_at': doc.get('last_saved_at', doc.get('updated_at', '')),
    }


def _parse_draft_request_payload(existing_images=None):
    """
    Mengekstrak dan memvalidasi payload draft dari JSON atau multipart/form-data.
    Mengembalikan dict berisi: content, kept_images, removed_filenames, new_files,
    version, client_seq, client_draft_key, atau melempar ValueError.
    """
    json_body = request.get_json(silent=True) if request.is_json else None

    if json_body is not None:
        raw_content = json_body.get('content')
        if raw_content is None:
            raw_content = json_body.get('comment')
        raw_version = json_body.get('version')
        raw_seq = json_body.get('client_seq')
        raw_key = json_body.get('client_draft_key')
        has_keep_param = ('keep_images' in json_body)
        raw_keep = json_body.get('keep_images')
    else:
        raw_content = request.form.get('content')
        if raw_content is None:
            raw_content = request.form.get('comment_give') or request.form.get('comment')
        raw_version = request.form.get('version')
        raw_seq = request.form.get('client_seq')
        raw_key = request.form.get('client_draft_key')
        has_keep_param = ('keep_images' in request.form)
        raw_keep = request.form.getlist('keep_images') if has_keep_param else None

    if raw_content is None:
        content = ''
    elif isinstance(raw_content, str):
        content = raw_content.strip()
    else:
        raise ValueError('Format konten draft tidak valid.')

    if len(content) > 5000:
        raise ValueError('Panjang konten draft melebihi batas maksimal 5000 karakter.')

    # Parse version & client_seq opsional untuk kontrol konkurensi
    version = None
    if raw_version not in (None, ''):
        try:
            version = int(raw_version)
            if version < 0:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError('Parameter version tidak valid.')

    client_seq = None
    if raw_seq not in (None, ''):
        try:
            client_seq = int(raw_seq)
            if client_seq < 0:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError('Parameter client_seq tidak valid.')

    client_draft_key = None
    if isinstance(raw_key, str) and raw_key.strip():
        clean_key = raw_key.strip()
        if len(clean_key) > 100 or not re.match(r'^[a-zA-Z0-9_-]+$', clean_key):
            raise ValueError('Parameter client_draft_key tidak valid.')
        client_draft_key = clean_key

    # Kelola gambar yang dipertahankan vs gambar baru
    base_images = list(existing_images) if isinstance(existing_images, list) else []
    new_files = []
    for key in ('images', 'images[]', 'file_give'):
        new_files.extend(request.files.getlist(key))

    if has_keep_param:
        if isinstance(raw_keep, list):
            if len(raw_keep) == 1 and isinstance(raw_keep[0], str) and raw_keep[0].strip().startswith('['):
                try:
                    parsed = json.loads(raw_keep[0])
                    keep_filenames = {str(x).strip() for x in (parsed if isinstance(parsed, list) else []) if str(x).strip()}
                except Exception:
                    raise ValueError('Format keep_images tidak valid.')
            else:
                keep_filenames = {str(x).strip() for x in raw_keep if str(x).strip()}
        elif isinstance(raw_keep, str) and raw_keep.strip().startswith('['):
            try:
                parsed = json.loads(raw_keep)
                keep_filenames = {str(x).strip() for x in (parsed if isinstance(parsed, list) else []) if str(x).strip()}
            except Exception:
                raise ValueError('Format keep_images tidak valid.')
        else:
            keep_filenames = set()

        kept_images = [img for img in base_images if img.get('filename') in keep_filenames]
        removed_filenames = [
            img.get('filename') for img in base_images
            if img.get('filename') and img.get('filename') not in keep_filenames
        ]
    else:
        kept_images = list(base_images)
        removed_filenames = []

    if len(kept_images) + len(new_files) > MAX_POST_IMAGES_COUNT:
        raise ValueError(f'Maksimal {MAX_POST_IMAGES_COUNT} gambar dalam satu draft.')

    return {
        'content': content,
        'kept_images': kept_images,
        'removed_filenames': removed_filenames,
        'new_files': new_files,
        'version': version,
        'client_seq': client_seq,
        'client_draft_key': client_draft_key,
    }


def _get_owned_draft_or_error(draft_id: str, current_username: str):
    """Memvalidasi format draft_id, keberadaan dokumen, dan kepemilikan oleh current_username."""
    if not draft_id or not ObjectId.is_valid(draft_id):
        return None, (jsonify({'result': 'fail', 'msg': 'Invalid draft ID format'}), 400)

    draft_doc = db.drafts.find_one({'_id': ObjectId(draft_id)})
    if not draft_doc:
        return None, (jsonify({'result': 'fail', 'msg': 'Draft not found'}), 404)

    if draft_doc.get('user_id') != current_username:
        return None, (jsonify({'result': 'fail', 'msg': 'You do not have permission to access this draft'}), 403)

    return draft_doc, None


@app.route('/drafts', methods=['GET'])
def drafts_page():
    """Halaman khusus manajemen draft postingan milik pengguna yang terautentikasi."""
    payload = get_current_user_from_token()
    if not payload:
        return redirect(url_for('login'))

    current_username = payload.get('id')
    user_info = db.users.find_one({'username': current_username}, {'_id': False})
    if not user_info:
        return redirect(url_for('login'))

    draft_count = db.drafts.count_documents({'user_id': current_username})
    return render_template(
        'drafts.html',
        user_info=user_info,
        current_user=user_info,
        draft_count=draft_count,
    )


@app.route('/api/drafts/count', methods=['GET'])
def get_drafts_count_api():
    """Mengembalikan jumlah draft tersimpan milik pengguna aktif."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    count = db.drafts.count_documents({'user_id': payload.get('id')})
    return jsonify({'result': 'success', 'draft_count': count}), 200


@app.route('/api/drafts', methods=['GET'])
def list_drafts_api():
    """Mengambil daftar draft milik pengguna aktif, diurutkan dari pembaruan terbaru, dengan cursor pagination."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    cursor = request.args.get('cursor', '').strip()
    limit_raw = request.args.get('limit', '20').strip()
    try:
        limit = max(1, min(int(limit_raw), 50))
    except ValueError:
        return jsonify({'result': 'fail', 'msg': 'Invalid limit parameter'}), 400

    query = {'user_id': current_username}
    total_count = db.drafts.count_documents({'user_id': current_username})

    if cursor:
        if not ObjectId.is_valid(cursor):
            return jsonify({'result': 'fail', 'msg': 'Invalid cursor parameter'}), 400
        anchor = db.drafts.find_one({'_id': ObjectId(cursor), 'user_id': current_username})
        if not anchor:
            return jsonify({'result': 'fail', 'msg': 'Cursor not found'}), 404
        anchor_ts = anchor.get('updated_at', '')
        query = {
            '$and': [
                {'user_id': current_username},
                {'$or': [
                    {'updated_at': {'$lt': anchor_ts}},
                    {'updated_at': anchor_ts, '_id': {'$lt': anchor['_id']}}
                ]}
            ]
        }

    docs = list(db.drafts.find(query).sort([('updated_at', -1), ('_id', -1)]).limit(limit))
    drafts = [_serialize_draft(d) for d in docs]
    has_more = (len(docs) == limit)
    next_cursor = str(docs[-1]['_id']) if has_more else None

    return jsonify({
        'result': 'success',
        'drafts': drafts,
        'total_count': total_count,
        'has_more': has_more,
        'next_cursor': next_cursor,
    }), 200


@app.route('/api/drafts', methods=['POST'])
def create_draft_api():
    """
    Membuat draft baru (manual save maupun autosave awal).
    Menggunakan client_draft_key opsional untuk mencegah duplikasi dari request bersamaan/klik ganda.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    if not _check_draft_rate_limit(current_username):
        return jsonify({'result': 'fail', 'msg': 'Terlalu banyak permintaan penyimpanan draft. Coba sesaat lagi.'}), 429

    try:
        parsed = _parse_draft_request_payload(existing_images=None)
    except ValueError as err:
        return jsonify({'result': 'fail', 'msg': str(err)}), 400

    content = parsed['content']
    new_files = parsed['new_files']
    client_key = parsed['client_draft_key']

    if not content and len(new_files) == 0:
        return jsonify({'result': 'fail', 'msg': 'Konten draft atau minimal satu gambar wajib diisi.'}), 400

    # Validasi seluruh gambar sebelum menulis ke disk
    validated_new = []
    for f in new_files:
        try:
            validated_new.append(validate_post_image_file(f))
        except ValueError as err:
            return jsonify({'result': 'fail', 'msg': str(err)}), 400

    # Idempotency check jika client_draft_key sudah ada untuk user ini
    if client_key:
        existing_by_key = db.drafts.find_one({'user_id': current_username, 'client_draft_key': client_key})
        if existing_by_key:
            return _perform_draft_update(existing_by_key, parsed, validated_new, status_code=200)

    saved_images = []
    try:
        for data_bytes, ext, mime_type, _size in validated_new:
            saved_images.append(save_post_image_file(data_bytes, ext, mime_type))

        now_iso = datetime.now(timezone.utc).isoformat()
        doc = {
            'user_id': current_username,
            'content': content,
            'images': saved_images,
            'version': 1,
            'last_client_seq': parsed['client_seq'] or 0,
            'created_at': now_iso,
            'updated_at': now_iso,
            'last_saved_at': now_iso,
        }
        if client_key:
            doc['client_draft_key'] = client_key

        try:
            res = db.drafts.insert_one(doc)
        except DuplicateKeyError:
            # Jika race condition menyisipkan key yang sama bersamaan, rollback file baru dan update dokumen yang ada
            for img in saved_images:
                safe_delete_post_image(img.get('filename'), check_db_refs=False)
            saved_images = []
            existing_by_key = db.drafts.find_one({'user_id': current_username, 'client_draft_key': client_key})
            if existing_by_key:
                return _perform_draft_update(existing_by_key, parsed, validated_new, status_code=200)
            raise

        doc['_id'] = res.inserted_id
        return jsonify({
            'result': 'success',
            'msg': 'Draft berhasil disimpan.',
            'draft': _serialize_draft(doc),
        }), 201
    except Exception as exc:
        for img in saved_images:
            safe_delete_post_image(img.get('filename'), check_db_refs=False)
        return jsonify({'result': 'fail', 'msg': f'Gagal menyimpan draft: {exc}'}), 500


def _perform_draft_update(draft_doc: dict, parsed: dict, validated_new: list, status_code: int = 200):
    """Helper internal pembaruan draft dengan proteksi version/sequence dan rollback aman."""
    draft_id = str(draft_doc['_id'])
    current_version = int(draft_doc.get('version', 1))
    last_seq = int(draft_doc.get('last_client_seq', 0))

    req_version = parsed.get('version')
    if req_version is not None and req_version < current_version:
        return jsonify({
            'result': 'fail',
            'code': 'STALE_VERSION',
            'msg': 'Versi draft lebih lama diabaikan agar tidak menimpa perubahan terbaru.',
            'current_version': current_version,
        }), 409

    req_seq = parsed.get('client_seq')
    if req_seq is not None and req_seq < last_seq:
        return jsonify({
            'result': 'fail',
            'code': 'STALE_SEQUENCE',
            'msg': 'Request autosave kadaluarsa diabaikan.',
            'current_version': current_version,
        }), 409

    content = parsed['content']
    kept_images = parsed['kept_images']
    removed_filenames = parsed['removed_filenames']

    if not content and (len(kept_images) + len(validated_new)) == 0:
        return jsonify({'result': 'fail', 'msg': 'Konten draft atau minimal satu gambar wajib diisi.'}), 400

    saved_new_images = []
    try:
        for data_bytes, ext, mime_type, _size in validated_new:
            saved_new_images.append(save_post_image_file(data_bytes, ext, mime_type))

        final_images = kept_images + saved_new_images
        now_iso = datetime.now(timezone.utc).isoformat()
        new_version = current_version + 1
        new_seq = req_seq if req_seq is not None else last_seq

        db.drafts.update_one(
            {'_id': draft_doc['_id']},
            {'$set': {
                'content': content,
                'images': final_images,
                'version': new_version,
                'last_client_seq': new_seq,
                'updated_at': now_iso,
                'last_saved_at': now_iso,
            }}
        )
    except Exception as exc:
        for img in saved_new_images:
            safe_delete_post_image(img.get('filename'), check_db_refs=False)
        return jsonify({'result': 'fail', 'msg': f'Gagal memperbarui draft: {exc}'}), 500

    for old_fname in removed_filenames:
        safe_delete_post_image(old_fname, check_db_refs=True)

    updated_doc = db.drafts.find_one({'_id': draft_doc['_id']}) or {
        **draft_doc,
        'content': content,
        'images': final_images,
        'version': new_version,
        'updated_at': now_iso,
        'last_saved_at': now_iso,
    }
    return jsonify({
        'result': 'success',
        'msg': 'Draft diperbarui.',
        'draft': _serialize_draft(updated_doc),
    }), status_code


@app.route('/api/drafts/<draft_id>', methods=['GET'])
def get_single_draft_api(draft_id):
    """Mengambil satu dokumen draft milik pengguna aktif."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    draft_doc, err_resp = _get_owned_draft_or_error(draft_id, payload.get('id'))
    if err_resp:
        return err_resp

    return jsonify({
        'result': 'success',
        'draft': _serialize_draft(draft_doc),
    }), 200


@app.route('/api/drafts/<draft_id>', methods=['PUT', 'POST'])
def update_draft_api(draft_id):
    """Memperbarui draft yang sudah ada (digunakan oleh autosave dan manual save)."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    if not _check_draft_rate_limit(current_username):
        return jsonify({'result': 'fail', 'msg': 'Terlalu banyak permintaan penyimpanan draft. Coba sesaat lagi.'}), 429

    draft_doc, err_resp = _get_owned_draft_or_error(draft_id, current_username)
    if err_resp:
        return err_resp

    existing_images = draft_doc.get('images') if isinstance(draft_doc.get('images'), list) else []
    try:
        parsed = _parse_draft_request_payload(existing_images=existing_images)
    except ValueError as err:
        return jsonify({'result': 'fail', 'msg': str(err)}), 400

    validated_new = []
    for f in parsed['new_files']:
        try:
            validated_new.append(validate_post_image_file(f))
        except ValueError as err:
            return jsonify({'result': 'fail', 'msg': str(err)}), 400

    return _perform_draft_update(draft_doc, parsed, validated_new, status_code=200)


@app.route('/api/drafts/<draft_id>', methods=['DELETE'])
@app.route('/api/drafts/<draft_id>/delete', methods=['POST'])
def delete_draft_api(draft_id):
    """Menghapus draft milik pengguna aktif dan membersihkan file gambar yang tidak lagi direferensikan."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    draft_doc, err_resp = _get_owned_draft_or_error(draft_id, current_username)
    if err_resp:
        return err_resp

    db.drafts.delete_one({'_id': ObjectId(draft_id)})

    for img in (draft_doc.get('images') or []):
        if isinstance(img, dict) and img.get('filename'):
            safe_delete_post_image(img['filename'], check_db_refs=True)

    return jsonify({
        'result': 'success',
        'msg': 'Draft berhasil dihapus.',
        'draft_id': draft_id,
    }), 200


@app.route('/api/drafts/<draft_id>/publish', methods=['POST'])
def publish_draft_api(draft_id):
    """
    Memublikasikan draft menjadi postingan publik:
    1. Memvalidasi kepemilikan draft & mengunci secara atomik (is_publishing) agar klik ganda tidak membuat duplikat.
    2. Menggunakan kembali _create_published_post() tanpa menduplikasi logika pembuatan postingan.
    3. Menghapus draft HANYA setelah postingan publik berhasil disimpan; gagal -> draft tetap utuh.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    draft_doc, err_resp = _get_owned_draft_or_error(draft_id, current_username)
    if err_resp:
        return err_resp

    user_info = db.users.find_one({'username': current_username})
    if not user_info:
        return jsonify({'result': 'fail', 'msg': 'User profile not found'}), 404

    # Kunci draft secara atomik untuk mencegah duplikasi publikasi dari request paralel/berulang
    lock_res = db.drafts.update_one(
        {'_id': ObjectId(draft_id), 'user_id': current_username, 'is_publishing': {'$ne': True}},
        {'$set': {'is_publishing': True}}
    )
    if getattr(lock_res, 'modified_count', 0) == 0:
        return jsonify({
            'result': 'fail',
            'msg': 'Draft sedang dipublikasikan atau sudah dipublikasikan.'
        }), 409

    saved_new_images = []
    try:
        # Jika request publish membawa pembaruan terakhir dari editor (JSON/multipart), gunakan; jika kosong pakai draft tersimpan
        has_inline_payload = bool(
            request.is_json
            or request.form
            or request.files
        )
        existing_images = draft_doc.get('images') if isinstance(draft_doc.get('images'), list) else []

        if has_inline_payload:
            parsed = _parse_draft_request_payload(existing_images=existing_images)
            final_content = parsed['content']
            kept_images = parsed['kept_images']
            removed_filenames = parsed['removed_filenames']

            validated_new = []
            for f in parsed['new_files']:
                validated_new.append(validate_post_image_file(f))

            for data_bytes, ext, mime_type, _size in validated_new:
                saved_new_images.append(save_post_image_file(data_bytes, ext, mime_type))

            final_images = kept_images + saved_new_images
        else:
            final_content = (draft_doc.get('content') or '').strip()
            final_images = list(existing_images)
            removed_filenames = []

        date_str = request.form.get('date_give') if request.form else None
        if not date_str and request.is_json:
            date_str = (request.get_json(silent=True) or {}).get('date_give')

        created_post = _create_published_post(
            user_info=user_info,
            comment_text=final_content,
            date_str=date_str,
            images_list=final_images,
        )
    except ValueError as err:
        # Validasi gagal -> batalkan file baru, buka kunci draft agar tetap utuh
        for img in saved_new_images:
            safe_delete_post_image(img.get('filename'), check_db_refs=False)
        db.drafts.update_one({'_id': ObjectId(draft_id)}, {'$set': {'is_publishing': False}})
        return jsonify({'result': 'fail', 'msg': str(err)}), 400
    except Exception as exc:
        for img in saved_new_images:
            safe_delete_post_image(img.get('filename'), check_db_refs=False)
        db.drafts.update_one({'_id': ObjectId(draft_id)}, {'$set': {'is_publishing': False}})
        return jsonify({'result': 'fail', 'msg': f'Gagal memublikasikan draft: {exc}'}), 500

    # Hanya hapus draft setelah postingan publik berhasil tersimpan
    db.drafts.delete_one({'_id': ObjectId(draft_id)})

    # Bersihkan file draft yang dibuang saat publish (jika ada)
    for old_fname in removed_filenames:
        safe_delete_post_image(old_fname, check_db_refs=True)

    return jsonify({
        'result': 'success',
        'msg': 'Draft berhasil dipublikasikan.',
        'post_id': created_post['_id'],
        'post': created_post,
        'draft_id': draft_id,
    }), 201


# ==========================================
# COMMUNITY / CIRCLES SUBSYSTEM
# ==========================================

COMMUNITY_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
RESERVED_COMMUNITY_SLUGS = {
    "all", "discover", "joined", "created", "invited", "new", "create",
    "edit", "delete", "api", "admin", "settings", "members", "requests",
}


def _normalize_community_slug(raw_val: str) -> str:
    """Menormalisasi string menjadi candidate slug huruf kecil dengan tanda hubung."""
    if not raw_val or not isinstance(raw_val, str):
        return ""
    s = raw_val.strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


def _sync_community_member_count(community_id: str) -> int:
    """Menghitung ulang dan menyinkronkan member_count di db.communities secara akurat."""
    cid = str(community_id)
    cnt = db.community_members.count_documents({"community_id": cid})
    db.communities.update_one({"_id": ObjectId(cid)}, {"$set": {"member_count": cnt}})
    return cnt


def _resolve_community(identifier: str):
    """Mencari komunitas berdasarkan slug (prioritas) atau ObjectId."""
    if not identifier or not isinstance(identifier, str):
        return None
    clean = identifier.strip()
    if not clean:
        return None
    doc = db.communities.find_one({"slug": clean.lower()})
    if doc:
        return doc
    if ObjectId.is_valid(clean):
        return db.communities.find_one({"_id": ObjectId(clean)})
    return None


def _serialize_community(doc: dict, current_username: str = None) -> dict:
    """Menserialisasi dokumen komunitas lengkap dengan status keanggotaan pengguna aktif."""
    cid = str(doc["_id"])
    membership = _get_community_membership(cid, current_username) if current_username else None
    is_member = bool(membership)
    my_role = membership.get("role") if membership else None

    my_request_status = "none"
    if current_username and not is_member:
        pending_inv = db.community_requests.find_one({
            "community_id": cid,
            "user_id": current_username,
            "request_type": "invite",
            "status": "pending",
        })
        if pending_inv:
            my_request_status = "pending_invite"
        else:
            pending_req = db.community_requests.find_one({
                "community_id": cid,
                "user_id": current_username,
                "request_type": "request",
                "status": "pending",
            })
            if pending_req:
                my_request_status = "pending_request"

    return {
        "_id": cid,
        "name": doc.get("name", ""),
        "slug": doc.get("slug", ""),
        "description": doc.get("description", ""),
        "visibility": doc.get("visibility", "public"),
        "profile_image": doc.get("profile_image"),
        "cover_image": doc.get("cover_image"),
        "creator_id": doc.get("creator_id", ""),
        "member_count": int(doc.get("member_count", 0)),
        "created_at": doc.get("created_at", ""),
        "updated_at": doc.get("updated_at", ""),
        "is_member": is_member,
        "my_role": my_role,
        "can_manage": my_role in ("creator", "moderator"),
        "my_request_status": my_request_status,
    }


def _recalc_or_get_community_response(doc: dict, current_username: str = None) -> dict:
    """Helper ringan mengambil ulang doc komunitas terbaru dan menserialisasinya."""
    fresh = db.communities.find_one({"_id": doc["_id"]}) or doc
    return _serialize_community(fresh, current_username)


@app.route('/communities', methods=['GET'])
def communities_page():
    """Halaman direktori & penemuan Community / Circles."""
    payload = get_current_user_from_token()
    if not payload:
        return redirect(url_for('login'))

    current_username = payload.get('id')
    user_info = db.users.find_one({'username': current_username}, {'_id': False})
    if not user_info:
        return redirect(url_for('login'))

    return render_template('communities.html', user_info=user_info, current_user=user_info)


@app.route('/communities/<slug>', methods=['GET'])
@app.route('/c/<slug>', methods=['GET'])
def community_detail_page(slug):
    """Halaman detail sebuah Community / Circle beserta feed, anggota, dan manajemen."""
    payload = get_current_user_from_token()
    if not payload:
        return redirect(url_for('login'))

    current_username = payload.get('id')
    user_info = db.users.find_one({'username': current_username}, {'_id': False})
    if not user_info:
        return redirect(url_for('login'))

    comm_doc = _resolve_community(slug)
    if not comm_doc:
        return render_template('404.html', username=slug, current_user=user_info), 404

    return render_template(
        'community_detail.html',
        user_info=user_info,
        current_user=user_info,
        community=_serialize_community(comm_doc, current_username),
    )


@app.route('/api/communities', methods=['GET'])
def list_communities_api():
    """
    Mengambil daftar komunitas dengan filter tab ('all', 'discover', 'joined', 'created', 'invited'),
    pencarian query 'q', serta pagination cursor & limit.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    tab = request.args.get('tab', 'all').lower().strip()
    q = request.args.get('q', '').strip()
    cursor = request.args.get('cursor', '').strip()
    limit_raw = request.args.get('limit', '20').strip()

    try:
        limit = max(1, min(int(limit_raw), 50))
    except ValueError:
        return jsonify({'result': 'fail', 'msg': 'Invalid limit parameter'}), 400

    joined_ids = _get_user_joined_community_ids(current_username)
    joined_oids = [ObjectId(cid) for cid in joined_ids if ObjectId.is_valid(cid)]

    if tab == 'joined':
        if not joined_oids:
            return jsonify({'result': 'success', 'communities': [], 'has_more': False, 'next_cursor': None}), 200
        base_filter = {'_id': {'$in': joined_oids}}
    elif tab == 'created':
        base_filter = {'creator_id': current_username}
    elif tab == 'invited':
        req_docs = list(db.community_requests.find({
            'user_id': current_username,
            'status': 'pending',
        }))
        req_oids = [ObjectId(r['community_id']) for r in req_docs if ObjectId.is_valid(r.get('community_id', ''))]
        if not req_oids:
            return jsonify({'result': 'success', 'communities': [], 'has_more': False, 'next_cursor': None}), 200
        base_filter = {'_id': {'$in': req_oids}}
    elif tab == 'discover':
        base_filter = {'visibility': 'public'}
        if joined_oids:
            base_filter = {'visibility': 'public', '_id': {'$nin': joined_oids}}
    else:
        # 'all': jika user mengetik pencarian, tampilkan public maupun private metadata agar dapat mengajukan permintaan bergabung;
        # tanpa pencarian, tampilkan komunitas public + komunitas privat yang diikuti user
        if q:
            base_filter = {}
        elif joined_oids:
            base_filter = {'$or': [{'visibility': 'public'}, {'_id': {'$in': joined_oids}}]}
        else:
            base_filter = {'visibility': 'public'}

    query_parts = [base_filter] if base_filter else []
    if q:
        safe_q = re.escape(q[:60])
        query_parts.append({
            '$or': [
                {'name': {'$regex': safe_q, '$options': 'i'}},
                {'slug': {'$regex': safe_q, '$options': 'i'}},
                {'description': {'$regex': safe_q, '$options': 'i'}},
            ]
        })

    if cursor:
        if not ObjectId.is_valid(cursor):
            return jsonify({'result': 'fail', 'msg': 'Invalid cursor parameter'}), 400
        anchor = db.communities.find_one({'_id': ObjectId(cursor)})
        if not anchor:
            return jsonify({'result': 'fail', 'msg': 'Cursor not found'}), 404
        anchor_ts = anchor.get('created_at', '')
        query_parts.append({
            '$or': [
                {'created_at': {'$lt': anchor_ts}},
                {'created_at': anchor_ts, '_id': {'$lt': anchor['_id']}},
            ]
        })

    final_query = {'$and': query_parts} if len(query_parts) > 1 else (query_parts[0] if query_parts else {})
    docs = list(db.communities.find(final_query).sort([('created_at', -1), ('_id', -1)]).limit(limit + 1))
    has_more = len(docs) > limit
    docs = docs[:limit]
    next_cursor = str(docs[-1]['_id']) if has_more else None

    return jsonify({
        'result': 'success',
        'communities': [_serialize_community(d, current_username) for d in docs],
        'has_more': has_more,
        'next_cursor': next_cursor,
    }), 200


@app.route('/api/communities', methods=['POST'])
def create_community_api():
    """Membuat Community / Circle baru beserta foto profil & sampul opsional."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    user_info = db.users.find_one({'username': current_username})
    if not user_info:
        return jsonify({'result': 'fail', 'msg': 'User profile not found'}), 404

    json_body = request.get_json(silent=True) if request.is_json else None
    if json_body is not None:
        raw_name = json_body.get('name', '')
        raw_slug = json_body.get('slug', '')
        raw_desc = json_body.get('description', '')
        raw_vis = json_body.get('visibility', 'public')
    else:
        raw_name = request.form.get('name', '')
        raw_slug = request.form.get('slug', '')
        raw_desc = request.form.get('description', '')
        raw_vis = request.form.get('visibility', 'public')

    if not isinstance(raw_name, str) or not raw_name.strip():
        return jsonify({'result': 'fail', 'msg': 'Nama komunitas wajib diisi.'}), 400
    name = raw_name.strip()
    if len(name) < 3 or len(name) > 60:
        return jsonify({'result': 'fail', 'msg': 'Nama komunitas harus terdiri dari 3 hingga 60 karakter.'}), 400

    slug_input = raw_slug if (isinstance(raw_slug, str) and raw_slug.strip()) else name
    slug = _normalize_community_slug(slug_input)
    if not slug or not COMMUNITY_SLUG_RE.match(slug):
        return jsonify({
            'result': 'fail',
            'msg': 'Slug komunitas harus 3–40 karakter huruf kecil, angka, atau tanda hubung (-).'
        }), 400
    if slug in RESERVED_COMMUNITY_SLUGS:
        return jsonify({'result': 'fail', 'msg': 'Slug tersebut tidak tersedia karena dicadangkan sistem.'}), 400

    if raw_desc is None:
        description = ''
    elif isinstance(raw_desc, str):
        description = raw_desc.strip()
    else:
        return jsonify({'result': 'fail', 'msg': 'Format deskripsi tidak valid.'}), 400
    if len(description) > 500:
        return jsonify({'result': 'fail', 'msg': 'Deskripsi komunitas maksimal 500 karakter.'}), 400

    visibility = str(raw_vis or 'public').strip().lower()
    if visibility not in ('public', 'private'):
        return jsonify({'result': 'fail', 'msg': 'Visibilitas komunitas harus "public" atau "private".'}), 400

    # Cek duplikasi slug sebelum upload gambar
    if db.communities.find_one({'slug': slug}):
        return jsonify({'result': 'fail', 'msg': f'Identifier "/c/{slug}" sudah digunakan komunitas lain.'}), 409

    # Validasi optional profile_image & cover_image menggunakan validator gambar existing
    avatar_file = request.files.get('profile_image') or request.files.get('avatar')
    cover_file = request.files.get('cover_image') or request.files.get('cover')

    validated_avatar = None
    validated_cover = None
    try:
        if avatar_file and (avatar_file.filename or '').strip():
            validated_avatar = validate_post_image_file(avatar_file)
        if cover_file and (cover_file.filename or '').strip():
            validated_cover = validate_post_image_file(cover_file)
    except ValueError as err:
        return jsonify({'result': 'fail', 'msg': str(err)}), 400

    saved_avatar = None
    saved_cover = None
    try:
        if validated_avatar:
            saved_avatar = save_community_image_file(*validated_avatar[:3])
        if validated_cover:
            saved_cover = save_community_image_file(*validated_cover[:3])

        now_iso = datetime.now(timezone.utc).isoformat()
        doc = {
            'name': name,
            'slug': slug,
            'description': description,
            'visibility': visibility,
            'profile_image': saved_avatar,
            'cover_image': saved_cover,
            'creator_id': current_username,
            'member_count': 1,
            'created_at': now_iso,
            'updated_at': now_iso,
        }

        try:
            res = db.communities.insert_one(doc)
        except DuplicateKeyError:
            if saved_avatar:
                safe_delete_community_image(saved_avatar['filename'], check_db_refs=False)
            if saved_cover:
                safe_delete_community_image(saved_cover['filename'], check_db_refs=False)
            return jsonify({'result': 'fail', 'msg': f'Identifier "/c/{slug}" sudah digunakan komunitas lain.'}), 409

        cid = str(res.inserted_id)
        doc['_id'] = res.inserted_id

        # Daftarkan creator sebagai anggota dengan role 'creator'
        db.community_members.insert_one({
            'community_id': cid,
            'user_id': current_username,
            'role': 'creator',
            'joined_at': now_iso,
        })

        return jsonify({
            'result': 'success',
            'msg': 'Komunitas berhasil dibuat.',
            'community': _serialize_community(doc, current_username),
        }), 201
    except Exception as exc:
        if saved_avatar:
            safe_delete_community_image(saved_avatar['filename'], check_db_refs=False)
        if saved_cover:
            safe_delete_community_image(saved_cover['filename'], check_db_refs=False)
        return jsonify({'result': 'fail', 'msg': f'Gagal membuat komunitas: {exc}'}), 500


@app.route('/api/communities/<identifier>', methods=['GET'])
def get_community_api(identifier):
    """Mengambil detail metadata satu komunitas berdasarkan slug atau ID."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    comm_doc = _resolve_community(identifier)
    if not comm_doc:
        return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404

    return jsonify({
        'result': 'success',
        'community': _serialize_community(comm_doc, payload.get('id')),
    }), 200


@app.route('/api/communities/<identifier>/update', methods=['PUT', 'POST'])
def update_community_api(identifier):
    """Memperbarui metadata & gambar komunitas (khusus creator atau moderator)."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    comm_doc = _resolve_community(identifier)
    if not comm_doc:
        return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404

    cid = str(comm_doc['_id'])
    membership = _get_community_membership(cid, current_username)
    if not membership or membership.get('role') not in ('creator', 'moderator'):
        return jsonify({'result': 'fail', 'msg': 'Anda tidak memiliki izin mengelola komunitas ini.'}), 403

    json_body = request.get_json(silent=True) if request.is_json else None
    src = json_body if json_body is not None else request.form

    updates = {}
    if 'name' in src and src.get('name') is not None:
        new_name = str(src.get('name')).strip()
        if len(new_name) < 3 or len(new_name) > 60:
            return jsonify({'result': 'fail', 'msg': 'Nama komunitas harus 3–60 karakter.'}), 400
        updates['name'] = new_name

    if 'description' in src and src.get('description') is not None:
        new_desc = str(src.get('description')).strip()
        if len(new_desc) > 500:
            return jsonify({'result': 'fail', 'msg': 'Deskripsi maksimal 500 karakter.'}), 400
        updates['description'] = new_desc

    if 'visibility' in src and src.get('visibility') is not None:
        new_vis = str(src.get('visibility')).strip().lower()
        if new_vis not in ('public', 'private'):
            return jsonify({'result': 'fail', 'msg': 'Visibilitas harus "public" atau "private".'}), 400
        updates['visibility'] = new_vis

    avatar_file = request.files.get('profile_image') or request.files.get('avatar')
    cover_file = request.files.get('cover_image') or request.files.get('cover')
    validated_avatar = None
    validated_cover = None
    try:
        if avatar_file and (avatar_file.filename or '').strip():
            validated_avatar = validate_post_image_file(avatar_file)
        if cover_file and (cover_file.filename or '').strip():
            validated_cover = validate_post_image_file(cover_file)
    except ValueError as err:
        return jsonify({'result': 'fail', 'msg': str(err)}), 400

    old_avatar_fname = (comm_doc.get('profile_image') or {}).get('filename') if isinstance(comm_doc.get('profile_image'), dict) else None
    old_cover_fname = (comm_doc.get('cover_image') or {}).get('filename') if isinstance(comm_doc.get('cover_image'), dict) else None
    saved_avatar = None
    saved_cover = None

    try:
        if validated_avatar:
            saved_avatar = save_community_image_file(*validated_avatar[:3])
            updates['profile_image'] = saved_avatar
        if validated_cover:
            saved_cover = save_community_image_file(*validated_cover[:3])
            updates['cover_image'] = saved_cover

        if updates:
            updates['updated_at'] = datetime.now(timezone.utc).isoformat()
            db.communities.update_one({'_id': comm_doc['_id']}, {'$set': updates})

            # Sinkronkan visibilitas/nama pada postingan komunitas jika berubah
            post_sync = {}
            if 'visibility' in updates:
                post_sync['community_visibility'] = updates['visibility']
            if 'name' in updates:
                post_sync['community_name'] = updates['name']
            if post_sync:
                db.posts.update_many({'community_id': cid}, {'$set': post_sync})
    except Exception as exc:
        if saved_avatar:
            safe_delete_community_image(saved_avatar['filename'], check_db_refs=False)
        if saved_cover:
            safe_delete_community_image(saved_cover['filename'], check_db_refs=False)
        return jsonify({'result': 'fail', 'msg': f'Gagal memperbarui komunitas: {exc}'}), 500

    if saved_avatar and old_avatar_fname and old_avatar_fname != saved_avatar['filename']:
        safe_delete_community_image(old_avatar_fname, check_db_refs=True)
    if saved_cover and old_cover_fname and old_cover_fname != saved_cover['filename']:
        safe_delete_community_image(old_cover_fname, check_db_refs=True)

    return jsonify({
        'result': 'success',
        'msg': 'Komunitas berhasil diperbarui.',
        'community': _recalc_or_get_community_response(comm_doc, current_username),
    }), 200


@app.route('/api/communities/<identifier>', methods=['DELETE'])
@app.route('/api/communities/<identifier>/delete', methods=['POST'])
def delete_community_api(identifier):
    """Menghapus komunitas beserta seluruh keanggotaan, permintaan, dan postingannya (khusus creator)."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    comm_doc = _resolve_community(identifier)
    if not comm_doc:
        return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404

    if comm_doc.get('creator_id') != current_username:
        return jsonify({'result': 'fail', 'msg': 'Hanya pembuat komunitas yang dapat menghapus komunitas ini.'}), 403

    cid = str(comm_doc['_id'])
    comm_posts = list(db.posts.find({'community_id': cid}))

    db.communities.delete_one({'_id': comm_doc['_id']})
    db.community_members.delete_many({'community_id': cid})
    db.community_requests.delete_many({'community_id': cid})
    db.posts.delete_many({'community_id': cid})

    for p in comm_posts:
        pid_str = str(p['_id'])
        db.bookmarks.delete_many({'post_id': pid_str})
        db.likes.delete_many({'post_id': pid_str})
        db.stars.delete_many({'post_id': pid_str})
        db.thumbs.delete_many({'post_id': pid_str})
        db.comments.delete_many({'post_id': pid_str})
        cleanup_notifications_for_post(pid_str)
        if p.get('post_type') != 'repost':
            for img in (p.get('images') or []):
                if isinstance(img, dict) and img.get('filename'):
                    safe_delete_post_image(img['filename'], check_db_refs=True)

    for key in ('profile_image', 'cover_image'):
        img_obj = comm_doc.get(key)
        if isinstance(img_obj, dict) and img_obj.get('filename'):
            safe_delete_community_image(img_obj['filename'], check_db_refs=True)

    return jsonify({'result': 'success', 'msg': 'Komunitas berhasil dihapus.'}), 200


@app.route('/api/communities/<identifier>/join', methods=['POST'])
def join_community_api(identifier):
    """
    Bergabung ke komunitas:
    - Public community: langsung menjadi member.
    - Private community: berhasil jika memiliki undangan tertunda; jika tidak ada undangan -> 403.
    """
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    comm_doc = _resolve_community(identifier)
    if not comm_doc:
        return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404

    cid = str(comm_doc['_id'])
    if _is_community_member(cid, current_username):
        return jsonify({'result': 'fail', 'msg': 'Anda sudah menjadi anggota komunitas ini.'}), 409

    visibility = comm_doc.get('visibility', 'public')
    pending_invite = db.community_requests.find_one({
        'community_id': cid,
        'user_id': current_username,
        'request_type': 'invite',
        'status': 'pending',
    })

    if visibility == 'private' and not pending_invite:
        return jsonify({
            'result': 'fail',
            'code': 'PRIVATE_COMMUNITY',
            'msg': 'Komunitas ini bersifat privat. Ajukan permintaan bergabung atau tunggu undangan pengelola.'
        }), 403

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        db.community_members.insert_one({
            'community_id': cid,
            'user_id': current_username,
            'role': 'member',
            'joined_at': now_iso,
        })
    except DuplicateKeyError:
        return jsonify({'result': 'fail', 'msg': 'Anda sudah menjadi anggota komunitas ini.'}), 409

    if pending_invite:
        db.community_requests.update_one(
            {'_id': pending_invite['_id']},
            {'$set': {'status': 'approved', 'updated_at': now_iso}}
        )
    # Bersihkan permintaan pending lain jika ada
    db.community_requests.delete_many({
        'community_id': cid,
        'user_id': current_username,
        'status': 'pending',
    })

    _sync_community_member_count(cid)
    return jsonify({
        'result': 'success',
        'msg': 'Berhasil bergabung ke komunitas.',
        'community': _recalc_or_get_community_response(comm_doc, current_username),
    }), 200


@app.route('/api/communities/<identifier>/leave', methods=['POST'])
def leave_community_api(identifier):
    """Keluar dari suatu komunitas. Creator tunggal tidak dapat meninggalkan komunitas tanpa menghapusnya."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    comm_doc = _resolve_community(identifier)
    if not comm_doc:
        return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404

    cid = str(comm_doc['_id'])
    membership = _get_community_membership(cid, current_username)
    if not membership:
        return jsonify({'result': 'fail', 'msg': 'Anda bukan anggota komunitas ini.'}), 400

    if membership.get('role') == 'creator':
        return jsonify({
            'result': 'fail',
            'msg': 'Pembuat komunitas tidak dapat keluar secara langsung. Hapus komunitas jika ingin menutupnya.'
        }), 400

    db.community_members.delete_one({'community_id': cid, 'user_id': current_username})
    _sync_community_member_count(cid)

    return jsonify({
        'result': 'success',
        'msg': 'Berhasil keluar dari komunitas.',
        'community': _recalc_or_get_community_response(comm_doc, current_username),
    }), 200


@app.route('/api/communities/<identifier>/request', methods=['POST'])
def request_join_community_api(identifier):
    """Mengajukan permintaan bergabung ke komunitas privat."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    comm_doc = _resolve_community(identifier)
    if not comm_doc:
        return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404

    cid = str(comm_doc['_id'])
    if _is_community_member(cid, current_username):
        return jsonify({'result': 'fail', 'msg': 'Anda sudah menjadi anggota komunitas ini.'}), 409

    if comm_doc.get('visibility') == 'public':
        return jsonify({'result': 'fail', 'msg': 'Komunitas publik dapat langsung diikuti tanpa persetujuan.'}), 400

    # Jika user sebenarnya sudah punya undangan pending, langsung terima
    pending_invite = db.community_requests.find_one({
        'community_id': cid,
        'user_id': current_username,
        'request_type': 'invite',
        'status': 'pending',
    })
    now_iso = datetime.now(timezone.utc).isoformat()
    if pending_invite:
        db.community_members.insert_one({
            'community_id': cid,
            'user_id': current_username,
            'role': 'member',
            'joined_at': now_iso,
        })
        db.community_requests.update_one(
            {'_id': pending_invite['_id']},
            {'$set': {'status': 'approved', 'updated_at': now_iso}}
        )
        _sync_community_member_count(cid)
        return jsonify({
            'result': 'success',
            'msg': 'Undangan tersedia — Anda langsung bergabung ke komunitas.',
            'community': _recalc_or_get_community_response(comm_doc, current_username),
        }), 200

    existing_req = db.community_requests.find_one({
        'community_id': cid,
        'user_id': current_username,
        'request_type': 'request',
        'status': 'pending',
    })
    if existing_req:
        return jsonify({'result': 'fail', 'msg': 'Permintaan bergabung Anda masih menunggu persetujuan.'}), 409

    db.community_requests.insert_one({
        'community_id': cid,
        'user_id': current_username,
        'request_type': 'request',
        'status': 'pending',
        'invited_by': None,
        'created_at': now_iso,
        'updated_at': now_iso,
    })

    create_notification(
        recipient=comm_doc.get('creator_id'),
        actor=current_username,
        notif_type='community_join_request',
        message=f"meminta bergabung ke komunitas {comm_doc.get('name', '')}."
    )

    return jsonify({
        'result': 'success',
        'msg': 'Permintaan bergabung berhasil dikirim.',
        'community': _recalc_or_get_community_response(comm_doc, current_username),
    }), 201


@app.route('/api/communities/<identifier>/request/cancel', methods=['POST'])
def cancel_join_request_api(identifier):
    """Membatalkan permintaan bergabung yang masih pending."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    comm_doc = _resolve_community(identifier)
    if not comm_doc:
        return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404

    cid = str(comm_doc['_id'])
    db.community_requests.delete_many({
        'community_id': cid,
        'user_id': current_username,
        'request_type': 'request',
        'status': 'pending',
    })
    return jsonify({
        'result': 'success',
        'msg': 'Permintaan bergabung dibatalkan.',
        'community': _recalc_or_get_community_response(comm_doc, current_username),
    }), 200


@app.route('/api/communities/<identifier>/invite', methods=['POST'])
def invite_to_community_api(identifier):
    """Mengundang pengguna ke komunitas (khusus creator/moderator)."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    comm_doc = _resolve_community(identifier)
    if not comm_doc:
        return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404

    cid = str(comm_doc['_id'])
    membership = _get_community_membership(cid, current_username)
    if not membership or membership.get('role') not in ('creator', 'moderator'):
        return jsonify({'result': 'fail', 'msg': 'Hanya pengelola komunitas yang dapat mengirim undangan.'}), 403

    json_body = request.get_json(silent=True) if request.is_json else None
    target_username = (json_body.get('username') if json_body else request.form.get('username', '') or '').strip().lstrip('@')
    if not target_username:
        return jsonify({'result': 'fail', 'msg': 'Username target wajib diisi.'}), 400

    target_user = db.users.find_one({'username': target_username})
    if not target_user:
        return jsonify({'result': 'fail', 'msg': 'Pengguna tidak ditemukan.'}), 404

    if _is_community_member(cid, target_username):
        return jsonify({'result': 'fail', 'msg': f'@{target_username} sudah menjadi anggota komunitas ini.'}), 409

    now_iso = datetime.now(timezone.utc).isoformat()
    # Jika target sudah mengirim request pending, langsung setujui saat pengelola mengundangnya
    pending_req = db.community_requests.find_one({
        'community_id': cid,
        'user_id': target_username,
        'request_type': 'request',
        'status': 'pending',
    })
    if pending_req:
        db.community_members.insert_one({
            'community_id': cid,
            'user_id': target_username,
            'role': 'member',
            'joined_at': now_iso,
        })
        db.community_requests.update_one(
            {'_id': pending_req['_id']},
            {'$set': {'status': 'approved', 'updated_at': now_iso}}
        )
        _sync_community_member_count(cid)
        create_notification(
            recipient=target_username,
            actor=current_username,
            notif_type='community_request_approved',
            message=f"menyetujui permintaan Anda bergabung ke {comm_doc.get('name', '')}."
        )
        return jsonify({'result': 'success', 'msg': f'Permintaan @{target_username} langsung disetujui dan ditambahkan.'}), 200

    existing_invite = db.community_requests.find_one({
        'community_id': cid,
        'user_id': target_username,
        'request_type': 'invite',
        'status': 'pending',
    })
    if existing_invite:
        return jsonify({'result': 'fail', 'msg': f'Undangan untuk @{target_username} sudah dikirim sebelumnya.'}), 409

    db.community_requests.insert_one({
        'community_id': cid,
        'user_id': target_username,
        'request_type': 'invite',
        'status': 'pending',
        'invited_by': current_username,
        'created_at': now_iso,
        'updated_at': now_iso,
    })

    create_notification(
        recipient=target_username,
        actor=current_username,
        notif_type='community_invite',
        message=f"mengundang Anda bergabung ke komunitas {comm_doc.get('name', '')}."
    )
    return jsonify({'result': 'success', 'msg': f'Undangan berhasil dikirim ke @{target_username}.'}), 201


@app.route('/api/communities/<identifier>/invite/accept', methods=['POST'])
def accept_community_invite_api(identifier):
    """Menerima undangan bergabung ke komunitas."""
    return join_community_api(identifier)


@app.route('/api/communities/<identifier>/invite/decline', methods=['POST'])
def decline_community_invite_api(identifier):
    """Menolak undangan bergabung ke komunitas."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    comm_doc = _resolve_community(identifier)
    if not comm_doc:
        return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404

    cid = str(comm_doc['_id'])
    inv = db.community_requests.find_one({
        'community_id': cid,
        'user_id': current_username,
        'request_type': 'invite',
        'status': 'pending',
    })
    if not inv:
        return jsonify({'result': 'fail', 'msg': 'Undangan tidak ditemukan.'}), 404

    db.community_requests.update_one(
        {'_id': inv['_id']},
        {'$set': {'status': 'rejected', 'updated_at': datetime.now(timezone.utc).isoformat()}}
    )
    return jsonify({
        'result': 'success',
        'msg': 'Undangan ditolak.',
        'community': _recalc_or_get_community_response(comm_doc, current_username),
    }), 200


@app.route('/api/communities/<identifier>/requests', methods=['GET'])
def list_community_requests_api(identifier):
    """Melihat daftar permintaan bergabung & undangan tertunda (khusus creator/moderator)."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    comm_doc = _resolve_community(identifier)
    if not comm_doc:
        return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404

    cid = str(comm_doc['_id'])
    membership = _get_community_membership(cid, current_username)
    if not membership or membership.get('role') not in ('creator', 'moderator'):
        return jsonify({'result': 'fail', 'msg': 'Anda tidak memiliki izin melihat permintaan keanggotaan.'}), 403

    pending_docs = list(db.community_requests.find({
        'community_id': cid,
        'status': 'pending',
    }).sort([('created_at', -1)]))

    usernames = list({r['user_id'] for r in pending_docs if r.get('user_id')})
    users_map = {}
    if usernames:
        for u in db.users.find({'username': {'$in': usernames}}, {'password': 0}):
            users_map[u['username']] = u

    enriched = []
    for r in pending_docs:
        u = users_map.get(r['user_id'], {})
        enriched.append({
            '_id': str(r['_id']),
            'user_id': r['user_id'],
            'profile_name': u.get('profile_name', r['user_id']),
            'profile_pic_real': u.get('profile_pic_real', 'profile_pics/profile_placeholder.png'),
            'request_type': r.get('request_type', 'request'),
            'invited_by': r.get('invited_by'),
            'created_at': r.get('created_at', ''),
        })

    return jsonify({'result': 'success', 'requests': enriched}), 200


@app.route('/api/communities/<identifier>/requests/<target_username>/approve', methods=['POST'])
def approve_community_request_api(identifier, target_username):
    """Menyetujui permintaan bergabung user ke komunitas privat (khusus creator/moderator)."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    comm_doc = _resolve_community(identifier)
    if not comm_doc:
        return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404

    cid = str(comm_doc['_id'])
    membership = _get_community_membership(cid, current_username)
    if not membership or membership.get('role') not in ('creator', 'moderator'):
        return jsonify({'result': 'fail', 'msg': 'Anda tidak memiliki izin menyetujui permintaan.'}), 403

    req_doc = db.community_requests.find_one({
        'community_id': cid,
        'user_id': target_username,
        'request_type': 'request',
        'status': 'pending',
    })
    if not req_doc:
        return jsonify({'result': 'fail', 'msg': 'Permintaan bergabung tidak ditemukan.'}), 404

    now_iso = datetime.now(timezone.utc).isoformat()
    if not _is_community_member(cid, target_username):
        db.community_members.insert_one({
            'community_id': cid,
            'user_id': target_username,
            'role': 'member',
            'joined_at': now_iso,
        })
    db.community_requests.update_one(
        {'_id': req_doc['_id']},
        {'$set': {'status': 'approved', 'updated_at': now_iso}}
    )
    _sync_community_member_count(cid)

    create_notification(
        recipient=target_username,
        actor=current_username,
        notif_type='community_request_approved',
        message=f"menyetujui permintaan bergabung Anda ke {comm_doc.get('name', '')}."
    )

    return jsonify({
        'result': 'success',
        'msg': f'@{target_username} disetujui menjadi anggota.',
        'community': _recalc_or_get_community_response(comm_doc, current_username),
    }), 200


@app.route('/api/communities/<identifier>/requests/<target_username>/reject', methods=['POST'])
def reject_community_request_api(identifier, target_username):
    """Menolak permintaan bergabung user ke komunitas privat (khusus creator/moderator)."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    comm_doc = _resolve_community(identifier)
    if not comm_doc:
        return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404

    cid = str(comm_doc['_id'])
    membership = _get_community_membership(cid, current_username)
    if not membership or membership.get('role') not in ('creator', 'moderator'):
        return jsonify({'result': 'fail', 'msg': 'Anda tidak memiliki izin menolak permintaan.'}), 403

    req_doc = db.community_requests.find_one({
        'community_id': cid,
        'user_id': target_username,
        'status': 'pending',
    })
    if not req_doc:
        return jsonify({'result': 'fail', 'msg': 'Permintaan atau undangan tidak ditemukan.'}), 404

    db.community_requests.update_one(
        {'_id': req_doc['_id']},
        {'$set': {'status': 'rejected', 'updated_at': datetime.now(timezone.utc).isoformat()}}
    )
    return jsonify({'result': 'success', 'msg': f'Permintaan @{target_username} ditolak.'}), 200


@app.route('/api/communities/<identifier>/members', methods=['GET'])
def list_community_members_api(identifier):
    """Mengambil daftar anggota komunitas (komunitas privat hanya dapat dilihat oleh anggotanya)."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    comm_doc = _resolve_community(identifier)
    if not comm_doc:
        return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404

    cid = str(comm_doc['_id'])
    if comm_doc.get('visibility') == 'private' and not _is_community_member(cid, current_username):
        return jsonify({'result': 'fail', 'msg': 'Daftar anggota komunitas privat hanya dapat dilihat oleh anggota.'}), 403

    memberships = list(db.community_members.find({'community_id': cid}).sort([('joined_at', 1)]))
    usernames = [m['user_id'] for m in memberships if m.get('user_id')]
    users_map = {}
    if usernames:
        for u in db.users.find({'username': {'$in': usernames}}, {'password': 0}):
            users_map[u['username']] = u

    members = []
    for m in memberships:
        uname = m['user_id']
        u = users_map.get(uname, {})
        members.append({
            'username': uname,
            'profile_name': u.get('profile_name', uname),
            'profile_pic_real': u.get('profile_pic_real', 'profile_pics/profile_placeholder.png'),
            'profile_info': u.get('profile_info', ''),
            'role': m.get('role', 'member'),
            'joined_at': m.get('joined_at', ''),
        })

    return jsonify({'result': 'success', 'members': members, 'total': len(members)}), 200


@app.route('/api/communities/<identifier>/members/<target_username>/remove', methods=['POST'])
def remove_community_member_api(identifier, target_username):
    """Mengeluarkan anggota dari komunitas (khusus creator/moderator; creator tidak bisa dikeluarkan)."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    comm_doc = _resolve_community(identifier)
    if not comm_doc:
        return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404

    cid = str(comm_doc['_id'])
    actor_m = _get_community_membership(cid, current_username)
    if not actor_m or actor_m.get('role') not in ('creator', 'moderator'):
        return jsonify({'result': 'fail', 'msg': 'Anda tidak memiliki izin mengeluarkan anggota.'}), 403

    target_m = _get_community_membership(cid, target_username)
    if not target_m:
        return jsonify({'result': 'fail', 'msg': 'Anggota tidak ditemukan.'}), 404

    if target_m.get('role') == 'creator':
        return jsonify({'result': 'fail', 'msg': 'Pembuat komunitas tidak dapat dikeluarkan.'}), 400
    if actor_m.get('role') == 'moderator' and target_m.get('role') == 'moderator':
        return jsonify({'result': 'fail', 'msg': 'Hanya pembuat komunitas yang dapat mengeluarkan moderator.'}), 403

    db.community_members.delete_one({'community_id': cid, 'user_id': target_username})
    _sync_community_member_count(cid)
    return jsonify({
        'result': 'success',
        'msg': f'@{target_username} telah dikeluarkan dari komunitas.',
        'community': _recalc_or_get_community_response(comm_doc, current_username),
    }), 200


@app.route('/api/communities/<identifier>/members/<target_username>/role', methods=['POST'])
def update_community_member_role_api(identifier, target_username):
    """Mengubah peran anggota menjadi 'moderator' atau 'member' (khusus creator)."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    comm_doc = _resolve_community(identifier)
    if not comm_doc:
        return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404

    if comm_doc.get('creator_id') != current_username:
        return jsonify({'result': 'fail', 'msg': 'Hanya pembuat komunitas yang dapat mengubah peran anggota.'}), 403

    json_body = request.get_json(silent=True) if request.is_json else None
    new_role = ((json_body.get('role') if json_body else request.form.get('role', '')) or '').strip().lower()
    if new_role not in ('member', 'moderator'):
        return jsonify({'result': 'fail', 'msg': 'Peran harus "member" atau "moderator".'}), 400

    cid = str(comm_doc['_id'])
    target_m = _get_community_membership(cid, target_username)
    if not target_m:
        return jsonify({'result': 'fail', 'msg': 'Anggota tidak ditemukan.'}), 404
    if target_m.get('role') == 'creator':
        return jsonify({'result': 'fail', 'msg': 'Peran pembuat komunitas tidak dapat diturunkan.'}), 400

    db.community_members.update_one(
        {'community_id': cid, 'user_id': target_username},
        {'$set': {'role': new_role}}
    )
    return jsonify({'result': 'success', 'msg': f'Peran @{target_username} diperbarui menjadi {new_role}.'}), 200


@app.route('/api/communities/<identifier>/posts', methods=['GET', 'POST'])
def community_posts_api(identifier):
    """Mengambil atau membuat postingan di dalam sebuah Community / Circle."""
    payload = get_current_user_from_token()
    if not payload:
        return jsonify({'result': 'fail', 'msg': 'Authentication required'}), 401

    current_username = payload.get('id')
    comm_doc = _resolve_community(identifier)
    if not comm_doc:
        return jsonify({'result': 'fail', 'msg': 'Komunitas tidak ditemukan.'}), 404

    cid = str(comm_doc['_id'])
    is_member = _is_community_member(cid, current_username)

    if request.method == 'GET':
        if comm_doc.get('visibility') == 'private' and not is_member:
            return jsonify({'result': 'fail', 'msg': 'Postingan komunitas privat hanya dapat dilihat oleh anggotanya.'}), 403

        cursor = request.args.get('cursor', '').strip()
        limit_raw = request.args.get('limit', '20').strip()
        try:
            limit = max(1, min(int(limit_raw), 50))
        except ValueError:
            return jsonify({'result': 'fail', 'msg': 'Invalid limit parameter'}), 400

        query = {'community_id': cid}
        if cursor:
            if not ObjectId.is_valid(cursor):
                return jsonify({'result': 'fail', 'msg': 'Invalid cursor parameter'}), 400
            anchor = db.posts.find_one({'_id': ObjectId(cursor), 'community_id': cid})
            if not anchor:
                return jsonify({'result': 'fail', 'msg': 'Cursor not found'}), 404
            query = {
                '$and': [
                    {'community_id': cid},
                    {'$or': [
                        {'date': {'$lt': anchor.get('date', '')}},
                        {'date': anchor.get('date', ''), '_id': {'$lt': anchor['_id']}},
                    ]}
                ]
            }

        docs = list(db.posts.find(query).sort([('date', -1), ('_id', -1)]).limit(limit + 1))
        has_more = len(docs) > limit
        docs = docs[:limit]
        posts = enrich_posts_metadata(docs, current_username)
        next_cursor = str(posts[-1]['_id']) if has_more else None
        return jsonify({
            'result': 'success',
            'posts': posts,
            'has_more': has_more,
            'next_cursor': next_cursor,
        }), 200

    # POST: membuat postingan ke dalam komunitas (wajib member)
    if not is_member:
        return jsonify({'result': 'fail', 'msg': 'Anda harus bergabung dengan komunitas ini untuk membuat postingan.'}), 403

    user_info = db.users.find_one({'username': current_username})
    if not user_info:
        return jsonify({'result': 'fail', 'msg': 'User profile not found'}), 404

    json_body = request.get_json(silent=True) if request.is_json else None
    if json_body is not None:
        comment_receive = (json_body.get('comment') or json_body.get('comment_give') or '').strip()
        date_receive = json_body.get('date_give') or datetime.now(timezone.utc).isoformat()
    else:
        comment_receive = (request.form.get('comment_give') or request.form.get('comment') or '').strip()
        date_receive = request.form.get('date_give') or datetime.now(timezone.utc).isoformat()

    raw_files = []
    for key in ('images', 'images[]', 'file_give'):
        raw_files.extend(request.files.getlist(key))

    if len(raw_files) > MAX_POST_IMAGES_COUNT:
        return jsonify({'result': 'fail', 'msg': f'Maksimal {MAX_POST_IMAGES_COUNT} gambar dalam satu postingan.'}), 400
    if not comment_receive and len(raw_files) == 0:
        return jsonify({'result': 'fail', 'msg': 'Isi postingan atau minimal satu gambar wajib diisi.'}), 400
    if len(comment_receive) > 5000:
        return jsonify({'result': 'fail', 'msg': 'Panjang postingan melebihi batas maksimal 5000 karakter.'}), 400

    validated_payloads = []
    for f in raw_files:
        try:
            validated_payloads.append(validate_post_image_file(f))
        except ValueError as err:
            return jsonify({'result': 'fail', 'msg': str(err)}), 400

    saved_images = []
    try:
        for data_bytes, ext, mime_type, _size in validated_payloads:
            saved_images.append(save_post_image_file(data_bytes, ext, mime_type))

        created_doc = _create_published_post(
            user_info=user_info,
            comment_text=comment_receive,
            date_str=date_receive,
            images_list=saved_images,
            community=comm_doc,
        )
        return jsonify({
            'result': 'success',
            'msg': 'Postingan komunitas berhasil dibuat.',
            'post_id': created_doc['_id'],
            'post': created_doc,
        }), 201
    except ValueError as verr:
        for img in saved_images:
            safe_delete_post_image(img.get('filename'), check_db_refs=False)
        return jsonify({'result': 'fail', 'msg': str(verr)}), 400
    except Exception as exc:
        for img in saved_images:
            safe_delete_post_image(img.get('filename'), check_db_refs=False)
        return jsonify({'result': 'fail', 'msg': f'Gagal membuat postingan komunitas: {exc}'}), 500


if __name__ == "__main__":
    app.run('0.0.0.0', port=5000, debug=True)
