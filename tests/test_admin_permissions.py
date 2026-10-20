from sqlalchemy import select

from portal.models import db, AuditLog, Department, Event, LoginGate, User, utcnow
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


def test_impersonation_permission_opens_specific_user_and_returns_to_admin(app, client):
    ids = app.config['IDS']
    sign_in(client, 'admin')

    selector = client.get('/admin/impersonation?role=teacher')
    assert selector.status_code == 200
    assert 'second.ee' in selector.text
    assert f'/admin/impersonate/user/{ids["second"]}' in selector.text

    response = client.post(f'/admin/impersonate/user/{ids["second"]}')
    assert response.status_code == 302
    assert client.get('/teacher').status_code == 200
    assert client.get('/admin/').status_code == 403

    response = client.post('/admin/stop-impersonation')
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/admin/')
    assert client.get('/admin/').status_code == 200


def test_limited_admin_needs_impersonation_permission(app, client):
    ids = app.config['IDS']
    sign_in(client, 'admin')
    assert create_admin(client, 'without.impersonation', {'news.manage'}).status_code == 302
    client.post('/logout')
    assert client.post('/login', data={
        'login': 'without.impersonation', 'password': 'Limited!123'}).status_code == 302

    assert client.get('/admin/impersonation').status_code == 403
    assert client.post(f'/admin/impersonate/user/{ids["student"]}').status_code == 403


def test_impersonating_admin_can_open_locked_account_and_audit_actions(app, client):
    ids = app.config['IDS']
    with app.app_context():
        student = db.session.get(User, ids['student'])
        student.must_change_password = True
        gate = db.session.get(LoginGate, student.login)
        gate.permanent = True
        gate.updated_at = utcnow()
        db.session.commit()

    sign_in(client, 'admin')
    assert client.post(f'/admin/impersonate/user/{ids["student"]}').status_code == 302
    assert client.get('/events').status_code == 200
    assert client.post(f'/events/{ids["first"]}/book').status_code == 302

    with app.app_context():
        entry = db.session.scalar(select(AuditLog).where(
            AuditLog.action == 'booking_created').order_by(AuditLog.id.desc()).limit(1))
        assert entry.actor_id == ids['admin']
        assert 'acting_as=student:2300431' in entry.details


def test_admin_cannot_impersonate_admin_or_inactive_user(app, client):
    ids = app.config['IDS']
    with app.app_context():
        student = db.session.get(User, ids['student'])
        student.active = False
        dismissed_teacher = db.session.get(User, ids['second'])
        dismissed_teacher.dismissed_on = utcnow().date()
        db.session.commit()

    sign_in(client, 'admin')
    active_teachers = client.get('/admin/impersonation?role=teacher')
    assert 'second.ee' not in active_teachers.text
    unavailable_teachers = client.get('/admin/impersonation?role=teacher&status=inactive')
    assert 'second.ee' in unavailable_teachers.text
    assert f'/admin/impersonate/user/{ids["second"]}' not in unavailable_teachers.text
    assert client.post(f'/admin/impersonate/user/{ids["admin"]}').status_code == 404
    response = client.post(f'/admin/impersonate/user/{ids["student"]}', follow_redirects=True)
    assert response.status_code == 200
    assert 'Нельзя открыть режим для удалённой или отключённой учётной записи.' in response.text
    response = client.post(f'/admin/impersonate/user/{ids["second"]}', follow_redirects=True)
    assert response.status_code == 200
    assert 'Нельзя открыть режим для удалённой или отключённой учётной записи.' in response.text
    assert client.get('/admin/').status_code == 200
