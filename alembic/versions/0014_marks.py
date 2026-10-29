"""keep each person's names and pins for conversations and reports

Revision ID: 0014_marks
Revises: 0013_audit_edited_from
"""
from typing import Sequence, Union

from alembic import op

from app.metadata.tables import marks

revision: str = "0014_marks"
down_revision: Union[str, None] = "0013_audit_edited_from"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    marks.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    marks.drop(op.get_bind(), checkfirst=True)
