"""record which run an edited query came from

Revision ID: 0013_audit_edited_from
Revises: 0012_column_notes
"""
from typing import Sequence, Union

from alembic import op

from app.metadata.tables import METADATA_SCHEMA

revision: str = "0013_audit_edited_from"
down_revision: Union[str, None] = "0012_column_notes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = f"{METADATA_SCHEMA}.audit_runs"


def upgrade() -> None:
    # 0002 builds the tables from current metadata, so a fresh database already has it.
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS edited_from VARCHAR(80)")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS edited_from")
