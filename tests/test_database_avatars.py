import html
import io
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
from PIL import Image, PngImagePlugin
from sqlalchemy import select, func
from sqlalchemy.engine import make_url

from conftest import sign_in
from portal.admin import create_user
from portal.models import db, AuditLog, Booking, Department, Event, Group, LoginGate, Notification, Subject, User, UserAvatar, utcnow
from portal.services import book_event


def photo(size=(320, 180), color='green'):
    output = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text('private', 'must-not-survive')
    Image.new('RGB', size, color).save(output, 'PNG', pnginfo=info)
    output.seek(0)
    return output


def preview(client, **chosen):
    response = client.post('/admin/database/preview', data=chosen)
    assert response.status_code == 200, response.text
    token = html.unescape(re.search(r'name="token" value="([^"]+)"', response.text)[1])
    phrase = re.search(r'<code>(DELETE \d+)</code>', response.text)[1]
    return dict(token=token, confirmation=phrase, backup_saved='yes', password='Admin!1234')


@pytest.mark.parametrize('role', ['student', 'teacher', 'admin'])
def test_settings_contact_warning_and_avatar_all_roles(app, client, role):
    sign_in(client, role)
    page = client.get('/settings')
    assert 'kurenkov.ee@yandex.ru' in page.text and '06.00 до 15.00 по Мск' in page.text
    assert ('Предупреждение! Не ставить личные пароли' in page.text) == (role == 'student')
    assert 'База данных' in page.text if role == 'admin' else 'href="/admin/database"' not in page.text
    response = client.post('/settings/avatar', data={'action':'upload', 'avatar':(photo(), '../../avatar.png')})
    assert response.status_code == 302
    uid = app.config['IDS'][role]
    with app.app_context():
        image = db.session.get(UserAvatar, uid).image
        decoded = Image.open(io.BytesIO(image))
        assert decoded.format == 'JPEG' and decoded.size == (256, 256)
        assert 'private' not in decoded.info and not decoded.getexif()
    fetched = client.get(f'/avatars/{uid}')
    assert fetched.data == image and fetched.mimetype == 'image/jpeg'
    assert fetched.headers['Cache-Control'] == 'no-store'
    assert f'/avatars/{uid}' in client.get('/settings').text
    client.post('/settings/avatar', data={'action':'upload', 'avatar':(photo(color='blue'), 'replacement.png')})
    with app.app_context():
        assert db.session.get(UserAvatar, uid).image != image
    client.post('/settings/avatar', data={'action':'remove'})
    assert client.get(f'/avatars/{uid}').status_code == 404


@pytest.mark.parametrize('payload,filename', [(b'<svg><script>alert(1)</script></svg>', 'avatar.png'), (b'not an image', 'photo.jpg'), (b'', 'empty.png')])
def test_avatar_rejects_bad_content(app, client, payload, filename):
    sign_in(client)
    response = client.post('/settings/avatar', data={'action':'upload', 'avatar':(io.BytesIO(payload), filename)}, follow_redirects=True)
    assert 'Не удалось прочитать фотографию' in response.text
    with app.app_context():
        assert db.session.get(UserAvatar, app.config['IDS']['student']) is None


def test_avatar_limits_and_privacy(app, client):
    sign_in(client)
    assert client.post('/settings/avatar', data={'action':'upload', 'avatar':(photo((9000,1)), 'wide.png')}, follow_redirects=True).status_code == 200
    with app.app_context():
        assert db.session.get(UserAvatar, app.config['IDS']['student']) is None
    large = client.post('/settings/avatar', data={'action':'upload', 'avatar':(io.BytesIO(b'x' * (6*1024*1024)), 'big.jpg')})
    assert large.status_code == 413 and '5 МБ' in large.text
    client.post('/settings/avatar', data={'action':'upload', 'avatar':(photo(), 'okay.png')})
    second = app.test_client()
    assert second.get(f'/avatars/{app.config["IDS"]["student"]}').status_code == 302
    sign_in(second, 'teacher')
    assert second.get(f'/avatars/{app.config["IDS"]["student"]}').status_code == 404
    second.post('/logout')
    sign_in(second, 'admin')
    assert second.get(f'/avatars/{app.config["IDS"]["student"]}').status_code == 200


@pytest.mark.parametrize('role', ['student', 'teacher'])
def test_database_routes_admin_only(app, client, role):
    sign_in(client, role)
    assert client.get('/admin/database').status_code == 403
    for route in ('preview', 'clear', 'export'):
        assert client.post('/admin/database/' + route, data={'scope':'all'}).status_code == 403


def test_database_csrf_and_no_get_destruction(app, client):
    sign_in(client, 'admin')
    app.config['WTF_CSRF_ENABLED'] = True
    for route in ('preview', 'clear', 'export'):
        assert client.post('/admin/database/' + route, data={'scope':'all'}).status_code == 400
        assert client.get('/admin/database/' + route).status_code == 405
    assert client.post('/settings/avatar', data={'action':'remove'}).status_code == 400


def test_clear_confirmation_and_password_failures_do_not_delete(app, client):
    sign_in(client, 'admin')
    data = preview(client, scope='all')
    for change in ({'confirmation':'DELETE'}, {'backup_saved':''}, {'password':'wrong'}, {'token':'tampered'}):
        assert client.post('/admin/database/clear', data={**data, **change}).status_code == 400
        with app.app_context():
            assert db.session.scalar(select(func.count(User.id))) == 4
    with app.app_context():
        assert db.session.get(LoginGate, 'lxrdx').failures == 1


def test_clear_rejects_stale_preview(app, client):
    sign_in(client, 'admin')
    data = preview(client, scope='all')
    with app.app_context():
        create_user(dict(role='student',login='2600888',group_id=app.config['IDS']['group'],course=4))
        db.session.commit()
    response = client.post('/admin/database/clear', data=data)
    assert response.status_code == 400 and 'Данные изменились' in response.text
    with app.app_context():
        assert db.session.scalar(select(func.count(User.id))) == 5


@pytest.mark.parametrize('chosen', [{'scope':''}, {'scope':'bogus'}, {'scope':'role','role':'bogus'}, {'scope':'group','group_id':'not-id'}, {'scope':'department','department_id':'999999'}])
def test_invalid_cleanup_scope_never_defaults_to_all(app, client, chosen):
    sign_in(client, 'admin')
    assert client.post('/admin/database/preview', data=chosen).status_code == 400


def test_clear_group_keeps_other_groups_and_teachers(app, client):
    with app.app_context():
        other = create_user(dict(role='student',login='2600888',group_id=app.config['IDS']['other_group'],course=3))
        other_id = other.id
        book_event(app.config['IDS']['student'], app.config['IDS']['first'])
        db.session.add(UserAvatar(user_id=app.config['IDS']['student'], image=b'avatar'))
        db.session.commit()
    sign_in(client, 'admin')
    data = preview(client, scope='group', group_id=app.config['IDS']['group'])
    assert client.post('/admin/database/clear', data=data).status_code == 302
    with app.app_context():
        assert db.session.get(User, app.config['IDS']['student']) is None
        assert db.session.get(UserAvatar, app.config['IDS']['student']) is None
        assert db.session.get(User, other_id) and db.session.get(User, app.config['IDS']['teacher'])
        assert db.session.get(Event, app.config['IDS']['first']) is None
        assert db.session.get(Event, app.config['IDS']['overlap']) is not None
        assert db.session.get(Group, app.config['IDS']['group']) is not None
        assert db.session.scalar(select(func.count(Booking.id))) == 0
    assert client.post('/admin/database/clear', data=data).status_code == 400


def test_clear_department_keeps_students_and_other_department(app, client):
    with app.app_context():
        dept = Department(name='Other department')
        db.session.add(dept)
        teacher = db.session.get(User, app.config['IDS']['teacher'])
        department_id = teacher.department_id
        db.session.get(User, app.config['IDS']['second']).department = dept
        book_event(app.config['IDS']['student'], app.config['IDS']['first'])
        db.session.commit()
    sign_in(client, 'admin')
    data = preview(client, scope='department', department_id=department_id)
    assert client.post('/admin/database/clear', data=data).status_code == 302
    with app.app_context():
        assert db.session.get(User, app.config['IDS']['teacher']) is None
        assert db.session.get(User, app.config['IDS']['student']) is not None
        assert db.session.get(User, app.config['IDS']['second']) is not None
        assert db.session.get(Event, app.config['IDS']['overlap']) is not None
        assert db.session.scalar(select(func.count(Booking.id))) == 0


def test_clear_admin_role_preserves_current_admin_and_audit(app, client):
    with app.app_context():
        other = create_user(dict(role='admin', login='admin.two', full_name='Второй Администратор', initial_password='Another!123'))
        other_id = other.id
        db.session.add(AuditLog(actor_id=other_id, action='test_history', entity='example'))
        db.session.commit()
    sign_in(client, 'admin')
    data = preview(client, scope='role', role='admin')
    assert client.post('/admin/database/clear', data=data).status_code == 302
    with app.app_context():
        assert db.session.get(User, app.config['IDS']['admin']).active
        assert db.session.get(User, other_id) is None
        assert db.session.scalar(select(AuditLog).where(AuditLog.action == 'test_history')).actor_id is None
        assert db.session.get(User, app.config['IDS']['student']) is not None


def test_full_clear_keeps_owner_avatar_password_and_settings(app, client):
    with app.app_context():
        owner = db.session.get(User, app.config['IDS']['admin'])
        original_hash = owner.password_hash
        db.session.add(UserAvatar(user_id=owner.id, image=b'owner-avatar'))
        book_event(app.config['IDS']['student'], app.config['IDS']['first'])
        db.session.commit()
    sign_in(client, 'admin')
    data = preview(client, scope='all')
    assert client.post('/admin/database/clear', data=data).status_code == 302
    with app.app_context():
        assert db.session.scalar(select(func.count(User.id))) == 1
        assert db.session.get(User, app.config['IDS']['admin']).password_hash == original_hash
        assert db.session.get(UserAvatar, app.config['IDS']['admin']).image == b'owner-avatar'
        for model in (Event, Booking, Notification, Group, Department, Subject):
            assert db.session.scalar(select(func.count()).select_from(model)) == 0
        assert db.session.scalar(select(AuditLog).where(AuditLog.action == 'database_cleared'))
    assert client.get('/admin/').status_code == 200


def test_actual_dump_restores_data_and_avatars(app, client, tmp_path):
    sign_in(client, 'admin')
    client.post('/settings/avatar', data={'action':'upload', 'avatar':(photo(), 'avatar.png')})
    with app.app_context():
        original_hash = db.session.get(User, app.config['IDS']['student']).password_hash
        original_avatar = db.session.get(UserAvatar, app.config['IDS']['admin']).image
    response = client.post('/admin/database/export', data={'password':'Admin!1234'})
    assert response.status_code == 200 and response.data.startswith(b'PGDMP'), response.status_code
    assert 'attachment;' in response.headers['Content-Disposition']
    assert response.headers['Cache-Control'] == 'no-store'
    dump_file = tmp_path / 'backup.dump'
    dump_file.write_bytes(response.data)
    binary = os.environ.get('PG_DUMP_PATH') or shutil.which('pg_dump')
    restore = str(Path(binary).with_name('pg_restore.exe' if os.name == 'nt' else 'pg_restore'))
    url = make_url(app.config['SQLALCHEMY_DATABASE_URI'])
    assert url.database.endswith('_test')
    env = {key:value for key,value in os.environ.items() if not key.startswith('PG')}
    env.update(PGHOST=url.host, PGPORT=str(url.port), PGUSER=url.username, PGPASSWORD=url.password or '', PGDATABASE=url.database)
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    restored = subprocess.run([restore, '--clean', '--if-exists', '--no-owner', '--no-acl', '--exit-on-error', '-d', url.database, str(dump_file)], env=env, capture_output=True, timeout=90)
    assert restored.returncode == 0, restored.stderr.decode(errors='replace')
    with app.app_context():
        assert db.session.get(User, app.config['IDS']['student']).password_hash == original_hash
        assert db.session.get(UserAvatar, app.config['IDS']['admin']).image == original_avatar


def test_export_password_and_missing_binary(app, client):
    sign_in(client, 'admin')
    assert client.post('/admin/database/export', data={'password':'wrong'}).status_code == 400
    app.config['PG_DUMP_PATH'] = 'missing-executable-pg-dump'
    assert client.post('/admin/database/export', data={'password':'Admin!1234'}).status_code == 400


def test_cleanup_rolls_back_all_changes_on_failure(app, client, monkeypatch):
    from portal import database_admin
    original = database_admin.apply_cleanup
    def fail_after_deletes(plan):
        original(plan)
        raise ValueError('Simulated transaction failure')
    monkeypatch.setattr(database_admin, 'apply_cleanup', fail_after_deletes)
    sign_in(client, 'admin')
    data = preview(client, scope='all')
    assert client.post('/admin/database/clear', data=data).status_code == 400
    with app.app_context():
        assert db.session.scalar(select(func.count(User.id))) == 4
        assert db.session.scalar(select(func.count(Event.id))) == 3
        assert db.session.scalar(select(func.count(Group.id))) == 2


def test_admin_role_view_cannot_export_database(app, client):
    sign_in(client, 'admin')
    client.post('/admin/impersonate/student')
    assert client.post('/admin/database/export', data={'password':'Admin!1234'}).status_code == 403
    assert client.post('/admin/database/preview', data={'scope':'all'}).status_code == 403


def test_new_settings_english(app, client):
    client.post('/language', data={'language':'en'})
    sign_in(client)
    response = client.get('/settings')
    assert 'Profile photo' in response.text and 'Do not reuse your personal passwords' in response.text
    assert '06:00 to 15:00 Moscow time' in response.text


def test_preview_language_switch_returns_to_get_page(app, client):
    sign_in(client, 'admin')
    response = client.post('/admin/database/preview', data={'scope':'all'})
    target = re.search(r'name="next" value="([^"]+)"', response.text)[1]
    assert target == '/admin/database'
    translated = client.post('/language', data={'language':'en', 'next':target}, follow_redirects=True)
    assert translated.status_code == 200 and 'PostgreSQL backup' in translated.text


def test_avatar_upload_rechecks_session_after_processing(app, client, monkeypatch):
    from portal import avatars
    original = avatars.normalize_avatar
    def revoke_during_processing(upload):
        data = original(upload)
        user = db.session.get(User, app.config['IDS']['student'])
        user.session_version += 1
        db.session.commit()
        return data
    monkeypatch.setattr(avatars, 'normalize_avatar', revoke_during_processing)
    sign_in(client)
    response = client.post('/settings/avatar', data={'action':'upload', 'avatar':(photo(), 'avatar.png')})
    assert response.status_code == 403
    with app.app_context():
        assert db.session.get(UserAvatar, app.config['IDS']['student']) is None
