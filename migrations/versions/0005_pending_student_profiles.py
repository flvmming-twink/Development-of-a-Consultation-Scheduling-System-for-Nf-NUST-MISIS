"""Allow imported students to complete their profile after first login."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


COMPLETE_PROFILE = """role != 'student' OR (
    (group_id IS NULL AND course IS NULL AND study_mode IS NULL) OR
    (group_id IS NOT NULL AND course IS NOT NULL AND course BETWEEN 1 AND 5
     AND study_mode IS NOT NULL AND study_mode IN ('full_time','part_time')
     AND (course < 5 OR study_mode = 'part_time'))
)"""

LEGACY_PROFILE = """role != 'student' OR (
    group_id IS NOT NULL AND course IS NOT NULL AND course BETWEEN 1 AND 5
    AND study_mode IS NOT NULL AND study_mode IN ('full_time','part_time')
    AND (course < 5 OR study_mode = 'part_time')
)"""


def upgrade():
    op.drop_constraint('ck_student_profile', 'users', type_='check')
    op.create_check_constraint('ck_student_profile', 'users', COMPLETE_PROFILE)


def downgrade():
    pending = op.get_bind().scalar(sa.text("""
        SELECT count(*) FROM users
        WHERE role = 'student' AND (group_id IS NULL OR course IS NULL OR study_mode IS NULL)
    """))
    if pending:
        raise RuntimeError('Complete or remove pending student profiles before downgrading migration 0005.')
    op.drop_constraint('ck_student_profile', 'users', type_='check')
    op.create_check_constraint('ck_student_profile', 'users', LEGACY_PROFILE)
