from sqlalchemy import select

from portal.models import db, Department, Event, User
from portal.permissions import ADMIN_PERMISSION_CODE_SET
from conftest import sign_in


def create_admin(client, login, permissions, password='Limited!123'):
    return client.post('/admin/users/new', data={
        'role': 'admin',
        'login': login,
        'last_name': 'Тестовый',
        'first_name': 'Администратор',
        'middle_name': '',
        'initial_password': password,
        'admin_permissions': list(permissions),
    })


def test_bootstrap_admin_receives_all_permissions(app):
    with app.app_context():
        admin = db.session.get(User, app.config['IDS']['admin'])
        assert admin.admin_permission_codes == ADMIN_PERMISSION_CODE_SET


def test_javascript_static_files_have_executable_mimetype(client):
    response = client.get('/static/app.js')
    assert response.status_code == 200
    assert response.mimetype == 'application/javascript'


def test_limited_admin_only_sees_and_uses_assigned_functions(app, client):
    sign_in(client, 'admin')
    assert create_admin(client, 'limited.admin', {'news.manage', 'about.manage'}).status_code == 302
    client.post('/logout')

    assert client.post('/login', data={'login': 'limited.admin', 'password': 'Limited!123'}).status_code == 302
    dashboard = client.get('/admin/')
    assert dashboard.status_code == 200
    assert 'Мои закреплённые права' in dashboard.text
    assert 'href="/admin/users"' not in dashboard.text
    assert client.get('/admin/users').status_code == 403
    assert client.get('/admin/catalogs').status_code == 403
    assert client.get('/admin/database').status_code == 403
    assert client.get('/events/new').status_code == 403
    assert client.get('/news/new').status_code == 200
    assert client.post('/about', data={'body_html': '<p>Описание ограниченного администратора</p>'}).status_code == 302


def test_admin_can_delegate_only_permissions_they_hold(app, client):
    sign_in(client, 'admin')
    assert create_admin(client, 'delegating.admin', {'admins.manage', 'news.manage'}).status_code == 302
    client.post('/logout')
    assert client.post('/login', data={'login': 'delegating.admin', 'password': 'Limited!123'}).status_code == 302

    forbidden = create_admin(client, 'forbidden.child', {'database.clear'})
    assert forbidden.status_code == 403
    with app.app_context():
        assert db.session.scalar(select(User.id).where(User.login == 'forbidden.child')) is None

    allowed = create_admin(client, 'allowed.child', {'news.manage'}, password='Allowed!123')
    assert allowed.status_code == 302
    with app.app_context():
        child = db.session.scalar(select(User).where(User.login == 'allowed.child', User.role == 'admin'))
        assert child.admin_permission_codes == {'news.manage'}


def test_existing_admin_permissions_can_be_reassigned(app, client):
    sign_in(client, 'admin')
    assert create_admin(client, 'existing.admin', {'news.manage'}).status_code == 302
    with app.app_context():
        target = db.session.scalar(select(User).where(User.login == 'existing.admin'))
        target_id = target.id

    response = client.post(f'/admin/users/{target_id}', data={
        'role': 'admin',
        'login': 'existing.admin',
        'last_name': 'Тестовый',
        'first_name': 'Администратор',
        'middle_name': '',
        'reason': 'Изменение закреплённых полномочий',
        'admin_permissions': ['about.manage', 'users.export'],
    })
    assert response.status_code == 302
    with app.app_context():
        target = db.session.get(User, target_id)
        assert target.admin_permission_codes == {'about.manage', 'users.export'}


def test_limited_admin_cannot_manage_more_privileged_admin(app, client):
    sign_in(client, 'admin')
    assert create_admin(client, 'senior.admin', {'admins.manage', 'news.manage'}).status_code == 302
    assert create_admin(client, 'junior.admin', {'admins.manage'}).status_code == 302
    with app.app_context():
        senior_id = db.session.scalar(select(User.id).where(User.login == 'senior.admin'))
    client.post('/logout')
    assert client.post('/login', data={'login': 'junior.admin', 'password': 'Limited!123'}).status_code == 302

    assert client.get(f'/admin/users/{senior_id}').status_code == 403
    assert client.post(f'/admin/users/{senior_id}/toggle').status_code == 403


def test_teacher_can_be_promoted_to_limited_admin(app, client):
    ids = app.config['IDS']
    sign_in(client, 'admin')
    response = client.post(f'/admin/users/{ids["second"]}', data={
        'role': 'admin',
        'login': 'second.ee',
        'last_name': 'Иванов',
        'first_name': 'Иван',
        'middle_name': 'Иванович',
        'reason': 'Назначение нового администратора',
        'initial_password': 'Promoted!123',
        'admin_permissions': ['news.manage'],
    })
    assert response.status_code == 302

    with app.app_context():
        promoted = db.session.get(User, ids['second'])
        assert promoted.role == 'admin'
        assert promoted.department_id is None
        assert promoted.subjects == []
        assert promoted.admin_permission_codes == {'news.manage'}
        assert all(event.status == 'cancelled' for event in db.session.scalars(
            select(Event).where(Event.teacher_id == promoted.id)).all())

    client.post('/logout')
    assert client.post('/login', data={'login': 'second.ee', 'password': 'Promoted!123'}).status_code == 302
    assert client.get('/news/new').status_code == 200
    assert client.get('/admin/users').status_code == 403


def test_student_role_change_updates_login_and_clears_student_fields(app, client):
    ids = app.config['IDS']
    with app.app_context():
        department_id = db.session.scalar(select(Department.id))
    sign_in(client, 'admin')
    response = client.post(f'/admin/users/{ids["student"]}', data={
        'role': 'teacher',
        'login': 'promoted.student',
        'last_name': 'Куренков',
        'first_name': 'Егор',
        'middle_name': 'Евгеньевич',
        'reason': 'Перевод пользователя в преподаватели',
        'department_id': department_id,
    })
    assert response.status_code == 302

    with app.app_context():
        changed = db.session.get(User, ids['student'])
        assert changed.role == 'teacher'
        assert changed.login == 'promoted.student'
        assert changed.group_id is None
        assert changed.course is None
        assert changed.study_mode is None
        assert changed.department_id == department_id
        assert changed.admin_permission_codes == frozenset()

    client.post('/logout')
    assert client.post('/login', data={'login': 'promoted.student', 'password': 'Student'}).status_code == 302


def test_admin_cannot_change_own_role_or_permissions(app, client):
    ids = app.config['IDS']
    with app.app_context():
        department_id = db.session.scalar(select(Department.id))
    sign_in(client, 'admin')
    response = client.post(f'/admin/users/{ids["admin"]}', data={
        'role': 'teacher',
        'login': 'lxrdx',
        'last_name': 'Администратор',
        'first_name': 'Системы',
        'middle_name': '',
        'reason': 'Попытка изменить себя',
        'department_id': department_id,
    }, follow_redirects=True)
    assert response.status_code == 200
    assert 'Нельзя изменить собственную роль или логин' in response.text
    with app.app_context():
        admin = db.session.get(User, ids['admin'])
        assert admin.role == 'admin'
        assert admin.admin_permission_codes == ADMIN_PERMISSION_CODE_SET
