"""record which earlier run a follow-up question built on

Revision ID: 0010_audit_follow_up
Revises: 0009_report_filters
"""
from typing import Sequence, Union

from alembic import op

from app.metadata.tables import METADATA_SCHEMA

revision: str = "0010_audit_follow_up"
down_revision: Union[str, None] = "0009_report_filters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = f"{METADATA_SCHEMA}.audit_runs"


def upgrade() -> None:
    # 0002 builds the tables from current metadata, so a fresh database already has it.
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS follow_up_of VARCHAR(80)")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS follow_up_of")
