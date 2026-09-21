import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, session, url_for
from flask_babel import gettext as _
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import OperationalError
from werkzeug.security import check_password_hash

from .auth import audit, clear_gate, lock_gate, roles_required
from .models import db, AuditLog, Booking, Department, Event, Group, LoginGate, Notification, Subject, User, UserAvatar, teacher_subject, utcnow

bp = Blueprint('database_admin', __name__, url_prefix='/admin/database')


def confirm_password():
    gate = lock_gate(g.user.login)
    user = db.session.scalar(select(User).where(User.id == g.user.id).with_for_update().execution_options(populate_existing=True))
    if not user or user.role != 'admin' or not user.active or user.deleted_at or user.session_version != session.get('version'):
        abort(403)
    now = utcnow()
    if gate.permanent or (gate.locked_until and gate.locked_until > now):
        abort(403)
    password = request.form.get('password', '')
    if not password or len(password) > 128:
        raise ValueError(_('Введите текущий пароль администратора.'))
    if not check_password_hash(user.password_hash, password):
        gate.failures += 1
        gate.updated_at = now
        if gate.failures >= 15:
            gate.permanent, gate.locked_until = True, None
        elif gate.failures == 10:
            gate.locked_until = now + timedelta(minutes=5)
        audit('database_auth_failed', 'database')
        db.session.commit()
        raise ValueError(_('Пароль администратора указан неверно. Попытка учитывается в блокировке входа.'))
    clear_gate(gate)


def selection(data):
    scope = data.get('scope')
    if scope == 'all':
        return {'scope': scope}
    if scope == 'role' and data.get('role') in ('student', 'teacher', 'admin'):
        return {'scope': scope, 'role': data['role']}
    if scope in ('group', 'department'):
        try:
            value = int(data.get(scope + '_id', ''))
        except (ValueError, TypeError):
            raise ValueError(_('Выберите существующую группу или кафедру.')) from None
        model = Group if scope == 'group' else Department
        if db.session.get(model, value):
            return {'scope': scope, scope + '_id': value}
    raise ValueError(_('Выберите область очистки и значение фильтра.'))


def ids(model, condition=None):
    query = select(model.id)
    if condition is not None:
        query = query.where(condition)
    return list(db.session.scalars(query.order_by(model.id)))


def cleanup_plan(chosen):
    query = select(User.id, User.login, User.role, User.full_name, User.group_id, User.department_id).where(User.id != g.user.id)
    scope = chosen['scope']
    if scope == 'group':
        query = query.where(User.group_id == chosen['group_id'])
    elif scope == 'department':
        query = query.where(User.department_id == chosen['department_id'])
    elif scope == 'role':
        query = query.where(User.role == chosen['role'])
    users = [dict(row._mapping) for row in db.session.execute(query.order_by(User.id))]
    user_ids = [u['id'] for u in users]
    event_filter = Event.teacher_id.in_(user_ids)
    if scope == 'group':
        event_filter = or_(event_filter, Event.group_id == chosen['group_id'])
    event_ids = ids(Event, None if scope == 'all' else event_filter)
    booking_ids = ids(Booking, None if scope == 'all' else or_(Booking.student_id.in_(user_ids), Booking.event_id.in_(event_ids)))
    notification_ids = ids(Notification, None if scope == 'all' else or_(Notification.user_id.in_(user_ids), Notification.event_id.in_(event_ids)))
    avatar_query = select(UserAvatar.user_id).order_by(UserAvatar.user_id)
    if scope != 'all':
        avatar_query = avatar_query.where(UserAvatar.user_id.in_(user_ids))
    else:
        avatar_query = avatar_query.where(UserAvatar.user_id != g.user.id)
    return dict(selection=chosen, users=users, events=event_ids, bookings=booking_ids,
        notifications=notification_ids, avatars=list(db.session.scalars(avatar_query)),
        groups=ids(Group) if scope == 'all' else [], departments=ids(Department) if scope == 'all' else [],
        subjects=ids(Subject) if scope == 'all' else [])


def fingerprint(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='database-clear-v1')


def page(**kwargs):
    counts = {key: db.session.scalar(select(func.count()).select_from(model)) for key, model in
              [('users', User), ('events', Event), ('bookings', Booking), ('avatars', UserAvatar)]}
    return render_template('admin_database.html', counts=counts, language_return=url_for('database_admin.index'),
        groups=db.session.scalars(select(Group).order_by(Group.name)).all(),
        departments=db.session.scalars(select(Department).order_by(Department.name)).all(), **kwargs)


@bp.get('')
@roles_required('admin')
def index():
    return page()


@bp.post('/preview')
@roles_required('admin')
def preview():
    try:
        chosen = selection(request.form)
        plan = cleanup_plan(chosen)
        nonce = secrets.token_urlsafe(24)
        session['database_preview_nonce'] = nonce
        token = signer().dumps(dict(actor=g.user.id, version=g.user.session_version, nonce=nonce,
            selection=chosen, digest=fingerprint(plan), count=len(plan['users'])))
        return page(plan=plan, token=token, confirmation=f'DELETE {len(plan["users"])}')
    except ValueError as exc:
        flash(str(exc), 'error')
        return page(), 400


def apply_cleanup(plan):
    user_ids = [u['id'] for u in plan['users']]
    db.session.execute(delete(Notification).where(Notification.id.in_(plan['notifications'])))
    db.session.execute(delete(Booking).where(Booking.id.in_(plan['bookings'])))
    db.session.execute(delete(Event).where(Event.id.in_(plan['events'])))
    db.session.execute(delete(teacher_subject).where(teacher_subject.c.teacher_id.in_(user_ids)))
    db.session.execute(update(AuditLog).where(AuditLog.actor_id.in_(user_ids)).values(actor_id=None))
    db.session.execute(delete(User).where(User.id.in_(user_ids)))
    if plan['selection']['scope'] == 'all':
        db.session.execute(delete(teacher_subject))
        db.session.execute(update(User).where(User.id == g.user.id).values(group_id=None, department_id=None))
        db.session.execute(delete(Subject))
        db.session.execute(delete(Group))
        db.session.execute(delete(Department))
        db.session.execute(delete(LoginGate).where(LoginGate.login != g.user.login))
    else:
        logins = {u['login'] for u in plan['users']}
        db.session.execute(delete(LoginGate).where(LoginGate.login.in_(logins), ~LoginGate.login.in_(select(User.login))))
    audit('database_cleared', 'database', json.dumps(dict(selection=plan['selection'],
        counts={key:len(value) for key, value in plan.items() if isinstance(value, list)}), ensure_ascii=False))


@bp.post('/clear')
@roles_required('admin')
def clear():
    try:
        proof = signer().loads(request.form.get('token', ''), max_age=600)
        if proof.get('actor') != g.user.id or proof.get('version') != g.user.session_version or not session.get('database_preview_nonce') or proof.get('nonce') != session.get('database_preview_nonce'):
            raise ValueError(_('Подтверждение устарело. Сформируйте предварительный просмотр заново.'))
        if request.form.get('confirmation') != f'DELETE {proof["count"]}' or request.form.get('backup_saved') != 'yes':
            raise ValueError(_('Введите точную фразу подтверждения и подтвердите наличие резервной копии.'))
        # Freeze membership and linked records through validation and deletion, including workers.
        db.session.execute(text('SET LOCAL lock_timeout = \'5s\''))
        db.session.execute(text('SELECT pg_advisory_xact_lock(824271)'))
        db.session.execute(text('LOCK TABLE login_gates, users, events, bookings, notifications, user_avatars, teacher_subject, groups, departments, subjects, audit_log, site_settings IN EXCLUSIVE MODE'))
        plan = cleanup_plan(selection(proof['selection']))
        if fingerprint(plan) != proof['digest']:
            raise ValueError(_('Данные изменились после предварительного просмотра. Проверьте состав очистки заново.'))
        confirm_password()
        apply_cleanup(plan)
        db.session.commit()
        session.pop('database_preview_nonce', None)
        flash(_('Очистка выполнена. Текущий администратор и история действий сохранены.'), 'success')
        return redirect(url_for('database_admin.index'))
    except (BadSignature, SignatureExpired):
        db.session.rollback()
        flash(_('Подтверждение устарело. Сформируйте предварительный просмотр заново.'), 'error')
        return page(), 400
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
        return page(), 400

    except OperationalError as exc:
        db.session.rollback()
        if getattr(exc.orig, 'sqlstate', None) not in ('55P03', '40P01'):
            raise
        flash(_('База данных занята другой операцией. Сформируйте предварительный просмотр и повторите позже.'), 'error')
        return page(), 409


def database_dump():
    binary = current_app.config.get('PG_DUMP_PATH') or os.environ.get('PG_DUMP_PATH') or shutil.which('pg_dump')
    if not binary:
        raise ValueError(_('Утилита pg_dump не установлена на сервере. Обратитесь к администратору сервера.'))
    url = db.engine.url
    env = {key:value for key, value in os.environ.items() if not key.startswith('PG')}
    env.update(PGHOST=url.host or 'localhost', PGPORT=str(url.port or 5432), PGDATABASE=url.database or '',
        PGUSER=url.username or '', PGPASSWORD=url.password or '', PGCONNECT_TIMEOUT='10',
        PGSSLMODE=str(url.query.get('sslmode', 'prefer')))
    output = tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024)
    try:
        result = subprocess.run([binary, '--no-password', '--format=custom', '--no-owner', '--no-acl'],
            stdout=output, stderr=subprocess.PIPE, env=env, timeout=120, check=False)
        size = output.tell()
        if result.returncode or not size or size > 200 * 1024 * 1024:
            raise ValueError(_('Не удалось подготовить резервную копию. Проверьте версию pg_dump, подключение к БД и размер выгрузки (до 200 МБ).'))
        output.seek(0)
        return output
    except (subprocess.TimeoutExpired, OSError):
        output.close()
        raise ValueError(_('Сервер не смог завершить выгрузку БД. Повторите позже или обратитесь к администратору сервера.')) from None
    except Exception:
        output.close()
        raise


@bp.post('/export')
@roles_required('admin')
def export():
    try:
        confirm_password()
        db.session.commit()
        output = database_dump()
        try:
            audit('database_exported', 'database', 'PostgreSQL custom-format backup')
            db.session.commit()
            response = send_file(output, mimetype='application/octet-stream', as_attachment=True,
                download_name=f'consultations-{utcnow():%Y%m%d-%H%M%S}.dump', max_age=0)
            response.call_on_close(output.close)
            return response
        except Exception:
            output.close()
            raise
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
        return page(), 400
