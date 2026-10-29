"""count each report's saves so a stale save is refused

Revision ID: 0015_report_version
Revises: 0014_marks
"""
from typing import Sequence, Union

from alembic import op

from app.metadata.tables import METADATA_SCHEMA

revision: str = "0015_report_version"
down_revision: Union[str, None] = "0014_marks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = f"{METADATA_SCHEMA}.reports"


def upgrade() -> None:
    # 0002 builds the tables from current metadata, so a fresh database already has it.
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS version")
