"""Add an administrator-managed site announcement."""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('site_settings', sa.Column(
        'announcement_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('site_settings', sa.Column(
        'announcement_text', sa.Text(), nullable=False, server_default=''))
    op.add_column('site_settings', sa.Column(
        'announcement_color', sa.String(length=16), nullable=False, server_default='info'))
    op.create_check_constraint(
        'ck_site_announcement_color', 'site_settings',
        "announcement_color IN ('info','warning','danger','success','neutral')")


def downgrade():
    op.drop_constraint('ck_site_announcement_color', 'site_settings')
    op.drop_column('site_settings', 'announcement_color')
    op.drop_column('site_settings', 'announcement_text')
    op.drop_column('site_settings', 'announcement_enabled')
