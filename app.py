
import os
import hashlib
import jwt

from datetime import datetime, timedelta

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

SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY belum diatur di file .env"
    )

app.config["SECRET_KEY"] = SECRET_KEY

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
    except jwt.exceptions.DecodeError:
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
    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
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
            # the token will be valid for 24 hours
            "exp": datetime.utcnow() + timedelta(seconds=60 * 60 * 24),
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")

        return jsonify(
            {
                "result": "success",
                "token": token,
            }
        )
    # Let's also handle the case where the id and
    # password combination cannot be found
    else:
        return jsonify(
            {
                "result": "fail",
                "msg": "We could not find a user with that id/password combination",
            }
        )

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
    
    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
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
    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
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
        return jsonify({
            'result': 'success',
            'msg' : ' Successfully fetched all posts',
            'posts': posts,
        })
    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
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
           
               
        
    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
        return redirect(url_for('home'))    
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
    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
        return redirect(url_for('home'))  

if __name__ == "__main__":
    app.run('0.0.0.0', port=5000, debug=True)