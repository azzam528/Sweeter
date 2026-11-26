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

# Konfigurasi Upload Media Postingan (dapat dikonfigurasi via env var untuk deployment/Docker)
DEFAULT_POST_UPLOAD_DIR = os.path.join(app.root_path, "uploads", "posts")
app.config["POST_UPLOAD_DIR"] = os.path.abspath(
    os.getenv("POST_UPLOAD_DIR") or DEFAULT_POST_UPLOAD_DIR
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
    Mengembalikan payload dict jika valid, atau None jika tidak valid/kadaluarsa/hilang.
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
        return payload
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError, Exception):
        return None


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
        post_count = db.posts.count_documents({'username': username})
        is_following = bool(db.follows.find_one({'follower': current_username, 'following': username}))
        follower_count = db.follows.count_documents({'following': username})
        following_count = db.follows.count_documents({'follower': username})
        return render_template(
            'user_profile.html',
            user_info=user_info,
            current_user=current_user,
            status=status,
            post_count=post_count,
            is_following=is_following,
            follower_count=follower_count,
            following_count=following_count
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
        payload = {
            "id": username_receive,
            # token berlaku 24 jam (menggunakan datetime timezone UTC standar)
            "exp": datetime.now(timezone.utc) + timedelta(seconds=60 * 60 * 24),
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")

        response = jsonify(
            {
                "result": "success",
                "token": token,
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
    doc = {
        "username": username_receive,                               # id
        "password": password_hash,                                  # password
        "profile_name": username_receive,                           # user's name is set to their id by default
        "profile_pic": "",                                          # profile image file name
        "profile_pic_real": "profile_pics/profile_placeholder.png", # a default profile image
        "profile_info": ""                                          # a profile description
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


def save_post_image_file(data: bytes, ext: str, mime_type: str) -> dict:
    """Menyimpan byte gambar tervalidasi ke POST_UPLOAD_DIR dengan nama unik yang aman."""
    upload_dir = os.path.abspath(app.config["POST_UPLOAD_DIR"])
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
        "url": f"/uploads/posts/{filename}",
        "mime_type": mime_type,
        "size": len(data),
    }


def safe_delete_post_image(filename: str, check_db_refs: bool = True) -> bool:
    """
    Menghapus file gambar post dari disk secara aman setelah memvalidasi nama file
    dan memastikan tidak ada dokumen postingan lain yang masih mereferensikannya.
    """
    if not filename or not isinstance(filename, str):
        return False
    if not SAFE_POST_IMAGE_FILENAME_RE.match(filename):
        return False

    if check_db_refs:
        try:
            if db.posts.count_documents({"images.filename": filename}) > 0:
                return False
            if db.drafts.count_documents({"images.filename": filename}) > 0:
                return False
        except Exception:
            return False

    upload_dir = os.path.abspath(app.config["POST_UPLOAD_DIR"])
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


@app.route('/uploads/posts/<path:filename>', methods=['GET'])
def serve_post_image(filename):
    """Menyajikan gambar postingan secara aman tanpa membuka akses ke direktori lain."""
    if not filename or not SAFE_POST_IMAGE_FILENAME_RE.match(filename):
        abort(404)

    upload_dir = os.path.abspath(app.config["POST_UPLOAD_DIR"])
    target_path = os.path.abspath(os.path.join(upload_dir, filename))
    if os.path.commonpath([upload_dir, target_path]) != upload_dir:
        abort(404)
    if not os.path.isfile(target_path):
        abort(404)

    response = send_from_directory(upload_dir, filename)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


def _create_published_post(user_info: dict, comment_text: str, date_str: str = None, images_list: list = None) -> dict:
    """
    Helper terpusat untuk memvalidasi dan membuat dokumen postingan publik baru di db.posts.
    Digunakan bersama oleh POST /posting dan POST /api/drafts/<draft_id>/publish agar tidak
    ada duplikasi logika pembuatan postingan.
    """
    comment_clean = (comment_text or '').strip() if isinstance(comment_text, str) else ''
    images_clean = list(images_list) if isinstance(images_list, list) else []

    if not comment_clean and len(images_clean) == 0:
        raise ValueError('Isi postingan atau minimal satu gambar wajib diisi.')

    if len(comment_clean) > 5000:
        raise ValueError('Panjang postingan melebihi batas maksimal 5000 karakter.')

    if len(images_clean) > MAX_POST_IMAGES_COUNT:
        raise ValueError(f'Maksimal {MAX_POST_IMAGES_COUNT} gambar dalam satu postingan.')

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
    }
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
        )
        return jsonify({
            'result': 'success',
            'msg': 'Posting successfull',
            'post_id': created_doc['_id'],
            'images': saved_images,
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
    
    
def enrich_posts_metadata(posts, current_username):
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
    username_receive = request.args.get('username_give', '').strip()
    tab = request.args.get('tab', '').lower().strip()
    cursor = request.args.get('cursor', '').strip()
    limit_raw = request.args.get('limit', '20').strip()

    try:
        limit = int(limit_raw)
        if limit <= 0 or limit > 50:
            limit = 20
    except ValueError:
        return jsonify({'result': 'fail', 'msg': 'Invalid limit parameter'}), 400

    # 2. Penentuan filter pengguna (Author Filter)
    if username_receive:
        # Mode Profil: hanya postingan milik pengguna yang diminta
        query = {'username': username_receive}
    elif tab in ['following', 'timeline']:
        # Mode Personalized Timeline: postingan diri sendiri + akun yang diikuti
        following_users = db.follows.distinct('following', {'follower': current_username})
        allowed_authors = [current_username] + following_users
        query = {'username': {'$in': allowed_authors}}
    else:
        # Mode Explore / All / Default (mempertahankan kompatibilitas endpoint lama): semua postingan publik
        query = {}

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
    posts = enrich_posts_metadata(posts, current_username)

    has_more = (len(posts) == limit)
    next_cursor = str(posts[-1]['_id']) if has_more else None

    return jsonify({
        'result': 'success',
        'msg': 'Successfully fetched posts',
        'posts': posts,
        'next_cursor': next_cursor,
        'has_more': has_more,
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

    # 2. Ambil komentar terurut ascending berdasarkan waktu pembuatan
    comments_cursor = db.comments.find({'post_id': str(post_id_receive)}).sort('created_at', 1)
    comments_list = []
    for c in comments_cursor:
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

    # Aturan 3: Cegah duplicate follow (race-safe & idempotent)
    existing = db.follows.find_one({'follower': current_username, 'following': target})
    if existing:
        follower_count = db.follows.count_documents({'following': target})
        following_count = db.follows.count_documents({'follower': current_username})
        return jsonify({
            'result': 'fail',
            'msg': 'You are already following this user',
            'is_following': True,
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

    # Hapus relasi follow secara aman
    db.follows.delete_one({'follower': current_username, 'following': target})

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
    follower_count = db.follows.count_documents({'following': target})
    following_count = db.follows.count_documents({'follower': target})

    return jsonify({
        'result': 'success',
        'target_username': target,
        'is_following': is_following,
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

    follow_docs = list(db.follows.find({'following': username}).sort('created_at', -1))
    follower_usernames = [d['follower'] for d in follow_docs]

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

    follow_docs = list(db.follows.find({'follower': username}).sort('created_at', -1))
    following_usernames = [d['following'] for d in follow_docs]

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

    # Pastikan postingan ada di db.posts
    post_doc = db.posts.find_one({'_id': ObjectId(target_pid)})
    if not post_doc:
        return jsonify({'result': 'fail', 'msg': 'Post not found'}), 404

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
    if not post_doc:
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

    # Cegah repost postingan milik sendiri
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
    match_query = {'post_type': {'$ne': 'repost'}}

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

    query = {'hashtags': clean_tag, 'post_type': {'$ne': 'repost'}}
    total_count = db.posts.count_documents({'hashtags': clean_tag, 'post_type': {'$ne': 'repost'}})

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

    # 1. Search Users
    if search_type in ['all', 'users']:
        clean_user_q = re.escape(q.lstrip('@').strip())
        user_matches = list(db.users.find({
            '$or': [
                {'username': {'$regex': clean_user_q, '$options': 'i'}},
                {'profile_name': {'$regex': clean_user_q, '$options': 'i'}}
            ]
        }, {'password': 0}).limit(limit))

        for u in user_matches:
            uname = u['username']
            results['users'].append({
                'username': uname,
                'profile_name': u.get('profile_name', uname),
                'profile_pic_real': u.get('profile_pic_real', 'profile_pics/profile_placeholder.png'),
                'profile_info': u.get('profile_info', ''),
                'follower_count': count_followers(uname),
                'is_following': is_following(current_username, uname) if current_username else False,
                'is_mine': (uname == current_username)
            })

    # 2. Search Hashtags
    if search_type in ['all', 'tags'] or q.startswith('#'):
        clean_tag_q = q.lstrip('#').lower().strip()
        if clean_tag_q:
            safe_tag_q = re.escape(clean_tag_q)
            pipeline = [
                {'$match': {'hashtags': {'$regex': safe_tag_q, '$options': 'i'}, 'post_type': {'$ne': 'repost'}}},
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
        post_query = {'comment': {'$regex': safe_q, '$options': 'i'}, 'post_type': {'$ne': 'repost'}}
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


if __name__ == "__main__":
    app.run('0.0.0.0', port=5000, debug=True)
