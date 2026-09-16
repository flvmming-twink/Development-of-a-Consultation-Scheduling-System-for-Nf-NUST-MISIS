"""Initial PostgreSQL schema frozen in an SQL snapshot."""
from pathlib import Path
from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    sql = (Path(__file__).resolve().parents[1] / '0001_schema.sql').read_text(encoding='utf-8')
    op.execute(sql)

def downgrade():
    for table in ['audit_log','bookings','events','teacher_subject','subjects','login_gates','users','groups']:
        op.execute('DROP TABLE ' + table)
