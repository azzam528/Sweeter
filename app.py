import os
import hashlib
import jwt
import warnings

from datetime import datetime, timedelta, timezone
from bson import ObjectId
from bson.errors import InvalidId

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
        status = username == payload.get('id')
        user_info = db.users.find_one(
            {'username':username},
            {'_id' : False}
        )
        return render_template('user.html',user_info=user_info, status=status)
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
    
    
@app.route('/get_posts', methods=['GET'])
def get_posts():
    token_receive = request.cookies.get('mytoken')
    try:
        payload = jwt.decode(
            token_receive,
            SECRET_KEY,
            algorithms=['HS256']
        )
        posts = list(db.posts.find({}).sort('date', -1).limit(20))

        # Batch count komentar untuk seluruh postingan yang diambil dalam satu query agregasi
        post_ids = [str(post['_id']) for post in posts]
        comment_counts = {}
        if post_ids:
            pipeline = [
                {'$match': {'post_id': {'$in': post_ids}}},
                {'$group': {'_id': '$post_id', 'count': {'$sum': 1}}}
            ]
            for item in db.comments.aggregate(pipeline):
                comment_counts[item['_id']] = item['count']

        for post in posts:
            post['_id'] = str(post['_id'])    
            post['count_heart'] = db.likes.count_documents({
                'post_id': post['_id'],
                'type' : 'heart',
            })
            post['heart_by_me'] = bool(db.likes.find_one({
                'post_id': post['_id'],
                'type' : 'heart',
                'username' : payload.get('id')
            }))
            post['_id'] = str(post['_id'])    
            post['count_stars'] = db.stars.count_documents({
                'post_id': post['_id'],
                'type' : 'stars',
            })
            post['stars_by_me'] = bool(db.stars.find_one({
                'post_id': post['_id'],
                'type' : 'stars',
                'username' : payload.get('id')
            }))
            post['_id'] = str(post['_id'])    
            post['count_thumbs'] = db.thumbs.count_documents({
                'post_id': post['_id'],
                'type' : 'thumbs',
            })
            post['thumbs_by_me'] = bool(db.thumbs.find_one({
                'post_id': post['_id'],
                'type' : 'thumbs',
                'username' : payload.get('id')
            }))
            # Jumlah total komentar (termasuk reply dan soft-deleted)
            post['count_comments'] = comment_counts.get(post['_id'], 0)

        return jsonify({
            'result': 'success',
            'msg' : ' Successfully fetched all posts',
            'posts': posts,
        })
    except (jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError):
        return redirect(url_for('home'))    
    
    
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

if __name__ == "__main__":
    app.run('0.0.0.0', port=5000, debug=True)
