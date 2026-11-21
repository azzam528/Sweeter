import os
import hashlib
import jwt
import warnings

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
)

from werkzeug.utils import secure_filename
from dotenv import load_dotenv

from database import db, check_connection

load_dotenv()

app = Flask(__name__)

app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["UPLOAD_FOLDER"] = "./static/profile_pics"

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

@app.route('/posting', methods=['POST'])
def posting():
    token_receive = request.cookies.get('mytoken')
    try:
        payload = jwt.decode(
            token_receive,
            SECRET_KEY,
            algorithms=['HS256']
        )
        user_info = db.users.find_one({'username': payload.get('id')})
        comment_receive = request.form.get('comment_give')
        date_receive = request.form.get('date_give')
        doc = {
            'username': user_info.get('username'),
            'profile_name' : user_info.get('profile_name'),
            'profile_pic_real' : user_info.get('profile_pic_real'),
            'comment' : comment_receive,
            'date': date_receive,
            
        }
        db.posts.insert_one(doc)
        return jsonify({
            'result': 'success',
            'msg' : 'Posting successfull'
        })
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return redirect(url_for('home'))    
    
    
def enrich_posts_metadata(posts, current_username):
    """
    Memperkaya daftar dokumen post dengan:
    1. Batch comments count
    2. Batch reactions count (heart, stars, thumbs)
    3. Status reaksi pengguna saat ini (heart_by_me, stars_by_me, thumbs_by_me)
    4. Status bookmark pengguna saat ini (is_bookmarked)
    5. Hak akses kepemilikan postingan (is_mine)
    6. Indikator editan postingan (is_edited)
    """
    post_ids = [str(post['_id']) for post in posts]
    comment_counts = {}
    heart_counts = {}
    stars_counts = {}
    thumbs_counts = {}
    my_hearts = set()
    my_stars = set()
    my_thumbs = set()
    my_bookmarks = set()

    if post_ids:
        # Batch count komentar
        pipeline_comments = [
            {'$match': {'post_id': {'$in': post_ids}}},
            {'$group': {'_id': '$post_id', 'count': {'$sum': 1}}}
        ]
        for item in db.comments.aggregate(pipeline_comments):
            comment_counts[item['_id']] = item['count']

        # Batch count reactions
        for item in db.likes.aggregate([
            {'$match': {'post_id': {'$in': post_ids}, 'type': 'heart'}},
            {'$group': {'_id': '$post_id', 'count': {'$sum': 1}}}
        ]):
            heart_counts[item['_id']] = item['count']

        for item in db.stars.aggregate([
            {'$match': {'post_id': {'$in': post_ids}, 'type': 'stars'}},
            {'$group': {'_id': '$post_id', 'count': {'$sum': 1}}}
        ]):
            stars_counts[item['_id']] = item['count']

        for item in db.thumbs.aggregate([
            {'$match': {'post_id': {'$in': post_ids}, 'type': 'thumbs'}},
            {'$group': {'_id': '$post_id', 'count': {'$sum': 1}}}
        ]):
            thumbs_counts[item['_id']] = item['count']

        if current_username:
            my_hearts = set(db.likes.distinct('post_id', {
                'post_id': {'$in': post_ids},
                'type': 'heart',
                'username': current_username
            }))
            my_stars = set(db.stars.distinct('post_id', {
                'post_id': {'$in': post_ids},
                'type': 'stars',
                'username': current_username
            }))
            my_thumbs = set(db.thumbs.distinct('post_id', {
                'post_id': {'$in': post_ids},
                'type': 'thumbs',
                'username': current_username
            }))
            my_bookmarks = set(db.bookmarks.distinct('post_id', {
                'post_id': {'$in': post_ids},
                'username': current_username
            }))

    for post in posts:
        pid = str(post['_id'])
        post['_id'] = pid
        post['count_heart'] = heart_counts.get(pid, 0)
        post['heart_by_me'] = pid in my_hearts
        post['count_stars'] = stars_counts.get(pid, 0)
        post['stars_by_me'] = pid in my_stars
        post['count_thumbs'] = thumbs_counts.get(pid, 0)
        post['thumbs_by_me'] = pid in my_thumbs
        post['count_comments'] = comment_counts.get(pid, 0)
        post['is_bookmarked'] = pid in my_bookmarks
        post['is_mine'] = (post.get('username') == current_username)
        post['is_edited'] = bool(post.get('is_edited', False))

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

    # Validasi konten baru
    new_content = None
    if request.is_json:
        new_content = (request.get_json(silent=True) or {}).get('comment')
    if not new_content:
        new_content = request.form.get('comment_give') or request.form.get('comment')

    if not new_content or not isinstance(new_content, str) or not new_content.strip():
        return jsonify({'result': 'fail', 'msg': 'Post content cannot be empty'}), 400

    new_content = new_content.strip()
    if len(new_content) > 5000:
        return jsonify({'result': 'fail', 'msg': 'Post content exceeds maximum length'}), 400

    now = datetime.now(timezone.utc).isoformat()
    db.posts.update_one(
        {'_id': ObjectId(target_pid)},
        {'$set': {
            'comment': new_content,
            'is_edited': True,
            'updated_at': now
        }}
    )

    return jsonify({
        'result': 'success',
        'msg': 'Post updated successfully',
        'post_id': target_pid,
        'comment': new_content,
        'is_edited': True
    }), 200


@app.route('/api/posts/<post_id>/delete', methods=['POST'])
@app.route('/delete_post', methods=['POST'])
def delete_post_endpoint(post_id=None):
    """
    Endpoint penghapusan postingan milik pengguna sendiri.
    Menghapus dokumen post dan membersihkan data referensi terkait (bookmarks, reactions, comments).
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

    return jsonify({
        'result': 'success',
        'msg': 'Post deleted successfully',
        'post_id': target_pid
    }), 200  

if __name__ == "__main__":
    app.run('0.0.0.0', port=5000, debug=True)
