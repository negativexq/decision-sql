"""store administrators' column descriptions per connection

Revision ID: 0012_column_notes
Revises: 0011_report_snapshots
"""
from typing import Sequence, Union

from alembic import op

from app.metadata.tables import column_notes

revision: str = "0012_column_notes"
down_revision: Union[str, None] = "0011_report_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    column_notes.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    column_notes.drop(op.get_bind(), checkfirst=True)
