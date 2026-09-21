import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from portal.models import AuditLog, db
from conftest import sign_in


MOSCOW = ZoneInfo('Europe/Moscow')


def test_audit_page_is_text_and_admin_only(app, client):
    assert client.get('/admin/audit').status_code == 302
    assert client.post('/admin/audit/export').status_code == 302
    for role in ('student', 'teacher'):
        sign_in(client, role)
        assert client.get('/admin/audit').status_code == 403
        assert client.post('/admin/audit/export', data={}).status_code == 403
        client.post('/logout')
    sign_in(client, 'admin')
    page = client.get('/admin/audit')
    assert page.status_code == 200
    assert b'<pre class="audit-log">' in page.data
    assert b'<table' not in page.data
    assert b'data-audit-export-open' in page.data
    assert b'name="from_date"' in page.data and b'name="to_date"' in page.data
    assert 'Успешный вход [login_success]' in page.text
    assert b'login_success' in client.get('/admin/audit?action=login_success').data
    assert b'login_success' not in client.get('/admin/audit?action=event_created').data


def test_txt_export_includes_moscow_date_boundaries_and_records_action(app, client):
    sign_in(client, 'admin')
    end = datetime.now(MOSCOW).date()
    start = end - timedelta(days=6)
    first = datetime.combine(start, time.min, MOSCOW).astimezone(timezone.utc)
    next_day = datetime.combine(end + timedelta(days=1), time.min, MOSCOW).astimezone(timezone.utc)
    with app.app_context():
        db.session.add_all([
            AuditLog(action='outside_before', entity='test', created_at=first - timedelta(microseconds=1)),
            AuditLog(action='first_included', entity='test', details='line one\nline two', created_at=first),
            AuditLog(action='last_included', entity='test', created_at=next_day - timedelta(microseconds=1)),
            AuditLog(action='outside_after', entity='test', created_at=next_day),
        ])
        db.session.commit()
    response = client.post('/admin/audit/export', data={
        'from_date': start.isoformat(), 'to_date': end.isoformat(), 'password': 'Admin!1234',
    })
    assert response.status_code == 200
    assert response.mimetype == 'text/plain'
    assert re.search(r'logs-\d{2}-\d{2}-\d{4}-\d{2}-\d{2}-\d{2}\.txt', response.headers['Content-Disposition'])
    content = response.data.decode('utf-8-sig')
    assert 'first_included' in content and 'last_included' in content
    assert 'outside_before' not in content and 'outside_after' not in content
    assert 'line one line two' in content
    with app.app_context():
        assert db.session.scalar(select(AuditLog).where(AuditLog.action == 'audit_log_exported'))


def test_export_rejects_invalid_ranges_before_password_check(app, client):
    sign_in(client, 'admin')
    today = datetime.now(MOSCOW).date()
    ranges = [
        ('invalid', today.isoformat()),
        ((today - timedelta(days=7)).isoformat(), today.isoformat()),
        (today.isoformat(), (today - timedelta(days=1)).isoformat()),
        (today.isoformat(), (today + timedelta(days=1)).isoformat()),
    ]
    for start, end in ranges:
        result = client.post('/admin/audit/export', data={
            'from_date': start, 'to_date': end, 'password': 'wrong',
        })
        assert result.status_code == 400
        assert result.mimetype == 'text/html'
    with app.app_context():
        assert not db.session.scalar(select(AuditLog).where(AuditLog.action == 'database_auth_failed'))
        assert not db.session.scalar(select(AuditLog).where(AuditLog.action == 'audit_log_exported'))


def test_export_requires_current_admin_password(app, client):
    sign_in(client, 'admin')
    today = datetime.now(MOSCOW).date().isoformat()
    response = client.post('/admin/audit/export', data={
        'from_date': today, 'to_date': today, 'password': 'wrong',
    })
    assert response.status_code == 400
    assert response.mimetype == 'text/html'
    with app.app_context():
        assert db.session.scalar(select(AuditLog).where(AuditLog.action == 'database_auth_failed'))
        assert not db.session.scalar(select(AuditLog).where(AuditLog.action == 'audit_log_exported'))
