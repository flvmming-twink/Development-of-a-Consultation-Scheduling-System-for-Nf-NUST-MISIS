from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo
from flask import current_app
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, UniqueConstraint, ForeignKeyConstraint, text, func
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import validates

db = SQLAlchemy()

def utcnow():
    return datetime.now(timezone.utc)

teacher_subject = db.Table('teacher_subject',
    db.Column('teacher_id', db.Integer, db.ForeignKey('users.id'), primary_key=True),
    db.Column('subject_id', db.Integer, db.ForeignKey('subjects.id'), primary_key=True))

class Group(db.Model):
    __tablename__ = 'groups'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(40), unique=True, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    admission_year = db.Column(db.Integer)

    @property
    def entry_year(self):
        match = re.search(r'(\d{4}|\d{2})$', self.name)
        return self.admission_year or (int(match[1]) + (2000 if len(match[1]) == 2 else 0) if match else None)

class Department(db.Model):
    __tablename__ = 'departments'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), unique=True, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    login = db.Column(db.String(120), nullable=False, index=True)
    role = db.Column(db.String(12), nullable=False)
    full_name = db.Column(db.String(160))
    last_name = db.Column(db.String(80))
    first_name = db.Column(db.String(80))
    middle_name = db.Column(db.String(80))
    name_locked = db.Column(db.Boolean, nullable=False, default=False)
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'))
    course = db.Column(db.Integer)
    study_mode = db.Column(db.String(12))
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'))
    dismissed_on = db.Column(db.Date)
    deleted_at = db.Column(db.DateTime(timezone=True), index=True)
    active_before_delete = db.Column(db.Boolean)
    password_hash = db.Column(db.Text, nullable=False)
    initial_password_hash = db.Column(db.Text, nullable=False)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    theme = db.Column(db.String(8), nullable=False, default='light')
    language = db.Column(db.String(2), nullable=False, default='ru')
    event_view = db.Column(db.String(8), nullable=False, default='grid')
    session_version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    group = db.relationship(Group)
    department = db.relationship(Department)
    subjects = db.relationship('Subject', secondary=teacher_subject, back_populates='teachers')
    avatar = db.relationship('UserAvatar', uselist=False, cascade='all, delete-orphan', passive_deletes=True)
    __table_args__ = (
        UniqueConstraint('login', 'role', name='uq_user_login_role'),
        CheckConstraint("role IN ('student','teacher','admin')", name='ck_user_role'),
        CheckConstraint("theme IN ('light','dark')", name='ck_theme'),
        CheckConstraint("language IN ('ru','en')", name='ck_user_language'),
        CheckConstraint("event_view IN ('grid','list')", name='ck_user_event_view'),
        CheckConstraint("role != 'student' OR (group_id IS NOT NULL AND course IS NOT NULL AND course BETWEEN 1 AND 5 AND study_mode IS NOT NULL AND study_mode IN ('full_time','part_time') AND (course < 5 OR study_mode = 'part_time'))", name='ck_student_profile'),)

    @validates('full_name')
    def split_legacy_name(self, key, value):
        parts = (value or '').split(maxsplit=2)
        self.last_name, self.first_name, self.middle_name = (parts + [None] * 3)[:3]
        return value

    def set_name_parts(self, last_name, first_name, middle_name=''):
        self.full_name = ' '.join(filter(None, (last_name, first_name, middle_name)))
        self.last_name, self.first_name, self.middle_name = last_name, first_name, middle_name or None

    @property
    def personal_address(self):
        return ' '.join(filter(None, (self.first_name, self.middle_name)))

    def academic_status(self, now=None):
        if self.role != 'student' or not self.group or not self.group.entry_year:
            return self.course, False
        today = (now or utcnow()).astimezone(ZoneInfo(current_app.config['APP_TIMEZONE'])).date()
        year = today.year - (today.month < 9)
        course = year - self.group.entry_year + 1
        duration = 5 if self.study_mode == 'part_time' else 4
        return max(1, min(course, duration)), course > duration

    @property
    def current_course(self):
        return self.academic_status()[0]

    @property
    def graduation_due(self):
        return self.academic_status()[1]

    @property
    def purge_at(self):
        return self.deleted_at + timedelta(days=10) if self.deleted_at else None

class LoginGate(db.Model):
    __tablename__ = 'login_gates'
    login = db.Column(db.String(120), primary_key=True)
    failures = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime(timezone=True))
    permanent = db.Column(db.Boolean, nullable=False, default=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

class Subject(db.Model):
    __tablename__ = 'subjects'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    teachers = db.relationship(User, secondary=teacher_subject, back_populates='subjects')

class Event(db.Model):
    __tablename__ = 'events'
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id'), nullable=False)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=False)
    room = db.Column(db.String(80), nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    allowed_course = db.Column(db.Integer, nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'))
    description = db.Column(db.String(1200), nullable=False, default='')
    status = db.Column(db.String(12), nullable=False, default='active')
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    registration_opens_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    teacher = db.relationship(User)
    subject = db.relationship(Subject)
    group = db.relationship(Group)
    bookings = db.relationship('Booking', back_populates='event')
    __table_args__ = (
        CheckConstraint('ends_at > starts_at', name='ck_event_interval'),
        CheckConstraint('capacity BETWEEN 1 AND 500', name='ck_event_capacity'),
        CheckConstraint('allowed_course BETWEEN 1 AND 5', name='ck_event_course'),
        CheckConstraint("status IN ('active','cancelled')", name='ck_event_status'),
        UniqueConstraint('id','starts_at','ends_at', name='uq_event_interval'),
        ExcludeConstraint(('teacher_id', '='), (func.tstzrange(starts_at, ends_at, '[)'), '&&'),
                          where=text("status = 'active'"), name='ex_teacher_overlap', using='gist'),)

    @property
    def occupied(self):
        return sum(b.status == 'active' for b in self.bookings)

class Booking(db.Model):
    __tablename__ = 'bookings'
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    starts_at = db.Column(db.DateTime(timezone=True), nullable=False)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=False)
    status = db.Column(db.String(12), nullable=False, default='active')
    attendance = db.Column(db.String(12), nullable=False, default='pending')
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    student = db.relationship(User)
    event = db.relationship(Event, back_populates='bookings')
    __table_args__ = (
        ForeignKeyConstraint(['event_id','starts_at','ends_at'], ['events.id','events.starts_at','events.ends_at'], name='fk_booking_event_interval'),
        UniqueConstraint('event_id', 'student_id', name='uq_event_student'),
        CheckConstraint("status IN ('active','cancelled')", name='ck_booking_status'),
        CheckConstraint("attendance IN ('pending','present','absent')", name='ck_attendance'),
        ExcludeConstraint(('student_id', '='), (func.tstzrange(starts_at, ends_at, '[)'), '&&'),
                          where=text("status = 'active'"), name='ex_student_overlap', using='gist'),)

class AuditLog(db.Model):
    __tablename__ = 'audit_log'
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    action = db.Column(db.String(60), nullable=False)
    entity = db.Column(db.String(120), nullable=False)
    details = db.Column(db.Text, nullable=False, default='')
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    actor = db.relationship(User)

class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id', ondelete='CASCADE'))
    key = db.Column(db.String(160), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    body_en = db.Column(db.Text)
    link = db.Column(db.String(200), nullable=False, default='')
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    read_at = db.Column(db.DateTime(timezone=True))
    __table_args__ = (UniqueConstraint('user_id', 'key', name='uq_notification_user_key'),)

class SiteSettings(db.Model):
    __tablename__ = 'site_settings'
    id = db.Column(db.Integer, primary_key=True)
    maintenance = db.Column(db.Boolean, nullable=False, default=False)
    __table_args__ = (CheckConstraint('id = 1', name='ck_site_settings_singleton'),)

class UserAvatar(db.Model):
    __tablename__ = 'user_avatars'
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    image = db.deferred(db.Column(db.LargeBinary, nullable=False))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (CheckConstraint('octet_length(image) <= 262144', name='ck_avatar_size'),)
