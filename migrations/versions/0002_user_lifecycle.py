"""Departments, student cohorts, recoverable deletion and notifications."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('groups', sa.Column('admission_year', sa.Integer()))
    op.execute("UPDATE groups SET admission_year = CASE WHEN name ~ '[0-9]{4}$' THEN right(name, 4)::integer WHEN name ~ '[0-9]{2}$' THEN 2000 + right(name, 2)::integer END")
    op.create_table('departments', sa.Column('id', sa.Integer(), primary_key=True), sa.Column('name', sa.String(160), nullable=False, unique=True), sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('users', sa.Column('department_id', sa.Integer(), sa.ForeignKey('departments.id')))
    op.add_column('users', sa.Column('dismissed_on', sa.Date()))
    op.add_column('users', sa.Column('deleted_at', sa.DateTime(timezone=True)))
    op.add_column('users', sa.Column('active_before_delete', sa.Boolean()))
    op.create_index('ix_users_deleted_at', 'users', ['deleted_at'])
    op.add_column('events', sa.Column('registration_opens_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    # Keep the consultation journal after the account itself is permanently removed.
    for table, column in [('events', 'teacher_id'), ('bookings', 'student_id')]:
        inspector = sa.inspect(op.get_bind())
        for fk in inspector.get_foreign_keys(table):
            if fk['constrained_columns'] == [column]:
                op.drop_constraint(fk['name'], table, type_='foreignkey')
        op.alter_column(table, column, nullable=True)
        op.create_foreign_key(f'fk_{table}_{column}', table, 'users', [column], ['id'], ondelete='SET NULL')
    op.create_table('notifications',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('events.id', ondelete='CASCADE')),
        sa.Column('key', sa.String(160), nullable=False),
        sa.Column('title', sa.String(160), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('link', sa.String(200), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('read_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('user_id', 'key', name='uq_notification_user_key'))
    op.create_index('ix_notifications_user_id', 'notifications', ['user_id'])

def downgrade():
    # Deleted identities cannot be recreated. Keep nullable journal references.
    op.drop_table('notifications')
    op.drop_column('events', 'registration_opens_at')
    for column in ['active_before_delete', 'deleted_at', 'dismissed_on', 'department_id']:
        op.drop_column('users', column)
    op.drop_table('departments')
    op.drop_column('groups', 'admission_year')
