from uuid import uuid4
from zoneinfo import ZoneInfo
from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for
from sqlalchemy import select, func, update
from sqlalchemy.dialects.postgresql import insert
from .models import db, User, Notification, Booking, utcnow
from .auth import roles_required
from flask_babel import force_locale, gettext as _

bp = Blueprint('notifications', __name__, url_prefix='/notifications')

def notify(user_ids, title, body, key=None, event=None, link=''):
    ids = set(uid for uid in user_ids if uid is not None)
    if not ids:
        return
    key = key or uuid4().hex
    messages = {}
    for locale in ('ru', 'en'):
        with force_locale(locale):
            message = str(body)
            if event:
                local_start = event.starts_at.astimezone(ZoneInfo(current_app.config['APP_TIMEZONE']))
                message = _('%(body)s Начало: %(time)s (Екатеринбург).', body=message, time=local_start.strftime('%d.%m.%Y %H:%M'))
            messages[locale] = message
    with force_locale('ru'):
        title = str(title)
    users = db.session.scalars(select(User).where(User.id.in_(ids), User.active, User.deleted_at.is_(None))).all()
    for user in users:
        target = link
        if event and not target:
            target = '/bookings' if user.role == 'student' else f'/events/{event.id}'
        db.session.execute(insert(Notification).values(user_id=user.id, event_id=event.id if event else None,
            key=key, title=title, body=messages['ru'], body_en=messages['en'], link=target, created_at=utcnow()).on_conflict_do_nothing(constraint='uq_notification_user_key'))

def admins():
    return db.session.scalars(select(User.id).where(User.role == 'admin', User.active, User.deleted_at.is_(None))).all()

def participants(event):
    return [event.teacher_id, *db.session.scalars(select(Booking.student_id).where(Booking.event_id == event.id, Booking.status == 'active')).all()]

@bp.get('')
@roles_required()
def inbox():
    query = select(Notification).where(Notification.user_id == g.user.id)
    if request.args.get('unread') == '1':
        query = query.where(Notification.read_at.is_(None))
    return render_template('notifications.html', page=db.paginate(query.order_by(Notification.id.desc()), per_page=30, error_out=False))

@bp.get('/count')
@roles_required()
def count():
    return {'unread': db.session.scalar(select(func.count(Notification.id)).where(Notification.user_id == g.user.id, Notification.read_at.is_(None)))}

@bp.post('/read-all')
@roles_required()
def read_all():
    db.session.execute(update(Notification).where(Notification.user_id == g.user.id, Notification.read_at.is_(None)).values(read_at=utcnow()))
    db.session.commit()
    return redirect(url_for('notifications.inbox'))

@bp.post('/<int:notification_id>/read')
@roles_required()
def read(notification_id):
    item = db.get_or_404(Notification, notification_id)
    if item.user_id != g.user.id:
        abort(404)
    item.read_at = item.read_at or utcnow()
    db.session.commit()
    return redirect(item.link if item.link.startswith('/') and not item.link.startswith('//') else url_for('notifications.inbox'))
