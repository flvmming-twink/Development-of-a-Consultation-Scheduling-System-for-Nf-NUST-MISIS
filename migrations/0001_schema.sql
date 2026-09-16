CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE groups (
	id SERIAL NOT NULL, 
	name VARCHAR(40) NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE login_gates (
	login VARCHAR(120) NOT NULL, 
	failures INTEGER NOT NULL, 
	locked_until TIMESTAMP WITH TIME ZONE, 
	permanent BOOLEAN NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (login)
);

CREATE TABLE subjects (
	id SERIAL NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE users (
	id SERIAL NOT NULL, 
	login VARCHAR(120) NOT NULL, 
	role VARCHAR(12) NOT NULL, 
	full_name VARCHAR(160), 
	name_locked BOOLEAN NOT NULL, 
	group_id INTEGER, 
	course INTEGER, 
	study_mode VARCHAR(12), 
	password_hash TEXT NOT NULL, 
	initial_password_hash TEXT NOT NULL, 
	must_change_password BOOLEAN NOT NULL, 
	active BOOLEAN NOT NULL, 
	theme VARCHAR(8) NOT NULL, 
	session_version INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_user_login_role UNIQUE (login, role), 
	CONSTRAINT ck_user_role CHECK (role IN ('student','teacher','admin')), 
	CONSTRAINT ck_theme CHECK (theme IN ('light','dark')), 
	CONSTRAINT ck_student_profile CHECK (role != 'student' OR (group_id IS NOT NULL AND course IS NOT NULL AND course BETWEEN 1 AND 5 AND study_mode IS NOT NULL AND study_mode IN ('full_time','part_time') AND (course < 5 OR study_mode = 'part_time'))), 
	FOREIGN KEY(group_id) REFERENCES groups (id)
);

CREATE INDEX ix_users_login ON users (login);

CREATE TABLE teacher_subject (
	teacher_id INTEGER NOT NULL, 
	subject_id INTEGER NOT NULL, 
	PRIMARY KEY (teacher_id, subject_id), 
	FOREIGN KEY(teacher_id) REFERENCES users (id), 
	FOREIGN KEY(subject_id) REFERENCES subjects (id)
);

CREATE TABLE events (
	id SERIAL NOT NULL, 
	teacher_id INTEGER NOT NULL, 
	subject_id INTEGER NOT NULL, 
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ends_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	room VARCHAR(80) NOT NULL, 
	capacity INTEGER NOT NULL, 
	allowed_course INTEGER NOT NULL, 
	group_id INTEGER, 
	description VARCHAR(1200) NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_event_interval CHECK (ends_at > starts_at), 
	CONSTRAINT ck_event_capacity CHECK (capacity BETWEEN 1 AND 500), 
	CONSTRAINT ck_event_course CHECK (allowed_course BETWEEN 1 AND 5), 
	CONSTRAINT ck_event_status CHECK (status IN ('active','cancelled')), 
	CONSTRAINT uq_event_interval UNIQUE (id, starts_at, ends_at), 
	CONSTRAINT ex_teacher_overlap EXCLUDE USING gist (teacher_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&) WHERE (status = 'active'), 
	FOREIGN KEY(teacher_id) REFERENCES users (id), 
	FOREIGN KEY(subject_id) REFERENCES subjects (id), 
	FOREIGN KEY(group_id) REFERENCES groups (id)
);

CREATE INDEX ix_events_starts_at ON events (starts_at);

CREATE TABLE audit_log (
	id SERIAL NOT NULL, 
	actor_id INTEGER, 
	action VARCHAR(60) NOT NULL, 
	entity VARCHAR(120) NOT NULL, 
	details TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(actor_id) REFERENCES users (id)
);

CREATE INDEX ix_audit_log_created_at ON audit_log (created_at);

CREATE TABLE bookings (
	id SERIAL NOT NULL, 
	event_id INTEGER NOT NULL, 
	student_id INTEGER NOT NULL, 
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ends_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	attendance VARCHAR(12) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_booking_event_interval FOREIGN KEY(event_id, starts_at, ends_at) REFERENCES events (id, starts_at, ends_at), 
	CONSTRAINT uq_event_student UNIQUE (event_id, student_id), 
	CONSTRAINT ck_booking_status CHECK (status IN ('active','cancelled')), 
	CONSTRAINT ck_attendance CHECK (attendance IN ('pending','present','absent')), 
	CONSTRAINT ex_student_overlap EXCLUDE USING gist (student_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&) WHERE (status = 'active'), 
	FOREIGN KEY(student_id) REFERENCES users (id)
);
