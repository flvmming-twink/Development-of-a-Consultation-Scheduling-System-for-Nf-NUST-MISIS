import os
from datetime import timedelta
from pathlib import Path
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from portal import create_app
from portal.models import db, Group, User, Subject, Event, utcnow
from portal.admin import create_user

@pytest.fixture()
def app():
    url = os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set TEST_DATABASE_URL to a disposable PostgreSQL database ending in _test.')
    if not (make_url(url).database or '').endswith('_test') and os.getenv('ALLOW_DISPOSABLE_TEST_DB') != '1':
        raise RuntimeError('Tests erase data. The database name must end in _test.')
    app = create_app({'TESTING':True,'SECRET_KEY':'unit-test-only-secret','SQLALCHEMY_DATABASE_URI':url,'WTF_CSRF_ENABLED':False,'PASSWORD_HASH_METHOD':'pbkdf2:sha256:1000','FORCE_INITIAL_PASSWORD_CHANGE':False})
    with app.app_context():
        # Separate disposable database, never the user's development database.
        db.session.execute(text('CREATE EXTENSION IF NOT EXISTS btree_gist'))
        db.session.commit()
        db.metadata.drop_all(db.engine)
        schema = (Path(__file__).resolve().parents[1] / 'migrations/0001_schema.sql').read_text(encoding='utf-8')
        db.session.execute(text(schema))
        db.session.commit()
        group = Group(name='БПИ-23')
        second_group = Group(name='БПИ-24')
        db.session.add_all([group,second_group])
        db.session.flush()
        student = create_user(dict(role='student',login='2300431',group_id=group.id,course=4,study_mode='full_time'))
        student.full_name='Куренков Егор Евгеньевич'
        student.name_locked=True
        teacher = create_user(dict(role='teacher',login='kurenkov.ee',full_name=student.full_name))
        admin = create_user(dict(role='admin',login='lxrdx',full_name='Администратор lxrdx',initial_password='Admin!1234'))
        second = create_user(dict(role='teacher',login='second.ee',full_name='Иванов Иван Иванович'))
        subject=Subject(name='Базы данных')
        other=Subject(name='Веб-программирование')
        teacher.subjects=[subject,other]
        second.subjects=[subject,other]
        db.session.add_all([subject,other])
        db.session.flush()
        start=(utcnow()+timedelta(days=7)).replace(second=0,microsecond=0)
        first=Event(teacher_id=teacher.id,subject_id=subject.id,starts_at=start,ends_at=start+timedelta(hours=1),capacity=2,room='201',allowed_course=4,group_id=group.id)
        overlap=Event(teacher_id=second.id,subject_id=other.id,starts_at=start+timedelta(minutes=30),ends_at=start+timedelta(hours=2),capacity=2,room='202',allowed_course=4)
        adjacent=Event(teacher_id=teacher.id,subject_id=other.id,starts_at=start+timedelta(hours=1),ends_at=start+timedelta(hours=2),capacity=2,room='201',allowed_course=4)
        db.session.add_all([first,overlap,adjacent])
        db.session.commit()
        app.config['IDS']={'student':student.id,'teacher':teacher.id,'admin':admin.id,'second':second.id,'first':first.id,'overlap':overlap.id,'adjacent':adjacent.id,'group':group.id,'other_group':second_group.id,'subject':subject.id,'other_subject':other.id}
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()

@pytest.fixture()
def client(app):
    return app.test_client()

def sign_in(client,role='student'):
    login,password={'student':('2300431','Student'),'teacher':('kurenkov.ee','Kurenkov'),'admin':('lxrdx','Admin!1234'),'second':('second.ee','Ivanov')}[role]
    return client.post('/login',data={'login':login,'password':password})
