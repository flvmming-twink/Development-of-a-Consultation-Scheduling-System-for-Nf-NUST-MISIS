import os
from datetime import timedelta
from zoneinfo import ZoneInfo
from flask import Flask, g, render_template, request, session, redirect, url_for, flash
from flask_wtf.csrf import CSRFProtect, CSRFError
from sqlalchemy.exc import IntegrityError
from .models import db, User, LoginGate, Notification, SiteSettings, utcnow
from flask_babel import gettext as _
from sqlalchemy import select, func

csrf = CSRFProtect()

def database_url():
    url = os.getenv('DATABASE_URL')
    if url and url.startswith('postgresql://'):
        return 'postgresql+psycopg://' + url.removeprefix('postgresql://')
    if url and url.startswith('postgres://'):
        return 'postgresql+psycopg://' + url.removeprefix('postgres://')
    return url

def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv('SECRET_KEY'),
        SQLALCHEMY_DATABASE_URI=database_url(),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={'pool_pre_ping': True},
        APP_TIMEZONE=os.getenv('APP_TIMEZONE', 'Asia/Yekaterinburg'),
        CORPORATE_DOMAIN=os.getenv('CORPORATE_DOMAIN', 'misis.ru'),
        BOOKING_LEAD_HOURS=int(os.getenv('BOOKING_LEAD_HOURS', '48')),
        FORCE_INITIAL_PASSWORD_CHANGE=os.getenv('FORCE_INITIAL_PASSWORD_CHANGE', '0') == '1',
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE', '0') == '1',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=1024 * 1024,
        MAX_FORM_MEMORY_SIZE=256 * 1024,
        AVATAR_MAX_BYTES=5 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config['SECRET_KEY'] or app.config['SECRET_KEY'] == 'CHANGE_ME':
        raise RuntimeError('Сначала запустите python scripts/configure.py и настройте .env.')
    if not str(app.config['SQLALCHEMY_DATABASE_URI']).startswith('postgresql'):
        raise RuntimeError('DATABASE_URL должен указывать на PostgreSQL.')
    db.init_app(app)
    @app.before_request
    def upload_limit():
        if request.endpoint == 'avatars.settings':
            request.max_content_length = app.config['AVATAR_MAX_BYTES'] + 64 * 1024

    csrf.init_app(app)
    from .i18n import babel, bp as language_bp, current_language
    babel.init_app(app, default_locale='ru', locale_selector=current_language)
    app.register_blueprint(language_bp)
    app.jinja_env.globals['int'] = int
    from .auth import bp as auth_bp
    from .views import bp as views_bp
    from .admin import bp as admin_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(views_bp)
    app.register_blueprint(admin_bp)
    from .avatars import bp as avatars_bp
    from .database_admin import bp as database_bp
    app.register_blueprint(avatars_bp)
    app.register_blueprint(database_bp)
    from .notifications import bp as notifications_bp
    app.register_blueprint(notifications_bp)
    from .cli import register_commands
    register_commands(app)

    @app.before_request
    def load_user():
        g.user = None
        g.admin_user = None
        g.maintenance = False
        if request.endpoint == 'static':
            return
        admin_uid = session.get('admin_uid')
        if admin_uid:
            admin = db.session.get(User, admin_uid)
            admin_gate = db.session.get(LoginGate, admin.login) if admin else None
            admin_blocked = admin_gate and (admin_gate.permanent or (admin_gate.locked_until and admin_gate.locked_until > utcnow()))
            if not admin or admin.role != 'admin' or not admin.active or admin.deleted_at or admin.session_version != session.get('admin_version') or admin_blocked:
                session.clear()
                return
            g.admin_user = admin
        uid = session.get('uid')
        if uid:
            user = db.session.get(User, uid)
            gate = db.session.get(LoginGate, user.login) if user else None
            blocked = gate and (gate.permanent or (gate.locked_until and gate.locked_until > utcnow()))
            if not user or not user.active or user.deleted_at or user.session_version != session.get('version') or blocked:
                session.clear()
            else:
                g.user = user
        if g.user:
            settings = db.session.get(SiteSettings, 1)
            g.maintenance = bool(settings and settings.maintenance)
        if g.maintenance and g.user.role != 'admin' and not g.admin_user and request.endpoint not in ('auth.logout', 'language.change', 'main.service_status', 'main.health'):
            if request.endpoint == 'notifications.count':
                return {'maintenance': True}, 503, {'Retry-After': '60'}
            return render_template('maintenance.html'), 503, {'Retry-After': '60'}
        if g.user and g.user.must_change_password and request.endpoint not in ('auth.settings','auth.logout','static','language.change','main.service_status','admin.stop_impersonation','avatars.image'):
            flash(_('Установите собственный пароль, чтобы продолжить.'), 'info')
            return redirect(url_for('auth.settings'))

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; font-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        if request.endpoint != 'static':
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.template_filter('localtime')
    def localtime(value, fmt='%d.%m.%Y %H:%M'):
        return value.astimezone(ZoneInfo(app.config['APP_TIMEZONE'])).strftime(fmt) if value else '—'

    @app.context_processor
    def common():
        unread = db.session.scalar(select(func.count(Notification.id)).where(Notification.user_id == g.user.id, Notification.read_at.is_(None))) if getattr(g, 'user', None) else 0
        return {'now': utcnow(), 'language': current_language(), 'unread_notifications': unread, 'roles': {'student':_('Студент'), 'teacher':_('Преподаватель'), 'admin':_('Администратор')},
                'statuses': {'active':_('Активна'),'cancelled':_('Отменена'),'pending':_('Не отмечено'),'present':_('Присутствовал'),'absent':_('Не явился')},
                'lead_hours': app.config['BOOKING_LEAD_HOURS'], 'timezone_name': app.config['APP_TIMEZONE']}

    @app.template_filter('action_label')
    def action_label(value):
        labels = {
            'user_created':'Создан пользователь', 'user_updated':'Изменены данные пользователя',
            'user_activated':'Аккаунт включён', 'user_deactivated':'Аккаунт отключён',
            'name_corrected':'Исправлено ФИО', 'student_name_set':'Студент указал ФИО',
            'password_changed':'Пользователь сменил пароль', 'password_reset':'Сброшен пароль',
            'admin_console_recovery':'Администратор восстановлен через консоль',
            'login_success':'Успешный вход', 'login_temporary_lock':'Блокировка на 5 минут',
            'login_permanent_lock':'Блокировка до обращения', 'catalog_updated':'Обновлён справочник',
            'assignment_added':'Назначена дисциплина', 'assignment_removed':'Снята дисциплина',
            'admin_impersonation_started':'Администратор открыл сценарий роли',
            'admin_impersonation_stopped':'Администратор вернулся в панель',
            'event_created':'Создано занятие', 'event_updated':'Изменено занятие',
            'event_cancelled':'Отменено занятие', 'booking_created':'Студент записался',
            'booking_cancelled':'Запись отменена', 'attendance_changed':'Отмечена посещаемость',
            'user_deleted':'Пользователь удалён',
            'user_trashed':'Пользователь перенесён в корзину', 'user_restored':'Пользователь восстановлен',
            'user_purged':'Пользователь удалён после 10 дней в корзине',
            'journal_exported':'Выгружен журнал', 'students_imported':'Импортированы студенты',
            'demo_seeded':'Добавлены демонстрационные данные',
            'maintenance_enabled':'Включено техническое обслуживание',
            'maintenance_disabled':'Выключено техническое обслуживание'
        }
        labels.update(avatar_updated=_('Изменена фотография профиля'), database_exported=_('Выгружена резервная копия БД'), database_cleared=_('Безвозвратная очистка БД'), database_auth_failed=_('Неудачное подтверждение пароля администратора'), audit_log_exported=_('Выгружена история действий'))
        return _(labels.get(value, value))

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        return render_template('error.html', code=400, message=_('Форма устарела. Обновите страницу и повторите действие.')), 400

    @app.errorhandler(IntegrityError)
    def integrity_error(error):
        db.session.rollback()
        return render_template('error.html', code=409, message=_('Изменение конфликтует с существующими данными. Проверьте время, логин или название и повторите действие.')), 409

    @app.errorhandler(413)
    def upload_too_large(error):
        message = _('Фотография должна быть не больше 5 МБ.') if request.endpoint == 'avatars.settings' else _('Файл слишком большой. Максимум 1 МБ.')
        return render_template('error.html', code=413, message=message), 413

    for code, msg in [(403,'Для этой страницы нужны другие права доступа.'),(404,'Страница или запись не найдена.')]:
        app.register_error_handler(code, lambda error, c=code, m=msg: (render_template('error.html',code=c,message=_(m)),c))
    return app
