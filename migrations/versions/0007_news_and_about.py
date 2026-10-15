"""Add project information, news and per-user read state."""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('site_settings', sa.Column('about_html', sa.Text(), nullable=False, server_default=''))
    op.create_table(
        'news_posts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('author_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('author_name', sa.String(length=160), nullable=False),
        sa.Column('author_role', sa.String(length=12), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('body_html', sa.Text(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("author_role IN ('teacher','admin')", name='ck_news_author_role'),
    )
    op.create_index('ix_news_posts_author_id', 'news_posts', ['author_id'])
    op.create_index('ix_news_posts_published_at', 'news_posts', ['published_at'])
    op.create_table(
        'news_reads',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('post_id', sa.Integer(), sa.ForeignKey('news_posts.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table('news_reads')
    op.drop_index('ix_news_posts_published_at', table_name='news_posts')
    op.drop_index('ix_news_posts_author_id', table_name='news_posts')
    op.drop_table('news_posts')
    op.drop_column('site_settings', 'about_html')
