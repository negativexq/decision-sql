"""store the SQL text the database received and why it differs from the proposal

Revision ID: 0005_audit_executed_sql
Revises: 0004_declared_relationships
"""
from typing import Sequence, Union

from alembic import op

from app.metadata.tables import METADATA_SCHEMA

revision: str = "0005_audit_executed_sql"
down_revision: Union[str, None] = "0004_declared_relationships"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = f"{METADATA_SCHEMA}.audit_runs"


def upgrade() -> None:
    # 0002 builds the tables from current metadata, so a fresh database already has these.
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS executed_sql TEXT")
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS sql_changes JSON")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS sql_changes")
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS executed_sql")
