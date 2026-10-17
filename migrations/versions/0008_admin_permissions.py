"""Add assignable administrator permissions."""
from alembic import op
import sqlalchemy as sa

from portal.permissions import ADMIN_PERMISSION_CODES


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'admin_permissions',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('permission', sa.String(length=40), primary_key=True),
        sa.CheckConstraint(
            "permission IN (" + ','.join(repr(code) for code in ADMIN_PERMISSION_CODES) + ")",
            name='ck_admin_permission_code'),
    )
    bind = op.get_bind()
    for permission in ADMIN_PERMISSION_CODES:
        bind.execute(sa.text(
            "INSERT INTO admin_permissions (user_id, permission) "
            "SELECT id, :permission FROM users WHERE role = 'admin'"
        ), {'permission': permission})


def downgrade():
    op.drop_table('admin_permissions')
