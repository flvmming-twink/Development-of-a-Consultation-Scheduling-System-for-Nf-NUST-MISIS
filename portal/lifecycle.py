from flask_babel import gettext as _, lazy_gettext as _l
from datetime import timedelta
from sqlalchemy import select, delete, update, text
from .models import db, User, Event, Booking, LoginGate, AuditLog, teacher_subject, utcnow
from .auth import audit
from .notifications import notify, admins, participants

def trash_users(user_ids, actor):
    # Serialize bulk lifecycle changes and lock in ID order to avoid partial batches.
    db.session.execute(text('SELECT pg_advisory_xact_lock(824271)'))
    actor = db.session.scalar(select(User).where(User.id == actor.id).execution_options(populate_existing=True))
    if not actor or not actor.active or actor.deleted_at or actor.role != 'admin':
        raise ValueError(_('Доступ администратора изменён. Войдите в систему заново.'))
    users = db.session.scalars(select(User).where(User.id.in_(user_ids)).order_by(User.id).with_for_update().execution_options(populate_existing=True)).all()
    if not users or len(users) != len(set(user_ids)):
        raise ValueError(_('Выберите существующих пользователей.'))
    for user in users:
        if user.id == actor.id:
            raise ValueError(_('Нельзя удалить собственную учётную запись.'))
        if user.deleted_at:
            raise ValueError(_('%(value0)s: пользователь уже в корзине.', value0=user.login))
        if user.role == 'teacher' and not user.dismissed_on:
            raise ValueError(_('%(value0)s: сначала укажите дату увольнения преподавателя.', value0=user.login))
    from .services import cancel_booking, cancel_event
    now = utcnow()
    for user in users:
        if user.role == 'teacher':
            for event_id in db.session.scalars(select(Event.id).where(Event.teacher_id == user.id, Event.status == 'active', Event.ends_at > now)).all():
                cancel_event(event_id, actor, 'Преподаватель уволен; учётная запись перенесена в корзину')
        if user.role == 'student':
            for booking_id in db.session.scalars(select(Booking.id).where(Booking.student_id == user.id, Booking.status == 'active', Booking.ends_at > now)).all():
                cancel_booking(booking_id, actor, 'Студент перенесён в корзину')
        user.active_before_delete = user.active
        user.active = False
        user.deleted_at = now
        user.session_version += 1
        audit('user_trashed', user.id, 'Срок восстановления: 10 дней')
    notify(admins(), _l('Пользователи в корзине'), _l('Перенесено учётных записей: %(value0)s. Срок восстановления: 10 дней.', value0=len(users)), link='/admin/trash')
    return len(users)

def restore_users(user_ids):
    db.session.execute(text('SELECT pg_advisory_xact_lock(824271)'))
    users = db.session.scalars(select(User).where(User.id.in_(user_ids)).order_by(User.id).with_for_update().execution_options(populate_existing=True)).all()
    now = utcnow()
    if not users or len(users) != len(set(user_ids)):
        raise ValueError(_('Выберите существующих пользователей.'))
    if any(not u.deleted_at or u.purge_at <= now for u in users):
        raise ValueError(_('Срок восстановления истёк или пользователь уже восстановлен.'))
    for user in users:
        user.deleted_at = None
        user.active = bool(user.active_before_delete) and not user.dismissed_on
        user.active_before_delete = None
        user.session_version += 1
        audit('user_restored', user.id)
    notify(admins(), _l('Пользователи восстановлены'), _l('Восстановлено учётных записей: %(value0)s. Отменённые записи и занятия остаются отменёнными.', value0=len(users)), link='/admin/users')
    return len(users)

def purge_expired(now):
    users = db.session.scalars(select(User).where(User.deleted_at <= now - timedelta(days=10)).order_by(User.id).with_for_update()).all()
    for user in users:
        uid, login = user.id, user.login
        db.session.execute(delete(teacher_subject).where(teacher_subject.c.teacher_id == uid))
        db.session.execute(update(AuditLog).where(AuditLog.actor_id == uid).values(actor_id=None))
        # Null FK references retain attendance and consultation history without an account.
        db.session.execute(delete(User).where(User.id == uid))
        if not db.session.scalar(select(User.id).where(User.login == login).limit(1)):
            db.session.execute(delete(LoginGate).where(LoginGate.login == login))
        audit('user_purged', uid, 'Автоматическая очистка корзины через 10 дней')
    if users:
        notify(admins(), _l('Корзина очищена'), _l('Безвозвратно удалено учётных записей: %(value0)s.', value0=len(users)), link='/admin/trash')
    return len(users)

def deliver_scheduled(now):
    from flask import current_app
    lead = timedelta(hours=current_app.config['BOOKING_LEAD_HOURS'])
    events = db.session.scalars(select(Event).where(Event.status == 'active', Event.ends_at > now)).all()
    students = db.session.scalars(select(User).where(User.role == 'student', User.active, User.deleted_at.is_(None))).all()
    admin_ids = admins()
    for event in events:
        if not event.teacher or not event.teacher.active or event.teacher.deleted_at or not event.subject.active:
            continue
        if event.registration_opens_at <= now <= event.starts_at - lead:
            recipients = [u.id for u in students if not u.academic_status(now)[1] and u.academic_status(now)[0] == event.allowed_course and (not event.group_id or event.group_id == u.group_id)]
            key = f'event:{event.id}:open:{event.registration_opens_at.isoformat()}'
            body = _l('%(value0)s. Аудитория: %(value1)s.', value0=event.subject.name, value1=event.room)
            notify(recipients, _l('Открыта запись на консультацию'), body, key=key, event=event, link='/events')
            notify([event.teacher_id, *admin_ids], _l('Открыта запись на консультацию'), body, key=key, event=event)
        if event.starts_at - timedelta(hours=1) <= now < event.starts_at:
            notify(participants(event) + admin_ids, _l('Консультация начнётся в течение часа'), _l('%(value0)s. Аудитория: %(value1)s.', value0=event.subject.name, value1=event.room), key=f'event:{event.id}:reminder:{event.starts_at.isoformat()}', event=event)
        if event.starts_at <= now < event.ends_at:
            notify(participants(event) + admin_ids, _l('Консультация началась'), _l('%(value0)s. Аудитория: %(value1)s.', value0=event.subject.name, value1=event.room), key=f'event:{event.id}:started:{event.starts_at.isoformat()}', event=event)

def run_maintenance(now=None):
    now = now or utcnow()
    if not db.session.scalar(text('SELECT pg_try_advisory_xact_lock(824271)')):
        db.session.rollback()
        return
    purged = purge_expired(now)
    for user in db.session.scalars(select(User).where(User.role == 'student', User.deleted_at.is_(None))).all():
        course, graduated = user.academic_status(now)
        user.course = course
        if graduated:
            notify(admins(), _l('Студент завершил обучение'), _l('%(value0)s · %(value1)s. Учётная запись отмечена к удалению.', value0=user.login, value1=user.group.name), key=f'graduate:{user.id}:{user.group.entry_year}', link=f'/admin/users/{user.id}')
    deliver_scheduled(now)
    db.session.commit()
    return purged
