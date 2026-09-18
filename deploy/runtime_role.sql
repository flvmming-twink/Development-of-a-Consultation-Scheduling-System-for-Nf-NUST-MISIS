-- Run after migrations, as the schema owner in psql.
-- The password is set by the psql prompt, never committed to this file.
CREATE ROLE consult_runtime LOGIN;
\password consult_runtime
GRANT CONNECT ON DATABASE consultations TO consult_runtime;
GRANT USAGE ON SCHEMA public TO consult_runtime;
GRANT SELECT, INSERT, UPDATE ON users, groups, subjects, events, bookings, login_gates TO consult_runtime;
GRANT DELETE ON users, login_gates TO consult_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON departments, notifications TO consult_runtime;
GRANT SELECT, INSERT, DELETE ON teacher_subject TO consult_runtime;
GRANT SELECT, INSERT, UPDATE ON audit_log TO consult_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO consult_runtime;
-- Run Alembic using the owner role; start Waitress using consult_runtime.
