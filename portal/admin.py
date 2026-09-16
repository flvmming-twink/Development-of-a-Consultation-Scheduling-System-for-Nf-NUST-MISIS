import csv
import io
import re
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import select, func, or_, delete, update
from .models import db, User, Group, Subject, Event, Booking, LoginGate, AuditLog, teacher_subject, utcnow
from .auth import roles_required, normalize_login, hash_password, validate_name, check_password_distinct, lock_gate, clear_gate, audit
from .services import integer, cancel_event, cancel_booking

bp = Blueprint('admin', __name__, url_prefix='/admin')

def transliterate_surname(surname):
    alphabet = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя', ['a','b','v','g','d','e','yo','zh','z','i','j','k','l','m','n','o','p','r','s','t','u','f','kh','ts','ch','sh','shch','','y','','e','yu','ya']))
    return ''.join(alphabet.get(c,c) for c in surname.lower()).capitalize()

def create_user(data):
    role = data.get('role')
    login = normalize_login(data.get('login',''))
    if role not in ('student','teacher','admin') or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{1,119}',login):
        raise ValueError('Укажите корректные роль и логин.')
    if role == 'student' and not login.isdigit():
        raise ValueError('Номер студенческого должен содержать только цифры.')
    if role != 'student' and login.isdigit():
        raise ValueError('Для сотрудника нужен логин корпоративной почты.')
    gate = lock_gate(login)
    if db.session.scalar(select(User.id).where(User.login == login, User.role == role)):
        raise ValueError('Такая учётная запись уже существует.')
    user = User(login=login, role=role, active=True, name_locked=role != 'student')
    if role == 'student':
        group_id = integer(data.get('group_id'), 'Группа')
        group = db.session.get(Group, group_id)
        if not group or not group.active:
            raise ValueError('Выберите действующую группу.')
        user.group_id = group_id
        user.course = integer(data.get('course'), 'Курс', 1, 5)
        user.study_mode = data.get('study_mode','full_time')
        if user.study_mode not in ('full_time','part_time') or (user.course == 5 and user.study_mode != 'part_time'):
            raise ValueError('Пятый курс доступен только при заочной форме обучения.')
        password = 'Student'
    else:
        user.full_name = validate_name(data.get('full_name',''))
        password = (data.get('initial_password') or '').strip()
        if role == 'teacher' and not password:
            password = transliterate_surname(user.full_name.split()[0])
        if not password or len(password) > 128 or (role == 'admin' and len(password) < 8):
            raise ValueError('Укажите начальный пароль. Для администратора — от 8 до 128 символов.')
    with db.session.no_autoflush:
        check_password_distinct(user, password)
    user.password_hash = hash_password(password)
    user.initial_password_hash = user.password_hash
    user.must_change_password = current_app.config['FORCE_INITIAL_PASSWORD_CHANGE']
    db.session.add(user)
    db.session.flush()
    audit('user_created', user.id, f'{user.role}; {user.login}')
    return user

@bp.get('')
@bp.get('/')
@roles_required('admin')
def dashboard():
    counts = {role:db.session.scalar(select(func.count(User.id)).where(User.role == role, User.active)) for role in ('student','teacher','admin')}
    counts['events'] = db.session.scalar(select(func.count(Event.id)).where(Event.status == 'active', Event.ends_at > utcnow()))
    locked = db.session.scalars(select(LoginGate).where(or_(LoginGate.permanent, LoginGate.locked_until > utcnow())).order_by(LoginGate.updated_at.desc()).limit(20)).all()
    demo_student = db.session.scalar(select(User).where(User.login == '2300431', User.role == 'student'))
    demo_teacher = db.session.scalar(select(User).where(User.login == 'kurenkov.ee', User.role == 'teacher'))
    return render_template('admin_dashboard.html', counts=counts, locked=locked, logs=db.session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(8)).all(), demo_student=demo_student, demo_teacher=demo_teacher)

@bp.post('/impersonate/<role>')
@roles_required('admin')
def impersonate(role):
    targets = {'student': '2300431', 'teacher': 'kurenkov.ee'}
    if role not in targets:
        abort(404)
    target = db.session.scalar(select(User).where(User.login == targets[role], User.role == role))
    if not target or not target.active:
        flash('Демонстрационная учётная запись для этого сценария не найдена или отключена.', 'error')
        return redirect(url_for('admin.dashboard'))
    session.update(admin_uid=g.user.id, admin_version=g.user.session_version, uid=target.id, version=target.session_version)
    session.permanent = True
    audit('admin_impersonation_started', target.id, f'{g.user.login} → {target.role}:{target.login}')
    db.session.commit()
    flash('Открыт сценарий роли. Возврат в админскую панель доступен сверху страницы.', 'success')
    return redirect(url_for('main.home'))

@bp.post('/stop-impersonation')
def stop_impersonation():
    if not getattr(g, 'admin_user', None):
        abort(403)
    admin = g.admin_user
    session.clear()
    session.update(uid=admin.id, version=admin.session_version)
    session.permanent = True
    audit('admin_impersonation_stopped', admin.id, actor_id=admin.id)
    db.session.commit()
    flash('Вы вернулись в админскую панель.', 'success')
    return redirect(url_for('admin.dashboard'))

@bp.get('/users')
@roles_required('admin')
def users():
    query = select(User).order_by(User.role, User.login)
    if request.args.get('role') in ('student','teacher','admin'):
        query = query.where(User.role == request.args['role'])
    term = request.args.get('q','').strip()[:120]
    if term:
        query = query.where(or_(User.login.ilike('%'+term+'%'), User.full_name.ilike('%'+term+'%')))
    page = db.paginate(query, per_page=25, max_per_page=25, error_out=False)
    gates = {u.login:db.session.get(LoginGate,u.login) for u in page.items}
    return render_template('admin_users.html', page=page, gates=gates)

@bp.route('/users/new', methods=['GET','POST'])
@roles_required('admin')
def user_new():
    if request.method == 'POST':
        try:
            user = create_user(request.form)
            db.session.commit()
            flash('Учётная запись создана.', 'success')
            return redirect(url_for('admin.user_edit', user_id=user.id))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
    return render_template('admin_user_form.html', user=None, groups=db.session.scalars(select(Group).where(Group.active).order_by(Group.name)).all())

@bp.route('/users/<int:user_id>', methods=['GET','POST'])
@roles_required('admin')
def user_edit(user_id):
    user = db.get_or_404(User, user_id)
    if request.method == 'POST':
        try:
            gate = lock_gate(user.login)
            user = db.session.execute(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)).scalar_one()
            name = request.form.get('full_name','').strip()
            if name != (user.full_name or ''):
                reason = request.form.get('reason','').strip()
                if not reason:
                    raise ValueError('Укажите основание исправления ФИО, например личное обращение студента.')
                old = user.full_name or 'Не заполнено'
                user.full_name = validate_name(name)
                user.name_locked = True
                audit('name_corrected', user.id, f'{old} → {user.full_name}; {reason[:300]}')
            if user.role == 'student':
                group_id = integer(request.form.get('group_id'), 'Группа')
                group = db.session.get(Group,group_id)
                if not group or (not group.active and group.id != user.group_id):
                    raise ValueError('Выберите действующую группу.')
                course = integer(request.form.get('course'),'Курс',1,5)
                mode = request.form.get('study_mode')
                if mode not in ('full_time','part_time') or (course == 5 and mode != 'part_time'):
                    raise ValueError('Пятый курс доступен только заочникам.')
                future = db.session.scalars(select(Booking).join(Event,Booking.event_id == Event.id).where(Booking.student_id == user.id, Booking.status == 'active', Booking.ends_at > utcnow())).all()
                if any(b.event.allowed_course != course or (b.event.group_id and b.event.group_id != group_id) for b in future):
                    raise ValueError('Новая группа или курс не подходят действующим записям. Сначала отмените их в журнале.')
                user.group_id, user.course, user.study_mode = group_id, course, mode
            audit('user_updated', user.id)
            db.session.commit()
            flash('Данные пользователя сохранены.', 'success')
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
        return redirect(url_for('admin.user_edit', user_id=user.id))
    return render_template('admin_user_form.html', user=user, groups=db.session.scalars(select(Group).order_by(Group.name)).all(), gate=db.session.get(LoginGate,user.login))

@bp.post('/users/<int:user_id>/reset')
@roles_required('admin')
def user_reset(user_id):
    user = db.get_or_404(User,user_id)
    gate = lock_gate(user.login)
    user = db.session.execute(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)).scalar_one()
    user.password_hash = user.initial_password_hash
    user.must_change_password = current_app.config['FORCE_INITIAL_PASSWORD_CHANGE']
    user.session_version += 1
    clear_gate(gate)
    # Logout all roles sharing the locked login; the other role's password is preserved.
    for other in db.session.scalars(select(User).where(User.login == user.login, User.id != user.id)).all():
        other.session_version += 1
    audit('password_reset',user.id,'Начальный пароль восстановлен; блокировка логина снята.')
    db.session.commit()
    flash('Начальный пароль восстановлен. Блокировка общего логина снята.', 'success')
    return redirect(url_for('admin.user_edit',user_id=user.id))

@bp.post('/users/<int:user_id>/toggle')
@roles_required('admin')
def user_toggle(user_id):
    user = db.get_or_404(User,user_id)
    if user.id == g.user.id:
        flash('Нельзя отключить собственную учётную запись.', 'error')
        return redirect(url_for('admin.user_edit',user_id=user.id))
    lock_gate(user.login)
    user = db.session.execute(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)).scalar_one()
    if user.active:
        if user.role == 'teacher':
            for eid in db.session.scalars(select(Event.id).where(Event.teacher_id == user.id,Event.status == 'active',Event.ends_at > utcnow())).all():
                cancel_event(eid,g.user,'Отключение преподавателя администратором')
        if user.role == 'student':
            for bid in db.session.scalars(select(Booking.id).where(Booking.student_id == user.id,Booking.status == 'active',Booking.ends_at > utcnow())).all():
                cancel_booking(bid,g.user,'Отключение студента администратором')
    user.active = not user.active
    user.session_version += 1
    audit('user_activated' if user.active else 'user_deactivated',user.id)
    db.session.commit()
    flash('Статус учётной записи изменён.', 'success')
    return redirect(url_for('admin.user_edit',user_id=user.id))

@bp.post('/users/<int:user_id>/delete')
@roles_required('admin')
def user_delete(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == g.user.id:
        flash('Нельзя удалить собственную учётную запись.', 'error')
        return redirect(url_for('admin.user_edit', user_id=user.id))
    try:
        gate = lock_gate(user.login)
        user = db.session.execute(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)).scalar_one()
        if user.role == 'teacher' and db.session.scalar(select(Event.id).where(Event.teacher_id == user.id).limit(1)):
            raise ValueError('Нельзя удалить преподавателя, у которого уже есть занятия. Отключите аккаунт или отмените занятия.')
        if user.role == 'student' and db.session.scalar(select(Booking.id).where(Booking.student_id == user.id).limit(1)):
            raise ValueError('Нельзя удалить студента, у которого уже есть записи. Отключите аккаунт или отмените записи.')
        login, role = user.login, user.role
        db.session.execute(delete(teacher_subject).where(teacher_subject.c.teacher_id == user.id))
        db.session.execute(update(AuditLog).where(AuditLog.actor_id == user.id).values(actor_id=None))
        db.session.delete(user)
        db.session.flush()
        if db.session.scalar(select(User.id).where(User.login == login).limit(1)):
            clear_gate(gate)
        else:
            db.session.delete(gate)
        audit('user_deleted', f'{role}:{login}')
        db.session.commit()
        flash('Учётная запись удалена.', 'success')
        return redirect(url_for('admin.users'))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
        return redirect(url_for('admin.user_edit', user_id=user_id))

@bp.route('/catalogs', methods=['GET','POST'])
@roles_required('admin')
def catalogs():
    if request.method == 'POST':
        try:
            kind = request.form.get('kind')
            if kind in ('group','subject'):
                model = Group if kind == 'group' else Subject
                entity = db.get_or_404(model,integer(request.form.get('id'),'ID')) if request.form.get('id') else model()
                name = request.form.get('name','').strip()
                if not name or len(name) > (40 if kind == 'group' else 120):
                    raise ValueError('Название пустое или слишком длинное.')
                entity.name = name
                active = request.form.get('active') == 'yes'
                if entity.id and not active:
                    if kind == 'group' and db.session.scalar(select(User.id).where(User.group_id == entity.id, User.active).limit(1)):
                        raise ValueError('В группе есть действующие студенты. Сначала переведите или отключите их.')
                    if kind == 'subject' and db.session.scalar(select(Event.id).where(Event.subject_id == entity.id,Event.status == 'active',Event.ends_at > utcnow()).limit(1)):
                        raise ValueError('По дисциплине есть будущие занятия. Сначала отмените их.')
                entity.active = active
                db.session.add(entity)
                db.session.flush()
                audit('catalog_updated',f'{kind}:{entity.id}',name)
            elif kind == 'assignment':
                teacher = db.session.execute(select(User).where(User.id == integer(request.form.get('teacher_id'),'Преподаватель')).with_for_update()).scalar_one_or_none()
                subject = db.session.get(Subject,integer(request.form.get('subject_id'),'Дисциплина'))
                if not teacher or teacher.role != 'teacher' or not subject:
                    raise ValueError('Выберите преподавателя и дисциплину.')
                remove = request.form.get('remove') == 'yes'
                if remove:
                    if db.session.scalar(select(Event.id).where(Event.teacher_id == teacher.id,Event.subject_id == subject.id,Event.status == 'active',Event.ends_at > utcnow()).limit(1)):
                        raise ValueError('Сначала отмените будущие занятия по этой дисциплине.')
                    if subject in teacher.subjects:
                        teacher.subjects.remove(subject)
                elif subject not in teacher.subjects:
                    teacher.subjects.append(subject)
                audit('assignment_removed' if remove else 'assignment_added',teacher.id,subject.name)
            else:
                abort(400)
            db.session.commit()
            flash('Справочник обновлён.', 'success')
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
        return redirect(url_for('admin.catalogs'))
    return render_template('admin_catalogs.html', groups=db.session.scalars(select(Group).order_by(Group.name)).all(), subjects=db.session.scalars(select(Subject).order_by(Subject.name)).all(), teachers=db.session.scalars(select(User).where(User.role == 'teacher').order_by(User.full_name)).all())

@bp.route('/import',methods=['GET','POST'])
@roles_required('admin')
def import_students():
    if request.method == 'POST':
        try:
            file = request.files.get('file')
            if not file:
                raise ValueError('Выберите CSV-файл.')
            text = file.read().decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(text), delimiter=';')
            if reader.fieldnames != ['login','group','course','study_mode']:
                raise ValueError('Ожидаются заголовки login;group;course;study_mode в указанном порядке.')
            count = 0
            for line, row in enumerate(reader,2):
                if count >= 500:
                    raise ValueError('За один импорт допускается не более 500 студентов.')
                if None in row or any(v is None for v in row.values()):
                    raise ValueError(f'Строка {line}: неверное число полей.')
                group = db.session.scalar(select(Group).where(Group.name == row['group'].strip(),Group.active))
                if not group:
                    raise ValueError(f'Строка {line}: сначала создайте группу {row["group"]}.')
                try:
                    create_user(dict(role='student',login=row['login'],group_id=group.id,course=row['course'],study_mode=row['study_mode']))
                except ValueError as exc:
                    raise ValueError(f'Строка {line}: {exc}')
                count += 1
            if not count:
                raise ValueError('В файле нет студентов.')
            audit('students_imported','csv',f'rows={count}')
            db.session.commit()
            flash(f'Добавлено студентов: {count}. Начальный пароль — Student.', 'success')
            return redirect(url_for('admin.users'))
        except (ValueError, UnicodeError, csv.Error) as exc:
            db.session.rollback()
            flash(str(exc), 'error')
    return render_template('admin_import.html')

@bp.get('/events')
@roles_required('admin')
def events():
    query = select(Event).order_by(Event.starts_at.desc())
    page = db.paginate(query,per_page=20,max_per_page=20,error_out=False)
    return render_template('admin_events.html',page=page)

@bp.get('/audit')
@roles_required('admin')
def logs():
    query = select(AuditLog).order_by(AuditLog.id.desc())
    if request.args.get('action'):
        query = query.where(AuditLog.action == request.args['action'][:60])
    page = db.paginate(query,per_page=30,max_per_page=30,error_out=False)
    return render_template('admin_audit.html',page=page)
