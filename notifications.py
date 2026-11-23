from datetime import datetime, timezone
from bson import ObjectId
from database import db


def create_notification(
    recipient: str,
    actor: str,
    notif_type: str,
    post_id: str = None,
    comment_id: str = None,
    action_type: str = None,
    message: str = None
):
    """
    Membuat dokumen notifikasi baru di db.notifications secara aman dan server-side.
    - Mencegah notifikasi untuk diri sendiri (recipient == actor)
    - Mencegah duplikasi notifikasi unread
    """
    if not recipient or not actor:
        return None

    recipient = str(recipient).strip()
    actor = str(actor).strip()

    # Cegah self-notification
    if recipient == actor:
        return None

    notif_type = str(notif_type).strip().lower()
    post_id = str(post_id).strip() if post_id else None
    comment_id = str(comment_id).strip() if comment_id else None
    action_type = str(action_type).strip().lower() if action_type else None

    # 1. Deduplikasi Follow: jika notifikasi follow yang belum dibaca sudah ada dari actor yang sama
    if notif_type == 'follow':
        existing = db.notifications.find_one({
            'recipient': recipient,
            'actor': actor,
            'type': 'follow',
            'is_read': False
        })
        if existing:
            return str(existing['_id'])

    # 2. Deduplikasi Reaksi: jika notifikasi reaksi unread dari actor pada post_id ini sudah ada
    elif notif_type == 'reaction':
        existing = db.notifications.find_one({
            'recipient': recipient,
            'actor': actor,
            'type': 'reaction',
            'post_id': post_id,
            'is_read': False
        })
        if existing:
            return str(existing['_id'])

    # 3. Deduplikasi Komentar / Balasan: jika notifikasi untuk comment_id ini sudah pernah dibuat
    elif notif_type in ['comment', 'reply'] and comment_id:
        existing = db.notifications.find_one({
            'recipient': recipient,
            'actor': actor,
            'comment_id': comment_id
        })
        if existing:
            return str(existing['_id'])

    # 4. Deduplikasi Repost: jika notifikasi repost unread dari actor pada post_id ini sudah ada
    elif notif_type == 'repost':
        existing = db.notifications.find_one({
            'recipient': recipient,
            'actor': actor,
            'type': 'repost',
            'post_id': post_id,
            'is_read': False
        })
        if existing:
            return str(existing['_id'])

    # 5. Deduplikasi Quote: jika notifikasi quote unread dari actor pada post_id ini sudah ada
    elif notif_type == 'quote':
        existing = db.notifications.find_one({
            'recipient': recipient,
            'actor': actor,
            'type': 'quote',
            'post_id': post_id,
            'is_read': False
        })
        if existing:
            return str(existing['_id'])

    # Generate pesan default jika tidak disertakan
    if not message:
        if notif_type == 'follow':
            message = "mulai mengikuti Anda."
        elif notif_type == 'reaction':
            reaction_label = 'reaksi'
            if action_type in ['heart', 'like']:
                reaction_label = 'hati (like)'
            elif action_type == 'stars':
                reaction_label = 'bintang'
            elif action_type == 'thumbs':
                reaction_label = 'jempol'
            message = f"memberikan {reaction_label} pada postingan Anda."
        elif notif_type == 'comment':
            message = "mengomentari postingan Anda."
        elif notif_type == 'reply':
            message = "membalas komentar Anda."
        elif notif_type == 'repost':
            message = "merepost postingan Anda."
        elif notif_type == 'quote':
            message = "mengutip postingan Anda."
        else:
            message = "berinteraksi dengan akun Anda."

    now_iso = datetime.now(timezone.utc).isoformat()
    doc = {
        'recipient': recipient,
        'actor': actor,
        'type': notif_type,
        'action_type': action_type,
        'post_id': post_id,
        'comment_id': comment_id,
        'message': message,
        'created_at': now_iso,
        'is_read': False
    }

    res = db.notifications.insert_one(doc)
    return str(res.inserted_id)


def get_unread_count(username: str) -> int:
    """Menghitung jumlah notifikasi yang belum dibaca untuk seorang pengguna."""
    if not username:
        return 0
    return db.notifications.count_documents({
        'recipient': username,
        'is_read': False
    })


def get_user_notifications(username: str, cursor: str = None, limit: int = 20):
    """
    Mengambil daftar notifikasi pengguna dengan keyset cursor pagination,
    diperkaya dengan profil actor dan status ketersediaan post.
    """
    if not username:
        return {'notifications': [], 'next_cursor': None, 'has_more': False, 'unread_count': 0}

    try:
        limit = int(limit)
        if limit < 1 or limit > 50:
            limit = 20
    except (TypeError, ValueError):
        limit = 20

    query = {'recipient': username}
    if cursor and ObjectId.is_valid(cursor):
        anchor = db.notifications.find_one({'_id': ObjectId(cursor)})
        if anchor:
            query['$or'] = [
                {'created_at': {'$lt': anchor.get('created_at', '')}},
                {'created_at': anchor.get('created_at', ''), '_id': {'$lt': ObjectId(cursor)}}
            ]

    notif_docs = list(db.notifications.find(query).sort([('created_at', -1), ('_id', -1)]).limit(limit))

    # Kumpulkan distinct actors untuk batch query efisien
    actor_usernames = list({n['actor'] for n in notif_docs if n.get('actor')})
    actors_map = {}
    if actor_usernames:
        user_cursor = db.users.find({'username': {'$in': actor_usernames}}, {'password': 0})
        actors_map = {u['username']: u for u in user_cursor}

    # Kumpulkan distinct post_ids untuk verifikasi ketersediaan postingan
    post_ids = [ObjectId(n['post_id']) for n in notif_docs if n.get('post_id') and ObjectId.is_valid(n['post_id'])]
    posts_map = {}
    if post_ids:
        post_cursor = db.posts.find({'_id': {'$in': post_ids}}, {'comment': 1})
        posts_map = {str(p['_id']): p for p in post_cursor}

    enriched_notifs = []
    for n in notif_docs:
        nid = str(n['_id'])
        actor_data = actors_map.get(n.get('actor'), {})
        post_id_str = n.get('post_id')
        post_doc = posts_map.get(post_id_str) if post_id_str else None

        post_snippet = None
        if post_doc and 'comment' in post_doc:
            full_comment = post_doc['comment']
            post_snippet = full_comment[:60] + ('...' if len(full_comment) > 60 else '')

        enriched_notifs.append({
            '_id': nid,
            'recipient': n.get('recipient'),
            'actor': n.get('actor'),
            'type': n.get('type'),
            'action_type': n.get('action_type'),
            'post_id': post_id_str,
            'post_exists': (post_doc is not None) if post_id_str else True,
            'post_snippet': post_snippet,
            'comment_id': n.get('comment_id'),
            'message': n.get('message', ''),
            'created_at': n.get('created_at', ''),
            'is_read': bool(n.get('is_read', False)),
            'actor_info': {
                'username': n.get('actor'),
                'profile_name': actor_data.get('profile_name', n.get('actor')),
                'profile_pic_real': actor_data.get('profile_pic_real', 'profile_pics/profile_placeholder.png')
            }
        })

    has_more = (len(notif_docs) == limit)
    next_cursor = str(notif_docs[-1]['_id']) if has_more else None
    unread_count = get_unread_count(username)

    return {
        'notifications': enriched_notifs,
        'next_cursor': next_cursor,
        'has_more': has_more,
        'unread_count': unread_count
    }


def mark_notification_as_read(notification_id: str, username: str):
    """
    Menandai satu notifikasi sebagai telah dibaca.
    Memeriksa validitas ID dan hak akses kepemilikan (recipient == username).
    """
    if not notification_id or not ObjectId.is_valid(notification_id):
        return {'status': 'invalid_id', 'code': 400, 'msg': 'Invalid notification ID format'}

    notif = db.notifications.find_one({'_id': ObjectId(notification_id)})
    if not notif:
        return {'status': 'not_found', 'code': 404, 'msg': 'Notification not found'}

    if notif.get('recipient') != username:
        return {'status': 'forbidden', 'code': 403, 'msg': 'You do not have permission to modify this notification'}

    if not notif.get('is_read', False):
        db.notifications.update_one({'_id': ObjectId(notification_id)}, {'$set': {'is_read': True}})

    unread_count = get_unread_count(username)
    return {'status': 'success', 'code': 200, 'msg': 'Notification marked as read', 'unread_count': unread_count}


def mark_all_notifications_as_read(username: str):
    """Menandai seluruh notifikasi milik pengguna tertentu sebagai telah dibaca."""
    if not username:
        return {'status': 'fail', 'code': 400, 'msg': 'Username required'}

    result = db.notifications.update_many(
        {'recipient': username, 'is_read': False},
        {'$set': {'is_read': True}}
    )
    return {
        'status': 'success',
        'code': 200,
        'msg': 'All notifications marked as read',
        'modified_count': result.modified_count,
        'unread_count': 0
    }


def cleanup_notifications_for_post(post_id: str):
    """Membersihkan seluruh notifikasi yang terkait dengan postingan yang dihapus."""
    if not post_id:
        return 0
    result = db.notifications.delete_many({'post_id': str(post_id)})
    return result.deleted_count
