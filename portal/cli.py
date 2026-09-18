import os
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import click
from sqlalchemy import select
from .models import db, User, Group, Department, Subject, Event, utcnow
from .auth import audit, hash_password, normalize_login, check_password_distinct, lock_gate, clear_gate
from .admin import create_user

DEMO_PERSON_NAME = 'Куренков Егор Евгеньевич'
DEMO_STUDENT_LOGIN = '2300431'
DEMO_TEACHER_LOGIN = 'kurenkov.ee'
DEMO_ADMIN_LOGINS = ('lxrdx', 'flvmming')

def register_commands(app):
    def admin_password():
        password = os.getenv('DEMO_ADMIN_PASSWORD')
        if not password:
            raise click.ClickException('Задайте DEMO_ADMIN_PASSWORD в окружении или .env.')
        if not 8 <= len(password) <= 128:
            raise click.ClickException('DEMO_ADMIN_PASSWORD должен содержать от 8 до 128 символов.')
        return password

    def ensure_admin(login, password, reset_password=True):
        canonical = normalize_login(login)
        gate = lock_gate(canonical)
        user = db.session.scalar(select(User).where(User.login == canonical, User.role == 'admin').with_for_update())
        full_name = f'Администратор {canonical}'
        if user:
            user.full_name = full_name
            user.name_locked = True
            if reset_password:
                check_password_distinct(user, password)
                user.password_hash = hash_password(password)
                user.initial_password_hash = user.password_hash
            user.must_change_password = app.config['FORCE_INITIAL_PASSWORD_CHANGE']
            user.active = True
            user.session_version += 1
            clear_gate(gate)
            audit('admin_console_recovery', user.id, 'Синхронизация демонстрационного администратора')
            return user
        user = create_user({'role': 'admin', 'login': canonical, 'full_name': full_name, 'initial_password': password})
        clear_gate(gate)
        return user

    def seed_demo_records(skip_existing=False):
        if db.session.scalar(select(User.id).limit(1)):
            message = 'База уже содержит пользователей. Демоданные не добавлены, пароли не изменены.'
            if skip_existing:
                click.echo(message)
                return
            raise click.ClickException(message)
        password = admin_password()
        group = Group(name='БПИ-23', admission_year=2023)
        department = Department(name='Учебная кафедра')
        db.session.add_all([group, department])
        db.session.flush()
        student = create_user({'role':'student','login':DEMO_STUDENT_LOGIN,'group_id':group.id,'course':4,'study_mode':'full_time'})
        student.full_name = DEMO_PERSON_NAME
        student.name_locked = True
        teacher = create_user({'role':'teacher','login':DEMO_TEACHER_LOGIN,'full_name':student.full_name,'department_id':department.id})
        for login in DEMO_ADMIN_LOGINS:
            ensure_admin(login, password)
        subjects = [Subject(name='Базы данных'), Subject(name='Веб-программирование')]
        db.session.add_all(subjects)
        teacher.subjects = subjects
        db.session.flush()
        tz = ZoneInfo(app.config['APP_TIMEZONE'])
        for i in range(4):
            day = datetime.now(tz).date() + timedelta(days=4 + i)
            start = datetime.combine(day,time(16,0),tzinfo=tz).astimezone(timezone.utc)
            db.session.add(Event(teacher_id=teacher.id,subject_id=subjects[i%2].id,starts_at=start,ends_at=start+timedelta(hours=1),room='Аудитория 201',capacity=12,allowed_course=4,group_id=group.id,description='Демонстрационное очное занятие. Дисциплину и аудиторию можно изменить до первой записи.'))
        audit('demo_seeded','database','Студент, преподаватель, два администратора и четыре демонстрационных занятия.')
        db.session.commit()
        click.echo('Готово: один студент, один преподаватель и два администратора. См. START_HERE.md.')

    @app.cli.command('seed-demo')
    def seed_demo():
        """Create Egor's three accounts and example lessons once. Never reset existing data."""
        seed_demo_records()

    @app.cli.command('seed-demo-if-empty')
    def seed_demo_if_empty():
        """Create demo records only when the database is empty."""
        seed_demo_records(skip_existing=True)

    @app.cli.command('reconcile-demo-accounts')
    def reconcile_demo_accounts():
        """Ensure the deployed demo admin accounts match the current stand requirements."""
        password = admin_password()
        for login in DEMO_ADMIN_LOGINS:
            ensure_admin(login, password, reset_password=False)
        legacy = db.session.scalar(select(User).where(User.login == DEMO_TEACHER_LOGIN, User.role == 'admin').with_for_update())
        if legacy:
            gate = lock_gate(DEMO_TEACHER_LOGIN)
            clear_gate(gate)
            if legacy.active:
                legacy.active = False
                legacy.session_version += 1
                audit('user_deactivated', legacy.id, 'Логин kurenkov.ee оставлен только для преподавателя')
        db.session.commit()
        click.echo('Готово: администраторы lxrdx и flvmming активны; kurenkov.ee остается преподавателем.')

    @app.cli.command('maintenance')
    def maintenance():
        """Deliver scheduled notifications and permanently purge expired trash."""
        from .lifecycle import run_maintenance
        count = run_maintenance()
        click.echo(f'Maintenance complete; expired accounts removed: {count or 0}')

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
