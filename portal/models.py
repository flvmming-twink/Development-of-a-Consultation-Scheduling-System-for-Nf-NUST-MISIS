from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, UniqueConstraint, ForeignKeyConstraint, text, func
from sqlalchemy.dialects.postgresql import ExcludeConstraint

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

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    login = db.Column(db.String(120), nullable=False, index=True)
    role = db.Column(db.String(12), nullable=False)
    full_name = db.Column(db.String(160))
    name_locked = db.Column(db.Boolean, nullable=False, default=False)
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'))
    course = db.Column(db.Integer)
    study_mode = db.Column(db.String(12))
    password_hash = db.Column(db.Text, nullable=False)
    initial_password_hash = db.Column(db.Text, nullable=False)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    theme = db.Column(db.String(8), nullable=False, default='light')
    session_version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    group = db.relationship(Group)
    subjects = db.relationship('Subject', secondary=teacher_subject, back_populates='teachers')
    __table_args__ = (
        UniqueConstraint('login', 'role', name='uq_user_login_role'),
        CheckConstraint("role IN ('student','teacher','admin')", name='ck_user_role'),
        CheckConstraint("theme IN ('light','dark')", name='ck_theme'),
        CheckConstraint("role != 'student' OR (group_id IS NOT NULL AND course IS NOT NULL AND course BETWEEN 1 AND 5 AND study_mode IS NOT NULL AND study_mode IN ('full_time','part_time') AND (course < 5 OR study_mode = 'part_time'))", name='ck_student_profile'),)

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
    teacher_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
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
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
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
