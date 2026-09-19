from flask_babel import gettext as _, lazy_gettext as _l
import csv
import io
import re
from datetime import date
from zoneinfo import ZoneInfo
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload
from .models import db, User, Group, Department, Subject, Event, Booking, LoginGate, AuditLog, SiteSettings, utcnow
from .auth import roles_required, normalize_login, hash_password, name_parts, check_password_distinct, lock_gate, clear_gate, audit
from .services import integer, cancel_event, cancel_booking
from .lifecycle import trash_users, restore_users
from .notifications import notify

bp = Blueprint('admin', __name__, url_prefix='/admin')

def group_from_form(data, current_id=None):
    name = data.get('new_group', '').strip()
    if name:
        if len(name) > 40:
            raise ValueError(_('Название группы: не более 40 символов.'))
        group = db.session.scalar(select(Group).where(Group.name == name))
        if not group:
            group = Group(name=name, active=True)
            group.admission_year = integer(data.get('admission_year') or group.entry_year, _('Год поступления'), 2000, 2100)
            db.session.add(group)
            db.session.flush()
            audit('catalog_updated', f'group:{group.id}', name)
    else:
        group = db.session.get(Group, integer(data.get('group_id'), _('Группа')))
    if not group or (not group.active and group.id != current_id):
        raise ValueError(_('Выберите действующую группу.'))
    return group

def department_from_form(data, current_id=None):
    name = data.get('new_department', '').strip()
    if name:
        if len(name) > 160:
            raise ValueError(_('Название кафедры: не более 160 символов.'))
        department = db.session.scalar(select(Department).where(Department.name == name))
        if not department:
            department = Department(name=name, active=True)
            db.session.add(department)
            db.session.flush()
            audit('catalog_updated', f'department:{department.id}', name)
    else:
        department = db.session.get(Department, integer(data.get('department_id'), _('Кафедра')))
    if not department or (not department.active and department.id != current_id):
        raise ValueError(_('Выберите действующую кафедру.'))
    return department

def form_catalogs():
    return dict(groups=db.session.scalars(select(Group).order_by(Group.name)).all(),
        departments=db.session.scalars(select(Department).order_by(Department.name)).all())

def live_user(user_id):
    user = db.get_or_404(User, user_id)
    if user.deleted_at:
        abort(404)
    return user

def transliterate_surname(surname):
    alphabet = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя', ['a','b','v','g','d','e','yo','zh','z','i','j','k','l','m','n','o','p','r','s','t','u','f','kh','ts','ch','sh','shch','','y','','e','yu','ya']))
    return ''.join(alphabet.get(c,c) for c in surname.lower()).capitalize()

def create_user(data):
    role = data.get('role')
    login = normalize_login(data.get('login',''))
    if role not in ('student','teacher','admin') or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{1,119}',login):
        raise ValueError(_('Укажите корректные роль и логин.'))
    if role == 'student' and not login.isdigit():
        raise ValueError(_('Номер студенческого должен содержать только цифры.'))
    if role != 'student' and login.isdigit():
        raise ValueError(_('Для сотрудника нужен логин корпоративной почты.'))
    gate = lock_gate(login)
    if db.session.scalar(select(User.id).where(User.login == login, User.role == role)):
        raise ValueError(_('Такая учётная запись уже существует.'))
    user = User(login=login, role=role, active=True, name_locked=role != 'student')
    if role == 'student':
        group = group_from_form(data)
        user.group = group
        user.course = integer(data.get('course'), _('Курс'), 1, 5)
        user.study_mode = data.get('study_mode','full_time')
        if user.study_mode not in ('full_time','part_time') or (user.course == 5 and user.study_mode != 'part_time'):
            raise ValueError(_('Пятый курс доступен только при заочной форме обучения.'))
        user.course = user.current_course
        password = 'Student'
    else:
        user.set_name_parts(*name_parts(data))
        password = (data.get('initial_password') or '').strip()
        if role == 'teacher' and not password:
            password = transliterate_surname(user.last_name)
        if role == 'teacher':
            user.department = department_from_form(data)
        if not password or len(password) > 128 or (role == 'admin' and len(password) < 8):
            raise ValueError(_('Укажите начальный пароль. Для администратора — от 8 до 128 символов.'))
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
    counts['trash'] = db.session.scalar(select(func.count(User.id)).where(User.deleted_at.is_not(None)))
    counts['graduates'] = sum(u.graduation_due for u in db.session.scalars(select(User).where(User.role == 'student', User.deleted_at.is_(None)).options(selectinload(User.group))).all())
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
        flash(_('Демонстрационная учётная запись для этого сценария не найдена или отключена.'), 'error')
        return redirect(url_for('admin.dashboard'))
    session.update(admin_uid=g.user.id, admin_version=g.user.session_version, uid=target.id, version=target.session_version)
    session.permanent = True
    audit('admin_impersonation_started', target.id, f'{g.user.login} → {target.role}:{target.login}')
    db.session.commit()
    flash(_('Открыт сценарий роли. Возврат в админскую панель доступен сверху страницы.'), 'success')
    return redirect(url_for('main.home'))


@bp.post('/maintenance')
@roles_required('admin')
def maintenance():
    setting = db.session.scalar(select(SiteSettings).where(SiteSettings.id == 1).with_for_update())
    enabled = request.form.get('maintenance') == 'on'
    if setting.maintenance != enabled:
        setting.maintenance = enabled
        audit('maintenance_enabled' if enabled else 'maintenance_disabled', 'site')
    db.session.commit()
    flash(_('Режим обслуживания включён.') if enabled else _('Режим обслуживания выключен.'), 'success')
    return redirect(url_for('admin.dashboard'))

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
    flash(_('Вы вернулись в админскую панель.'), 'success')
    return redirect(url_for('admin.dashboard'))

@bp.get('/users')
@roles_required('admin')
def users():
    query = select(User).where(User.deleted_at.is_(None)).options(selectinload(User.group), selectinload(User.department)).order_by(User.role, User.login)
    if request.args.get('role') in ('student','teacher','admin'):
        query = query.where(User.role == request.args['role'])
    term = request.args.get('q','').strip()[:120]
    if term:
        query = query.where(or_(User.login.ilike('%'+term+'%'), User.full_name.ilike('%'+term+'%')))
    for arg, column in [('group', User.group_id), ('department', User.department_id)]:
        if request.args.get(arg, type=int):
            query = query.where(column == request.args.get(arg, type=int))
    if request.args.get('status') == 'graduated':
        marked = [u.id for u in db.session.scalars(query).all() if u.graduation_due]
        query = query.where(User.id.in_(marked))
    elif request.args.get('status') == 'dismissed':
        query = query.where(User.dismissed_on.is_not(None))
    elif request.args.get('status') == 'inactive':
        query = query.where(User.active.is_(False))
    page = db.paginate(query, per_page=50, max_per_page=50, error_out=False)
    gates = {u.login:db.session.get(LoginGate,u.login) for u in page.items}
    return render_template('admin_users.html', page=page, gates=gates, **form_catalogs())

@bp.route('/users/new', methods=['GET','POST'])
@roles_required('admin')
def user_new():
    if request.method == 'POST':
        try:
            user = create_user(request.form)
            db.session.commit()
            flash(_('Учётная запись создана.'), 'success')
            return redirect(url_for('admin.user_edit', user_id=user.id))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
    return render_template('admin_user_form.html', user=None, **form_catalogs())

@bp.route('/users/<int:user_id>', methods=['GET','POST'])
@roles_required('admin')
def user_edit(user_id):
    user = live_user(user_id)
    if request.method == 'POST':
        try:
            gate = lock_gate(user.login)
            user = db.session.execute(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)).scalar_one()
            if user.deleted_at:
                abort(404)
            parts = name_parts(request.form) if any(request.form.get(k, '').strip() for k in ('last_name', 'first_name', 'middle_name', 'full_name')) else ['', '', '']
            name = ' '.join(filter(None, parts))
            if name != (user.full_name or '') or parts != [user.last_name or '', user.first_name or '', user.middle_name or '']:
                reason = request.form.get('reason','').strip()
                if not reason:
                    raise ValueError(_('Укажите основание исправления ФИО, например личное обращение студента.'))
                old = user.full_name or 'Не заполнено'
                if not name:
                    raise ValueError(_('Фамилия и имя обязательны.'))
                user.set_name_parts(*parts)
                user.name_locked = True
                audit('name_corrected', user.id, f'{old} → {user.full_name}; {reason[:300]}')
            if user.role == 'student':
                group = group_from_form(request.form, user.group_id)
                group_id = group.id
                course = integer(request.form.get('course'),_('Курс'),1,5)
                mode = request.form.get('study_mode')
                if mode not in ('full_time','part_time') or (course == 5 and mode != 'part_time'):
                    raise ValueError(_('Пятый курс доступен только заочникам.'))
                user.group, user.study_mode = group, mode
                course = user.current_course if group.entry_year else course
                user.course = course
                future = db.session.scalars(select(Booking).join(Event,Booking.event_id == Event.id).where(Booking.student_id == user.id, Booking.status == 'active', Booking.ends_at > utcnow())).all()
                if any(b.event.allowed_course != course or (b.event.group_id and b.event.group_id != group_id) for b in future):
                    raise ValueError(_('Новая группа или курс не подходят действующим записям. Сначала отмените их в журнале.'))
                user.group_id, user.course, user.study_mode = group_id, course, mode
                user.group = group
            if user.role == 'teacher':
                user.department = department_from_form(request.form, user.department_id)
                dismissed = request.form.get('dismissed_on', '').strip()
                dismissed_on = date.fromisoformat(dismissed) if dismissed else None
                if dismissed_on and dismissed_on > utcnow().astimezone(ZoneInfo(current_app.config['APP_TIMEZONE'])).date():
                    raise ValueError(_('Дата увольнения не может быть в будущем.'))
                if dismissed_on and not user.dismissed_on:
                    for eid in db.session.scalars(select(Event.id).where(Event.teacher_id == user.id, Event.status == 'active', Event.ends_at > utcnow())).all():
                        cancel_event(eid, g.user, 'Увольнение преподавателя')
                    user.active = False
                    user.session_version += 1
                user.dismissed_on = dismissed_on
            audit('user_updated', user.id)
            db.session.commit()
            flash(_('Данные пользователя сохранены.'), 'success')
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
        return redirect(url_for('admin.user_edit', user_id=user.id))
    return render_template('admin_user_form.html', user=user, gate=db.session.get(LoginGate,user.login), **form_catalogs())

@bp.post('/users/<int:user_id>/reset')
@roles_required('admin')
def user_reset(user_id):
    user = live_user(user_id)
    gate = lock_gate(user.login)
    user = db.session.execute(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)).scalar_one()
    if user.deleted_at:
        abort(404)
    user.password_hash = user.initial_password_hash
    user.must_change_password = current_app.config['FORCE_INITIAL_PASSWORD_CHANGE']
    user.session_version += 1
    clear_gate(gate)
    # Logout all roles sharing the locked login; the other role's password is preserved.
    for other in db.session.scalars(select(User).where(User.login == user.login, User.id != user.id)).all():
        other.session_version += 1
    audit('password_reset',user.id,'Начальный пароль восстановлен; блокировка логина снята.')
    notify([user.id], _l('Пароль сброшен администратором'), _l('Восстановлен начальный пароль. При следующем входе смените его в настройках.'), link='/settings')
    db.session.commit()
    flash(_('Начальный пароль восстановлен. Блокировка общего логина снята.'), 'success')
    return redirect(url_for('admin.user_edit',user_id=user.id))

@bp.post('/users/<int:user_id>/toggle')
@roles_required('admin')
def user_toggle(user_id):
    user = live_user(user_id)
    if user.dismissed_on:
        flash(_('Для активации сначала снимите отметку об увольнении.'), 'error')
        return redirect(url_for('admin.user_edit', user_id=user.id))
    if user.id == g.user.id:
        flash(_('Нельзя отключить собственную учётную запись.'), 'error')
        return redirect(url_for('admin.user_edit',user_id=user.id))
    lock_gate(user.login)
    user = db.session.execute(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)).scalar_one()
    if user.deleted_at or user.dismissed_on:
        abort(409)
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
    flash(_('Статус учётной записи изменён.'), 'success')
    return redirect(url_for('admin.user_edit',user_id=user.id))

@bp.post('/users/<int:user_id>/delete')
@roles_required('admin')
def user_delete(user_id):
    try:
        trash_users([user_id], g.user)
        db.session.commit()
        flash(_('Пользователь перенесён в корзину на 10 дней.'), 'success')
        return redirect(url_for('admin.users'))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
        return redirect(url_for('admin.users'))

@bp.post('/users/bulk')
@roles_required('admin')
def users_bulk():
    restoring = request.form.get('action') == 'restore'
    try:
        ids = {integer(value, _('Пользователь')) for value in request.form.getlist('user_ids')}
        if len(ids) > 500:
            raise ValueError(_('За один раз можно выбрать до 500 пользователей.'))
        count = restore_users(ids) if restoring else trash_users(ids, g.user)
        db.session.commit()
        flash(_('Восстановлено: %(value0)s.', value0=count) if restoring else _('Перенесено в корзину на 10 дней: %(value0)s.', value0=count), 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    return redirect(url_for('admin.trash' if restoring else 'admin.users'))

@bp.get('/trash')
@roles_required('admin')
def trash():
    query = select(User).where(User.deleted_at.is_not(None)).order_by(User.deleted_at.desc())
    return render_template('admin_trash.html', page=db.paginate(query, per_page=50, error_out=False))

@bp.route('/catalogs', methods=['GET','POST'])
@roles_required('admin')
def catalogs():
    if request.method == 'POST':
        try:
            kind = request.form.get('kind')
            if kind in ('group','subject','department'):
                model = {'group': Group, 'subject': Subject, 'department': Department}[kind]
                entity = db.get_or_404(model,integer(request.form.get('id'),'ID')) if request.form.get('id') else model()
                if kind == 'department' and request.form.get('delete') == 'yes':
                    if db.session.scalar(select(User.id).where(User.department_id == entity.id).limit(1)):
                        raise ValueError(_('Кафедра связана с пользователями. Перенесите их в другую кафедру или отправьте кафедру в архив.'))
                    audit('catalog_updated', f'department:{entity.id}', 'Кафедра удалена')
                    db.session.delete(entity)
                    db.session.commit()
                    flash(_('Кафедра удалена.'), 'success')
                    return redirect(url_for('admin.catalogs'))
                name = request.form.get('name','').strip()
                if not name or len(name) > (40 if kind == 'group' else 160 if kind == 'department' else 120):
                    raise ValueError(_('Название пустое или слишком длинное.'))
                entity.name = name
                if kind == 'group':
                    entity.admission_year = integer(request.form.get('admission_year') or entity.entry_year, _('Год поступления'), 2000, 2100)
                active = request.form.get('active') == 'yes'
                if entity.id and not active:
                    if kind == 'group' and db.session.scalar(select(User.id).where(User.group_id == entity.id, User.active).limit(1)):
                        raise ValueError(_('В группе есть действующие студенты. Сначала переведите или отключите их.'))
                    if kind == 'subject' and db.session.scalar(select(Event.id).where(Event.subject_id == entity.id,Event.status == 'active',Event.ends_at > utcnow()).limit(1)):
                        raise ValueError(_('По дисциплине есть будущие занятия. Сначала отмените их.'))
                entity.active = active
                db.session.add(entity)
                db.session.flush()
                audit('catalog_updated',f'{kind}:{entity.id}',name)
            elif kind == 'assignment':
                teacher = db.session.execute(select(User).where(User.id == integer(request.form.get('teacher_id'),_('Преподаватель'))).with_for_update()).scalar_one_or_none()
                subject = db.session.get(Subject,integer(request.form.get('subject_id'),_('Дисциплина')))
                if not teacher or teacher.role != 'teacher' or teacher.deleted_at or not subject:
                    raise ValueError(_('Выберите преподавателя и дисциплину.'))
                remove = request.form.get('remove') == 'yes'
                if remove:
                    if db.session.scalar(select(Event.id).where(Event.teacher_id == teacher.id,Event.subject_id == subject.id,Event.status == 'active',Event.ends_at > utcnow()).limit(1)):
                        raise ValueError(_('Сначала отмените будущие занятия по этой дисциплине.'))
                    if subject in teacher.subjects:
                        teacher.subjects.remove(subject)
                elif subject not in teacher.subjects:
                    teacher.subjects.append(subject)
                audit('assignment_removed' if remove else 'assignment_added',teacher.id,subject.name)
            else:
                abort(400)
            db.session.commit()
            flash(_('Справочник обновлён.'), 'success')
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
        return redirect(url_for('admin.catalogs'))
    return render_template('admin_catalogs.html', **form_catalogs(), subjects=db.session.scalars(select(Subject).order_by(Subject.name)).all(), teachers=db.session.scalars(select(User).where(User.role == 'teacher', User.deleted_at.is_(None)).order_by(User.full_name)).all())

@bp.route('/import',methods=['GET','POST'])
@roles_required('admin')
def import_students():
    if request.method == 'POST':
        try:
            file = request.files.get('file')
            if not file:
                raise ValueError(_('Выберите CSV-файл.'))
            text = file.read().decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(text), delimiter=';')
            if reader.fieldnames != ['login','group','course','study_mode']:
                raise ValueError(_('Ожидаются заголовки login;group;course;study_mode в указанном порядке.'))
            count = 0
            for line, row in enumerate(reader,2):
                if count >= 500:
                    raise ValueError(_('За один импорт допускается не более 500 студентов.'))
                if None in row or any(v is None for v in row.values()):
                    raise ValueError(_('Строка %(value0)s: неверное число полей.', value0=line))
                group = db.session.scalar(select(Group).where(Group.name == row['group'].strip(),Group.active))
                if not group:
                    raise ValueError(_('Строка %(value0)s: сначала создайте группу %(value1)s.', value0=line, value1=row["group"]))
                try:
                    create_user(dict(role='student',login=row['login'],group_id=group.id,course=row['course'],study_mode=row['study_mode']))
                except ValueError as exc:
                    raise ValueError(_('Строка %(value0)s: %(value1)s', value0=line, value1=exc))
                count += 1
            if not count:
                raise ValueError(_('В файле нет студентов.'))
            audit('students_imported','csv',f'rows={count}')
            db.session.commit()
            flash(_('Добавлено студентов: %(value0)s. Начальный пароль — Student.', value0=count), 'success')
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
