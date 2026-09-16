import os
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import click
from sqlalchemy import select
from .models import db, User, Group, Subject, Event, utcnow
from .auth import audit, hash_password, normalize_login, check_password_distinct, lock_gate, clear_gate
from .admin import create_user

def register_commands(app):
    def seed_demo_records(skip_existing=False):
        if db.session.scalar(select(User.id).limit(1)):
            message = 'База уже содержит пользователей. Демоданные не добавлены, пароли не изменены.'
            if skip_existing:
                click.echo(message)
                return
            raise click.ClickException(message)
        password = os.getenv('DEMO_ADMIN_PASSWORD')
        if not password:
            raise click.ClickException('Задайте DEMO_ADMIN_PASSWORD в .env.')
        group = Group(name='БПИ-23')
        db.session.add(group)
        db.session.flush()
        student = create_user({'role':'student','login':'2300431','group_id':group.id,'course':4,'study_mode':'full_time'})
        student.full_name = 'Куренков Егор Евгеньевич'
        student.name_locked = True
        teacher = create_user({'role':'teacher','login':'kurenkov.ee','full_name':student.full_name})
        create_user({'role':'admin','login':'kurenkov.ee','full_name':student.full_name,'initial_password':password})
        subjects = [Subject(name='Базы данных'), Subject(name='Веб-программирование')]
        db.session.add_all(subjects)
        teacher.subjects = subjects
        db.session.flush()
        tz = ZoneInfo(app.config['APP_TIMEZONE'])
        for i in range(4):
            day = datetime.now(tz).date() + timedelta(days=4 + i)
            start = datetime.combine(day,time(16,0),tzinfo=tz).astimezone(timezone.utc)
            db.session.add(Event(teacher_id=teacher.id,subject_id=subjects[i%2].id,starts_at=start,ends_at=start+timedelta(hours=1),room='Аудитория 201',capacity=12,allowed_course=4,group_id=group.id,description='Демонстрационное очное занятие. Дисциплину и аудиторию можно изменить до первой записи.'))
        audit('demo_seeded','database','Три учётные записи Егора; четыре демонстрационных занятия.')
        db.session.commit()
        click.echo('Готово: один студент, один преподаватель и один администратор. См. START_HERE.md.')

    @app.cli.command('seed-demo')
    def seed_demo():
        """Create Egor's three accounts and example lessons once. Never reset existing data."""
        seed_demo_records()

    @app.cli.command('seed-demo-if-empty')
    def seed_demo_if_empty():
        """Create demo records only when the database is empty."""
        seed_demo_records(skip_existing=True)

    @app.cli.command('recover-admin')
    @click.option('--login',required=True,help='Логин администратора')
    def recover_admin(login):
        """Emergency recovery through the server console when all admins are locked."""
        canonical = normalize_login(login)
        gate = lock_gate(canonical)
        user = db.session.scalar(select(User).where(User.login == canonical,User.role == 'admin').with_for_update())
        if not user:
            db.session.rollback()
            raise click.ClickException('Администратор с таким логином не найден.')
        password = click.prompt('Новый пароль администратора (от 8 символов)',hide_input=True,confirmation_prompt=True)
        if not 8 <= len(password) <= 128:
            raise click.ClickException('Нужно от 8 до 128 символов.')
        try:
            check_password_distinct(user,password)
        except ValueError as exc:
            raise click.ClickException(str(exc))
        user.password_hash = hash_password(password)
        user.initial_password_hash = user.password_hash
        user.must_change_password = False
        user.active = True
        for account in db.session.scalars(select(User).where(User.login == canonical)).all():
            account.session_version += 1
        clear_gate(gate)
        audit('admin_console_recovery',user.id,'Восстановление из консоли сервера')
        db.session.commit()
        click.echo('Администратор восстановлен. Используйте новый пароль.')
