"""store reports: cards with a question, pinned SQL, chart choice, and layout

Revision ID: 0007_reports
Revises: 0006_value_sharing
"""
from typing import Sequence, Union

from alembic import op

from app.metadata.tables import reports

revision: str = "0007_reports"
down_revision: Union[str, None] = "0006_value_sharing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    reports.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    reports.drop(op.get_bind(), checkfirst=True)
