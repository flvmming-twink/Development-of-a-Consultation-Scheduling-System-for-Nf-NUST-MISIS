from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from conftest import sign_in
from portal.content import clean_rich_text
from portal.models import db, NewsPost, NewsRead, User, utcnow


def test_rich_text_is_sanitized():
    cleaned = clean_rich_text('<h2>О системе</h2><script>alert(1)</script><p><strong>Важно</strong> '
        '<a href="javascript:alert(2)" onclick="alert(3)">ссылка</a> '
        '<a href="https://example.org" style="color:red">сайт</a></p>')
    assert '<h2>О системе</h2>' in cleaned
    assert '<strong>Важно</strong>' in cleaned
    assert 'script' not in cleaned and 'alert' not in cleaned and 'onclick' not in cleaned
    assert '<a>ссылка</a>' in cleaned
    assert 'href="https://example.org"' in cleaned and 'style=' not in cleaned


def test_about_starts_empty_and_only_admin_can_edit(app, client):
    sign_in(client)
    page = client.get('/about')
    assert page.status_code == 200
    assert 'rich-editor' not in page.text
    assert client.post('/about', data={'body_html': '<p>Нет доступа</p>'}).status_code == 403

    admin = app.test_client()
    sign_in(admin, 'admin')
    response = admin.post('/about', data={'body_html': '<h2>Цель</h2><p><strong>Запись</strong> на консультации.</p>'})
    assert response.status_code == 302
    assert '<h2>Цель</h2>' in admin.get('/about').text
    page = client.get('/about')
    assert '<strong>Запись</strong>' in page.text


def publish(client, title, body='<p>Текст <strong>новости</strong>.</p>'):
    response = client.post('/news/new', data={'title': title, 'body_html': body})
    assert response.status_code == 302
    return int(response.headers['Location'].rstrip('/').split('/')[-1])


def test_news_permissions_unread_counter_filters_and_colors(app, client):
    teacher = app.test_client()
    sign_in(teacher, 'teacher')
    teacher_post = publish(teacher, 'Новость преподавателя')

    admin = app.test_client()
    sign_in(admin, 'admin')
    admin_post = publish(admin, 'Важная новость администрации')

    sign_in(client)
    page = client.get('/news')
    assert 'Новость преподавателя' in page.text
    assert 'Важная новость администрации' in page.text
    assert 'admin-news' in page.text
    assert '>2<' in page.text
    assert client.post('/news/new', data={'title': 'Запрещено', 'body_html': '<p>Текст</p>'}).status_code == 403

    with app.app_context():
        teacher_id = app.config['IDS']['teacher']
        today = utcnow().astimezone(ZoneInfo('Asia/Yekaterinburg')).date().isoformat()
    filtered = client.get(f'/news?teacher={teacher_id}&date={today}&sort=teacher')
    assert 'Новость преподавателя' in filtered.text
    assert 'Важная новость администрации' not in filtered.text

    assert client.get(f'/news/{teacher_post}').status_code == 200
    with app.app_context():
        student_id = app.config['IDS']['student']
        assert db.session.scalar(select(func.count()).select_from(NewsRead).where(NewsRead.user_id == student_id)) == 1
    assert client.get(f'/news/{admin_post}').status_code == 200
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(NewsRead).where(NewsRead.user_id == student_id)) == 2

    assert teacher.post(f'/news/{admin_post}/edit', data={'title': 'Чужая', 'body_html': '<p>Текст</p>'}).status_code == 403
    assert admin.post(f'/news/{teacher_post}/edit', data={'title': 'Исправленная новость', 'body_html': '<p>Обновлено</p>'}).status_code == 302


def test_teacher_navigation_order_and_profile_link_without_name(app, client):
    sign_in(client, 'teacher')
    html = client.get('/teacher').text
    assert html.index('Создать занятие') < html.index('Новости') < html.index('Журнал студентов')

    client.get('/logout')
    with app.app_context():
        student = db.session.get(User, app.config['IDS']['student'])
        student.full_name = None
        student.last_name = student.first_name = student.middle_name = None
        student.name_locked = False
        db.session.commit()
    student_client = app.test_client()
    sign_in(student_client)
    html = student_client.get('/events').text
    assert 'class="sidebar-bottom" href="/settings"' in html
    assert 'Заполните ФИО' in html


def test_student_dashboard_combines_primary_workflow_news_and_about(app, client):
    teacher = app.test_client()
    sign_in(teacher, 'teacher')
    publish(teacher, 'Изменение расписания')

    admin = app.test_client()
    sign_in(admin, 'admin')
    admin.post('/about', data={'body_html': '<p>Сервис очных консультаций.</p>'})

    sign_in(client)
    event_id = app.config['IDS']['first']
    page = client.get('/events')
    assert page.status_code == 200
    assert 'class="authenticated student-shell"' in page.text
    assert 'class="student-header"' in page.text
    assert 'class="sidebar"' not in page.text
    assert 'Мои ближайшие записи' in page.text
    assert 'Доступные консультации' in page.text
    assert 'Изменение расписания' in page.text
    assert 'Сервис очных консультаций.' in page.text
    assert f'action="/events/{event_id}/book"' in page.text

    booked = client.post(f'/events/{event_id}/book', follow_redirects=True)
    assert booked.status_code == 200
    assert 'Ближайшая консультация' in booked.text
    assert 'Базы данных' in booked.text
    assert '/bookings/' in booked.text and '#my-bookings' in booked.text


def test_news_delete_removes_read_state(app, client):
    sign_in(client, 'admin')
    post_id = publish(client, 'Временная новость')
    client.get(f'/news/{post_id}')
    assert client.post(f'/news/{post_id}/delete').status_code == 302
    with app.app_context():
        assert db.session.get(NewsPost, post_id) is None
        assert db.session.scalar(select(func.count()).select_from(NewsRead)) == 0
