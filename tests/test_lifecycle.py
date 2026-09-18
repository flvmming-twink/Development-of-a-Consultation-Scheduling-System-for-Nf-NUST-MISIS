from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select, func
from portal.models import db, User, Group, Department, Event, Booking, Notification, utcnow
from portal.admin import create_user
from portal.lifecycle import trash_users, restore_users, run_maintenance, deliver_scheduled
from portal.services import book_event, booking_problem
from conftest import sign_in

def test_inline_group_and_department_creation(app, client):
    sign_in(client, 'admin')
    response = client.post('/admin/users/new', data=dict(role='student', login='2600999', new_group='БПИ-26', course=1, study_mode='full_time'))
    assert response.status_code == 302
    response = client.post('/admin/users/new', data=dict(role='teacher', login='petrov.ee', full_name='Петров Пётр Петрович', new_department='Математика'))
    assert response.status_code == 302
    with app.app_context():
        student = db.session.scalar(select(User).where(User.login == '2600999'))
        assert student.group.entry_year == 2026
        assert student.full_name is None
        teacher = db.session.scalar(select(User).where(User.login == 'petrov.ee'))
        assert teacher.department.name == 'Математика'
    assert 'Математика' in client.get('/admin/catalogs').text

def test_department_required_and_linked_department_not_deleted(app, client):
    sign_in(client, 'admin')
    response = client.post('/admin/users/new', data=dict(role='teacher', login='petrov.ee', full_name='Петров Пётр Петрович'))
    assert response.status_code == 200 and 'Кафедра' in response.text
    with app.app_context():
        dept_id = db.session.get(User, app.config['IDS']['teacher']).department_id
    response = client.post('/admin/catalogs', data=dict(kind='department', id=dept_id, delete='yes'), follow_redirects=True)
    assert 'Кафедра связана' in response.text
    client.post('/admin/catalogs', data=dict(kind='department', name='Новая кафедра', active='yes'))
    with app.app_context():
        empty_id = db.session.scalar(select(Department.id).where(Department.name == 'Новая кафедра'))
    client.post('/admin/catalogs', data=dict(kind='department', id=empty_id, delete='yes'))
    with app.app_context():
        assert db.session.get(Department, empty_id) is None
        assert db.session.get(Department, dept_id) is not None

@pytest.mark.parametrize('mode,day,course,graduated', [
    ('full_time', '2026-08-31T23:59:59', 3, False),
    ('full_time', '2026-09-01T00:00:00', 4, False),
    ('full_time', '2027-08-31T23:59:59', 4, False),
    ('full_time', '2027-09-01T00:00:00', 4, True),
    ('part_time', '2027-09-01T00:00:00', 5, False),
    ('part_time', '2028-09-01T00:00:00', 5, True),
])
def test_academic_year_boundary(app, mode, day, course, graduated):
    with app.app_context():
        student = db.session.get(User, app.config['IDS']['student'])
        student.study_mode = mode
        now = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo('Asia/Yekaterinburg'))
        assert student.academic_status(now) == (course, graduated)

def test_bulk_delete_atomic_and_teacher_requires_dismissal(app, client):
    ids = app.config['IDS']
    sign_in(client, 'admin')
    response = client.post('/admin/users/bulk', data={'user_ids':[ids['student'], ids['teacher']], 'action':'trash'}, follow_redirects=True)
    assert 'сначала укажите дату увольнения' in response.text
    with app.app_context():
        assert db.session.get(User, ids['student']).deleted_at is None
        assert db.session.get(User, ids['teacher']).deleted_at is None
    response = client.post('/admin/users/bulk', data={'user_ids':[ids['student'], ids['admin']], 'action':'trash'}, follow_redirects=True)
    assert 'собственную' in response.text
    with app.app_context():
        assert db.session.get(User, ids['student']).active

def test_trash_restoration_and_session_invalidation(app, client):
    ids = app.config['IDS']
    student_client = app.test_client()
    sign_in(student_client)
    student_client.post(f'/events/{ids["first"]}/book')
    sign_in(client, 'admin')
    client.post('/admin/users/bulk', data={'user_ids':[ids['student']], 'action':'trash'})
    assert student_client.get('/bookings').status_code == 302
    with app.app_context():
        assert db.session.get(User, ids['student']).deleted_at is not None
        assert db.session.scalar(select(Booking)).status == 'cancelled'
    assert '2300431' not in client.get('/admin/users').text
    assert '2300431' in client.get('/admin/trash').text
    assert client.post(f'/admin/users/{ids["student"]}/toggle').status_code == 404
    client.post('/admin/users/bulk', data={'user_ids':[ids['student']], 'action':'restore'})
    with app.app_context():
        user = db.session.get(User, ids['student'])
        assert user.deleted_at is None and user.active
        assert db.session.scalar(select(Booking)).status == 'cancelled'
    assert sign_in(student_client).status_code == 302

def test_purge_exact_ten_days_preserves_journal(app, client):
    ids = app.config['IDS']
    with app.app_context():
        book_event(ids['student'], ids['first'])
        student = db.session.get(User, ids['student'])
        teacher = db.session.get(User, ids['teacher'])
        teacher.dismissed_on = utcnow().date()
        trash_users([student.id, teacher.id], db.session.get(User, ids['admin']))
        deleted = student.deleted_at
        db.session.commit()
        run_maintenance(deleted + timedelta(days=10, microseconds=-1))
        assert db.session.get(User, ids['student']) is not None
        run_maintenance(deleted + timedelta(days=10))
        assert db.session.get(User, ids['student']) is None
        assert db.session.get(User, ids['teacher']) is None
        booking = db.session.scalar(select(Booking))
        assert booking.student_id is None and booking.status == 'cancelled'
        assert db.session.get(Event, ids['first']).teacher_id is None
    sign_in(client, 'admin')
    assert 'Удалённый студент' in client.get('/journal').text
    assert 'Удалённый преподаватель' in client.get(f'/events/{ids["first"]}').text
    assert client.get('/journal.xlsx').status_code == 200

def test_expired_trash_cannot_restore(app, client):
    ids = app.config['IDS']
    with app.app_context():
        user = db.session.get(User, ids['student'])
        user.active = False
        user.deleted_at = utcnow() - timedelta(days=10)
        db.session.commit()
    sign_in(client, 'admin')
    response = client.post('/admin/users/bulk', data={'user_ids':[ids['student']], 'action':'restore'}, follow_redirects=True)
    assert 'Срок восстановления истёк' in response.text

def test_dismissal_cancels_events_and_restore_stays_inactive(app, client):
    ids = app.config['IDS']
    sign_in(client, 'admin')
    with app.app_context():
        teacher = db.session.get(User, ids['teacher'])
        data = dict(full_name=teacher.full_name, department_id=teacher.department_id, dismissed_on=utcnow().date().isoformat())
    client.post(f'/admin/users/{ids["teacher"]}', data=data)
    with app.app_context():
        assert not db.session.get(User, ids['teacher']).active
        assert db.session.get(Event, ids['first']).status == 'cancelled'
    client.post('/admin/users/bulk', data={'user_ids':[ids['teacher']], 'action':'trash'})
    client.post('/admin/users/bulk', data={'user_ids':[ids['teacher']], 'action':'restore'})
    with app.app_context():
        assert not db.session.get(User, ids['teacher']).active

def test_notifications_deduplicated_and_private(app, client):
    ids = app.config['IDS']
    with app.app_context():
        now = utcnow()
        event = db.session.get(Event, ids['first'])
        event.registration_opens_at = now - timedelta(seconds=1)
        deliver_scheduled(now)
        count = db.session.scalar(select(func.count(Notification.id)))
        deliver_scheduled(now)
        assert db.session.scalar(select(func.count(Notification.id))) == count
        book_event(ids['student'], ids['first'])
        event = db.session.get(Event, ids['first'])
        deliver_scheduled(event.starts_at - timedelta(minutes=30))
        deliver_scheduled(event.starts_at)
        db.session.commit()
        for role in ('student','teacher','admin'):
            titles = db.session.scalars(select(Notification.title).where(Notification.user_id == ids[role])).all()
            assert 'Консультация началась' in titles
            assert 'Консультация начнётся в течение часа' in titles
        other_notice = db.session.scalar(select(Notification.id).where(Notification.user_id == ids['teacher']).limit(1))
    sign_in(client)
    assert client.get('/notifications/count').json['unread'] > 0
    assert client.post(f'/notifications/{other_notice}/read').status_code == 404
    client.post('/notifications/read-all')
    assert client.get('/notifications/count').json['unread'] == 0

def test_registration_not_open_blocks_booking(app):
    ids = app.config['IDS']
    with app.app_context():
        now = utcnow()
        event = db.session.get(Event, ids['first'])
        event.registration_opens_at = now + timedelta(hours=1)
        assert 'ещё не открыта' in booking_problem(event, db.session.get(User, ids['student']), now)
        deliver_scheduled(now)
        assert not db.session.scalar(select(Notification.id).where(Notification.key.like(f'event:{event.id}:open:%')))

def test_graduation_filter_and_admin_notification(app, client):
    with app.app_context():
        student = db.session.get(User, app.config['IDS']['student'])
        student.group.admission_year = utcnow().year - 5
        run_maintenance()
        count = db.session.scalar(select(func.count(Notification.id)).where(Notification.key.like('graduate:%')))
        run_maintenance()
        assert db.session.scalar(select(func.count(Notification.id)).where(Notification.key.like('graduate:%'))) == count
    sign_in(client, 'admin')
    response = client.get('/admin/users?status=graduated')
    assert 'graduation-row' in response.text and '2300431' in response.text

def test_new_routes_forbidden_to_student(app, client):
    sign_in(client)
    assert client.get('/admin/trash').status_code == 403
    assert client.post('/admin/users/bulk', data={'user_ids':[app.config['IDS']['admin']]}).status_code == 403
