"""Add role tutorials and per-user first-view state."""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('tutorial_seen_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        'tutorial_videos',
        sa.Column('role', sa.String(length=12), primary_key=True),
        sa.Column('source_url', sa.String(length=500), nullable=False),
        sa.Column('embed_url', sa.String(length=500), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('student','teacher','admin')", name='ck_tutorial_video_role'),
    )


def downgrade():
    op.drop_table('tutorial_videos')
    op.drop_column('users', 'tutorial_seen_at')
