from pathlib import Path
from alembic import command
from alembic.config import Config
from flask_babel import force_locale
from sqlalchemy import select, text
from portal.models import db, User, SiteSettings, Notification, Booking
from portal.services import book_event
from conftest import sign_in


def test_maintenance_blocks_every_user_workflow_but_not_admin(app, client):
    sign_in(client, 'admin')
    assert client.post('/admin/maintenance', data={'maintenance': 'on'}).status_code == 302
    assert client.get('/admin/').status_code == 200
    with app.app_context():
        assert db.session.get(SiteSettings, 1).maintenance
    for role, route in [('student', '/events'), ('teacher', '/teacher')]:
        visitor = app.test_client()
        sign_in(visitor, role)
        response = visitor.get(route)
        assert response.status_code == 503
        assert response.headers['Retry-After'] == '60'
        assert 'Сейчас сервис находится' in response.text
        assert 'class="sidebar"' not in response.text
        for path in ('/settings', '/notifications', '/journal.xlsx'):
            assert visitor.get(path).status_code == 503
        assert visitor.post('/settings', data={'action': 'theme', 'theme': 'dark'}).status_code == 503
        assert visitor.get('/notifications/count').json == {'maintenance': True}
        assert visitor.get('/service-status').json == {'maintenance': True}
        assert visitor.post('/language', data={'language': 'en', 'next': route}).status_code == 302
        assert 'undergoing maintenance' in visitor.get(route).text
        assert visitor.post('/logout').status_code == 302
    guest = app.test_client()
    assert guest.get('/login').status_code == 200
    assert guest.get('/health').status_code == 200
    client.post('/admin/maintenance', data={})
    sign_in(guest)
    assert guest.get('/events').status_code == 200


def test_maintenance_cannot_be_changed_by_student_and_admin_preview_bypasses(app, client):
    sign_in(client)
    assert client.post('/admin/maintenance', data={'maintenance': 'on'}).status_code == 403
    client.post('/logout')
    sign_in(client, 'admin')
    client.post('/admin/maintenance', data={'maintenance': 'on'})
    client.post('/admin/impersonate/student')
    assert client.get('/events').status_code == 200
    assert client.post('/admin/stop-impersonation').status_code == 302
    assert client.get('/admin/').status_code == 200


def test_language_before_login_persists_and_errors_are_translated(app, client):
    russian_page = client.get('/login').text
    assert 'Continue English' in russian_page
    assert 'Забыли пароль?' in russian_page
    assert 'kurenkov.ee@yandex.ru' in russian_page
    assert 'cookie' not in russian_page.lower()
    response = client.post('/language', data={'language': 'en', 'next': '/login'}, follow_redirects=True)
    assert '<html lang="en"' in response.text
    assert 'Log In' in response.text and 'Продолжить на русском' in response.text
    assert 'Forgot your password?' in response.text
    bad = client.post('/login', data={'login': '2300431', 'password': 'wrong'})
    assert 'Incorrect username or password.' in bad.text
    sign_in(client)
    assert 'Hi, Егор!' in client.get('/events').text
    with app.app_context():
        assert db.session.get(User, app.config['IDS']['student']).language == 'en'
    client.post('/settings', data={'action': 'language', 'language': 'ru'})
    assert 'Привет, Егор!' in client.get('/events').text
    client.post('/logout')
    assert 'Continue English' in client.get('/login').text


def test_invalid_language_open_redirect_and_csrf(app, client):
    assert client.post('/language', data={'language': 'de'}).status_code == 400
    for value in ('https://example.org', '//example.org', '/\\example.org', 'https://[broken'):
        response = client.post('/language', data={'language': 'en', 'next': value})
        assert response.location == '/'
    app.config['WTF_CSRF_ENABLED'] = True
    assert client.post('/language', data={'language': 'en'}).status_code == 400
    assert client.post('/admin/maintenance', data={'maintenance': 'on'}).status_code == 400


def test_split_names_once_and_admin_correction(app, client):
    uid = app.config['IDS']['student']
    with app.app_context():
        user = db.session.get(User, uid)
        user.full_name = None
        user.name_locked = False
        db.session.commit()
    sign_in(client)
    data = dict(action='name', last_name='Петров', first_name='Иван', middle_name='', confirm_name='yes')
    assert client.post('/settings', data=data).status_code == 302
    with app.app_context():
        user = db.session.get(User, uid)
        assert (user.full_name, user.last_name, user.first_name, user.middle_name) == ('Петров Иван', 'Петров', 'Иван', None)
    assert 'Привет, Иван!' in client.get('/events').text
    assert client.post('/settings', data={**data, 'first_name': 'Пётр'}).status_code == 403
    client.post('/logout')
    sign_in(client, 'admin')
    form = dict(last_name='Петров', first_name='Пётр', middle_name='Иванович', group_id=app.config['IDS']['group'], course=4, study_mode='full_time', reason='Личное обращение')
    assert client.post(f'/admin/users/{uid}', data=form).status_code == 302
    with app.app_context():
        user = db.session.get(User, uid)
        assert user.full_name == 'Петров Пётр Иванович'
        assert user.first_name == 'Пётр' and user.name_locked


def test_invalid_name_does_not_lock_student(app, client):
    with app.app_context():
        user = db.session.get(User, app.config['IDS']['student'])
        user.full_name, user.name_locked = None, False
        db.session.commit()
    sign_in(client)
    response = client.post('/settings', data=dict(action='name', last_name='Иванов', first_name='<script>', confirm_name='yes'), follow_redirects=True)
    assert 'Укажите фамилию и имя.' in response.text
    with app.app_context():
        assert not db.session.get(User, app.config['IDS']['student']).name_locked


def test_list_preference_details_and_booking_privacy(app, client):
    sign_in(client)
    eid = app.config['IDS']['first']
    assert client.post('/event-view', data={'view': 'invalid'}).status_code == 400
    response = client.post('/event-view', data={'view': 'list', 'next': '/events?mine=1&page=1'}, follow_redirects=True)
    assert 'consultation-list' in response.text
    assert f'/events/{eid}' in response.text
    assert 'class="description"' not in response.text
    detail = client.get(f'/events/{eid}')
    assert detail.status_code == 200 and 'Записаться' in detail.text
    assert 'Записавшиеся студенты' not in detail.text and 'journal.xlsx' not in detail.text
    assert client.post(f'/events/{eid}/book').status_code == 302
    assert 'Вы уже записаны.' in client.get(f'/events/{eid}').text
    assert client.post(f'/events/{eid}/edit', data={}).status_code == 403
    with app.app_context():
        assert db.session.get(User, app.config['IDS']['student']).event_view == 'list'
        assert db.session.scalar(select(Booking.id).where(Booking.event_id == eid))


def test_teacher_greeting_and_views(app, client):
    sign_in(client, 'teacher')
    assert 'Добрый день, Егор Евгеньевич!' in client.get('/teacher').text
    response = client.post('/event-view', data={'view': 'list', 'next': '/teacher'}, follow_redirects=True)
    assert '<table>' in response.text
    response = client.post('/event-view', data={'view': 'grid', 'next': '/teacher'}, follow_redirects=True)
    assert 'class="event-grid"' in response.text


def test_localized_notification_and_personal_address(app, client):
    with app.test_request_context():
        with force_locale('en'):
            book_event(app.config['IDS']['student'], app.config['IDS']['first'])
            db.session.commit()
        item = db.session.scalar(select(Notification).where(Notification.user_id == app.config['IDS']['student']))
        assert item.title == 'Запись на консультацию подтверждена'
        assert 'Аудитория:' in item.body and 'Room:' in item.body_en
    client.post('/language', data={'language': 'en'})
    sign_in(client)
    response = client.get('/notifications')
    assert 'Consultation booking confirmed' in response.text
    assert 'Егор,' in response.text and 'Room:' in response.text


def test_migration_preserves_original_names_and_passwords(app):
    with app.app_context():
        db.session.remove()
        with db.engine.begin() as connection:
            config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
            config.attributes['connection'] = connection
            command.downgrade(config, '0002')
            connection.execute(text("UPDATE users SET full_name = 'Куренков Егор Евгеньевич' WHERE id = :id"), {'id': app.config['IDS']['student']})
            original = connection.execute(text('SELECT id, full_name, password_hash FROM users ORDER BY id')).all()
            command.upgrade(config, 'head')
            assert original == connection.execute(text('SELECT id, full_name, password_hash FROM users ORDER BY id')).all()
        user = db.session.get(User, app.config['IDS']['student'])
        assert (user.last_name, user.first_name, user.middle_name) == ('Куренков', 'Егор', 'Евгеньевич')
        assert not db.session.get(SiteSettings, 1).maintenance
