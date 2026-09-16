from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import current_app
from sqlalchemy import select, func
from .models import db, User, Group, Subject, Event, Booking, utcnow
from .auth import audit

def integer(value, label, minimum=1, maximum=2**31-1):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label}: введите целое число.')
    if not minimum <= result <= maximum:
        raise ValueError(f'{label}: допустимы значения от {minimum} до {maximum}.')
    return result

def local_datetime(value):
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is not None:
            raise ValueError()
        return dt.replace(tzinfo=ZoneInfo(current_app.config['APP_TIMEZONE'])).astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise ValueError('Укажите корректную дату и время занятия.')

def booking_problem(event, student, now=None):
    now = now or utcnow()
    if event.status != 'active' or not event.teacher.active or not event.subject.active:
        return 'Запись на занятие закрыта.'
    if not student.full_name:
        return 'Сначала укажите ФИО в настройках.'
    if event.allowed_course != student.course or (event.group_id and event.group_id != student.group_id):
        return 'Занятие предназначено для другого курса или группы.'
    if event.starts_at < now + timedelta(hours=current_app.config['BOOKING_LEAD_HOURS']):
        return f"Запись закрывается за {current_app.config['BOOKING_LEAD_HOURS']} часов до начала."
    if any(b.student_id == student.id and b.status == 'active' for b in event.bookings):
        return 'Вы уже записаны.'
    if event.occupied >= event.capacity:
        return 'Свободных мест нет.'
    return None

def book_event(student_id, event_id):
    student = db.session.execute(select(User).where(User.id == student_id).with_for_update().execution_options(populate_existing=True)).scalar_one()
    event = db.session.execute(select(Event).where(Event.id == event_id).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if not event or not student.active or student.role != 'student':
        raise ValueError('Занятие или студент недоступны.')
    db.session.expire(event, ['bookings'])
    problem = booking_problem(event, student)
    if problem:
        raise ValueError(problem)
    overlap = db.session.scalar(select(Booking.id).where(Booking.student_id == student.id, Booking.status == 'active', Booking.starts_at < event.ends_at, Booking.ends_at > event.starts_at).limit(1))
    if overlap:
        raise ValueError('В это время у вас уже есть занятие. Выберите другой интервал.')
    booking = db.session.scalar(select(Booking).where(Booking.event_id == event.id, Booking.student_id == student.id))
    if booking:
        booking.status = 'active'
        booking.attendance = 'pending'
        booking.created_at = utcnow()
    else:
        booking = Booking(event_id=event.id, student_id=student.id, starts_at=event.starts_at, ends_at=event.ends_at)
        db.session.add(booking)
    audit('booking_created', event.id, f'student_id={student.id}')
    db.session.flush()
    return booking

def cancel_booking(booking_id, actor, reason=''):
    booking = db.session.get(Booking, booking_id)
    if not booking or (actor.role != 'admin' and booking.student_id != actor.id):
        raise ValueError('Запись недоступна.')
    db.session.execute(select(User).where(User.id == booking.student_id).with_for_update())
    db.session.execute(select(Event).where(Event.id == booking.event_id).with_for_update())
    db.session.refresh(booking)
    if actor.role == 'student' and booking.starts_at <= utcnow():
        raise ValueError('После начала занятия отмена доступна только администратору.')
    if booking.status == 'active':
        booking.status = 'cancelled'
        audit('booking_cancelled', booking.id, reason)

def cancel_event(event_id, actor, reason):
    event = db.session.execute(select(Event).where(Event.id == event_id).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if not event or (actor.role != 'admin' and event.teacher_id != actor.id):
        raise ValueError('Занятие недоступно.')
    if not reason.strip():
        raise ValueError('Укажите причину отмены.')
    event.status = 'cancelled'
    for booking in db.session.scalars(select(Booking).where(Booking.event_id == event.id, Booking.status == 'active')).all():
        booking.status = 'cancelled'
    audit('event_cancelled', event.id, reason[:500])

def save_event(form, actor, event_id=None):
    teacher_id = actor.id if actor.role == 'teacher' else integer(form.get('teacher_id'), 'Преподаватель')
    teacher = db.session.execute(select(User).where(User.id == teacher_id).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if not teacher or teacher.role != 'teacher' or not teacher.active:
        raise ValueError('Выберите действующего преподавателя.')
    subject_id = integer(form.get('subject_id'), 'Дисциплина')
    if subject_id not in [s.id for s in teacher.subjects if s.active]:
        raise ValueError('Эта дисциплина не закреплена за преподавателем.')
    starts_at = local_datetime(form.get('starts_at'))
    ends_at = local_datetime(form.get('ends_at'))
    if starts_at <= utcnow() or ends_at <= starts_at or ends_at - starts_at > timedelta(hours=12):
        raise ValueError('Начало должно быть в будущем, длительность — от 1 минуты до 12 часов.')
    room = form.get('room', '').strip()
    if not room or len(room) > 80:
        raise ValueError('Укажите аудиторию или место проведения, до 80 символов.')
    capacity = integer(form.get('capacity'), 'Число мест', 1, 500)
    course = integer(form.get('allowed_course'), 'Курс', 1, 5)
    group_id = integer(form.get('group_id'), 'Группа') if form.get('group_id') else None
    group = db.session.get(Group, group_id) if group_id else None
    if group_id and (not group or not group.active):
        raise ValueError('Выберите действующую группу.')
    description = form.get('description','').strip()
    if len(description) > 1200:
        raise ValueError('Описание: не более 1200 символов.')
    event = None
    if event_id:
        event = db.session.execute(select(Event).where(Event.id == event_id).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if not event or event.status != 'active' or (actor.role != 'admin' and event.teacher_id != actor.id):
            raise ValueError('Занятие недоступно для редактирования.')
        # Historical bookings also retain the original interval via a composite FK.
        has_bookings = db.session.scalar(select(Booking.id).where(Booking.event_id == event.id).limit(1))
        if has_bookings and (starts_at != event.starts_at or ends_at != event.ends_at or teacher_id != event.teacher_id or subject_id != event.subject_id or course != event.allowed_course or group_id != event.group_id):
            raise ValueError('На занятие уже записывались студенты. Для смены времени, дисциплины или аудитории слушателей отмените его и создайте новое.')
        if capacity < event.occupied:
            raise ValueError('Число мест меньше количества записанных студентов.')
    overlap = db.session.scalar(select(Event.id).where(Event.teacher_id == teacher_id, Event.status == 'active', Event.starts_at < ends_at, Event.ends_at > starts_at, Event.id != (event_id or 0)).limit(1))
    if overlap:
        raise ValueError('У преподавателя уже есть занятие, пересекающееся с этим временем.')
    if not event:
        event = Event()
        db.session.add(event)
    for key, value in dict(teacher_id=teacher_id, subject_id=subject_id, starts_at=starts_at, ends_at=ends_at, room=room, capacity=capacity, allowed_course=course, group_id=group_id, description=description).items():
        setattr(event, key, value)
    db.session.flush()
    audit('event_updated' if event_id else 'event_created', event.id)
    return event
