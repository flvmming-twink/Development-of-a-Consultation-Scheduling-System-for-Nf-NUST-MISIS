from flask_babel import gettext as _, lazy_gettext as _l
import re
from datetime import timedelta
from functools import wraps
from zoneinfo import ZoneInfo
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from werkzeug.security import generate_password_hash, check_password_hash
from .models import db, User, Group, LoginGate, AuditLog, utcnow

bp = Blueprint('auth', __name__)
_dummy = generate_password_hash('not-an-account-password')

def normalize_login(value):
    login = value.strip().lower()
    suffix = '@' + current_app.config['CORPORATE_DOMAIN'].lower()
    if login.endswith(suffix):
        login = login[:-len(suffix)]
    return login

def hash_password(password):
    return generate_password_hash(password, method=current_app.config.get('PASSWORD_HASH_METHOD', 'scrypt:32768:8:1'))

def audit(action, entity, details='', actor_id=None):
    if actor_id is None and getattr(g, 'user', None):
        actor_id = g.user.id
    db.session.add(AuditLog(actor_id=actor_id, action=action, entity=str(entity), details=details))

def lock_gate(login):
    db.session.execute(insert(LoginGate).values(login=login, failures=0, permanent=False, updated_at=utcnow()).on_conflict_do_nothing())
    return db.session.execute(select(LoginGate).where(LoginGate.login == login).with_for_update()).scalar_one()

def clear_gate(gate):
    gate.failures = 0
    gate.locked_until = None
    gate.permanent = False
    gate.updated_at = utcnow()

def roles_required(*roles):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not g.user:
                return redirect(url_for('auth.login'))
            if roles and g.user.role not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return wrapped
    return decorate

def validate_name(value):
    name = ' '.join(value.split())
    if not 3 <= len(name) <= 160 or len(name.split()) < 2 or not all(c.isalpha() or c in " -'’" for c in name):
        raise ValueError(_('Введите фамилию, имя и отчество при наличии, без цифр.'))
    return name


def name_parts(data):
    if not any(key in data for key in ('last_name', 'first_name', 'middle_name')):
        return (validate_name(data.get('full_name', '')).split(maxsplit=2) + [''])[:3]
    parts = [' '.join(data.get(key, '').split()) for key in ('last_name', 'first_name', 'middle_name')]
    if not parts[0] or not parts[1] or any(len(part) > 80 or not all(c.isalpha() or c in " -'’" for c in part) for part in parts):
        raise ValueError(_('Укажите фамилию и имя. Отчество необязательно. В каждом поле допустимы только буквы, пробелы, дефис и апостроф, не более 80 символов.'))
    validate_name(' '.join(filter(None, parts)))
    return parts

def check_password_distinct(user, password):
    others = db.session.scalars(select(User).where(User.login == user.login, User.id != user.id)).all()
    if any(check_password_hash(u.password_hash, password) or check_password_hash(u.initial_password_hash, password) for u in others):
        raise ValueError(_('Этот пароль занят другой ролью с таким логином. Выберите другой.'))

@bp.route('/login', methods=['GET','POST'])
def login():
    if g.user:
        return redirect(url_for('main.home'))
    status = 200
    entered = ''
    if request.method == 'POST':
        entered = request.form.get('login', '')
        password = request.form.get('password', '')
        canonical = normalize_login(entered)
        if not canonical or not password:
            flash(_('Заполните логин и пароль. Пустые поля не считаются попыткой.'), 'error')
            status = 400
        elif len(canonical) > 120 or len(password) > 128:
            flash(_('Слишком длинный логин или пароль.'), 'error')
            status = 400
        else:
            gate = lock_gate(canonical)
            now = utcnow()
            if gate.permanent:
                flash(_('Вход заблокирован. Обратитесь к администратору для сброса пароля.'), 'error')
                status = 423
            elif gate.locked_until and gate.locked_until > now:
                seconds = int((gate.locked_until - now).total_seconds()) + 1
                flash(_('Вход временно заблокирован. Повторите через %(value0)s сек.', value0=seconds), 'error')
                status = 429
            else:
                candidates = db.session.scalars(select(User).where(User.login == canonical).order_by(User.id)).all()
                matched = [u for u in candidates if check_password_hash(u.password_hash, password)]
                for attempt in range(max(0, 2 - len(candidates))):
                    check_password_hash(_dummy, password)
                valid = matched[0] if len(matched) == 1 and matched[0].active and not matched[0].deleted_at else None
                if valid:
                    clear_gate(gate)
                    language = session.get('language')
                    if language in ('ru', 'en'):
                        valid.language = language
                    session.clear()
                    session.update(uid=valid.id, version=valid.session_version)
                    session.permanent = True
                    audit('login_success', canonical, valid.role, actor_id=valid.id)
                    db.session.commit()
                    return redirect(url_for('main.home'))
                gate.failures += 1
                gate.updated_at = now
                if gate.failures >= 15:
                    gate.permanent = True
                    gate.locked_until = None
                    flash(_('Вход заблокирован. Обратитесь к администратору для сброса пароля.'), 'error')
                    audit('login_permanent_lock', canonical)
                    status = 423
                elif gate.failures == 10:
                    gate.locked_until = now + timedelta(minutes=5)
                    flash(_('10 неудачных попыток. Вход заблокирован на 5 минут.'), 'error')
                    audit('login_temporary_lock', canonical)
                    status = 429
                else:
                    flash(_('Неверный логин или пароль.'), 'error')
                    status = 401
            db.session.commit()
    return render_template('login.html', entered=entered), status

@bp.post('/logout')
def logout():
    from .i18n import current_language
    language = current_language()
    session.clear()
    session['language'] = language
    return redirect(url_for('auth.login'))

@bp.route('/settings', methods=['GET','POST'])
@roles_required()
def settings():
    if request.method == 'POST':
        try:
            gate = lock_gate(g.user.login)
            user = db.session.execute(select(User).where(User.id == g.user.id).with_for_update().execution_options(populate_existing=True)).scalar_one()
            action = request.form.get('action')
            if action == 'theme':
                theme = request.form.get('theme')
                if theme not in ('light','dark'):
                    raise ValueError(_('Выберите светлую или тёмную тему.'))
                user.theme = theme
            elif action in ('name', 'profile'):
                if user.role != 'student' or user.name_locked:
                    abort(403)
                if request.form.get('confirm_name') != 'yes':
                    raise ValueError(_('Подтвердите правильность ФИО и группы.'))
                group_id = request.form.get('group_id')
                group = user.group
                if not group:
                    group = db.session.get(Group, int(group_id)) if group_id and group_id.isdigit() else None
                    if not group or not group.active or not group.entry_year:
                        raise ValueError(_('Выберите действующую группу с указанным годом поступления.'))
                    today = utcnow().astimezone(ZoneInfo(current_app.config['APP_TIMEZONE'])).date()
                    academic_year = today.year - (today.month < 9)
                    course = academic_year - group.entry_year + 1
                    if not 1 <= course <= 4:
                        raise ValueError(_('Выбранная группа не относится к 1–4 курсу очного обучения. Обратитесь к администратору.'))
                    user.group = group
                    user.course = course
                    user.study_mode = 'full_time'
                user.set_name_parts(*name_parts(request.form))
                user.name_locked = True
                audit('student_name_set', user.id, f'{user.full_name}; group={group.name}')
            elif action == 'language':
                language = request.form.get('language')
                if language not in ('ru', 'en'):
                    abort(400)
                owner = g.admin_user or user
                owner.language = language
                session['language'] = language
                from flask_babel import refresh
                refresh()
            elif action == 'password':
                if not check_password_hash(user.password_hash, request.form.get('current_password','')):
                    raise ValueError(_('Текущий пароль указан неверно.'))
                password = request.form.get('new_password','')
                if not 8 <= len(password) <= 128 or password != request.form.get('repeat_password'):
                    raise ValueError(_('Пароль должен содержать 8–128 символов. Повтор пароля должен совпадать.'))
                if check_password_hash(user.initial_password_hash, password):
                    raise ValueError(_('Новый пароль должен отличаться от начального.'))
                check_password_distinct(user, password)
                user.password_hash = hash_password(password)
                user.must_change_password = False
                user.session_version += 1
                session['version'] = user.session_version
                clear_gate(gate)
                audit('password_changed', user.id)
            else:
                abort(400)
            db.session.commit()
            flash(_('Настройки сохранены.'), 'success')
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
        return redirect(url_for('auth.settings'))
    groups = db.session.scalars(select(Group).where(Group.active).order_by(Group.name)).all() if g.user.role == 'student' else []
    return render_template('settings.html', groups=groups)
