"""Maintenance switch, structured names and interface preferences."""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    for name in ('last_name', 'first_name', 'middle_name'):
        op.add_column('users', sa.Column(name, sa.String(80)))
    # Preserve full_name verbatim, including compound patronymics in existing profiles.
    op.execute("""WITH names AS (
        SELECT id, regexp_split_to_array(trim(full_name), E'\\\\s+') AS parts
        FROM users WHERE full_name IS NOT NULL
    ) UPDATE users SET last_name = left(parts[1], 80), first_name = left(parts[2], 80),
      middle_name = nullif(left(array_to_string(parts[3:array_length(parts, 1)], ' '), 80), '')
      FROM names WHERE users.id = names.id""")
    op.add_column('users', sa.Column('language', sa.String(2), nullable=False, server_default='ru'))
    op.add_column('users', sa.Column('event_view', sa.String(8), nullable=False, server_default='grid'))
    op.create_check_constraint('ck_user_language', 'users', "language IN ('ru','en')")
    op.create_check_constraint('ck_user_event_view', 'users', "event_view IN ('grid','list')")
    op.add_column('notifications', sa.Column('body_en', sa.Text()))
    op.create_table('site_settings', sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('maintenance', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint('id = 1', name='ck_site_settings_singleton'))
    op.execute('INSERT INTO site_settings (id, maintenance) VALUES (1, false)')


def downgrade():
    op.drop_table('site_settings')
    op.drop_column('notifications', 'body_en')
    op.drop_constraint('ck_user_language', 'users')
    op.drop_constraint('ck_user_event_view', 'users')
    for name in ('last_name', 'first_name', 'middle_name', 'language', 'event_view'):
        op.drop_column('users', name)
