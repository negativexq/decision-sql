"""store the model's explanation of a non-answer and the SQL a repair replaced

Revision ID: 0003_audit_model_detail
Revises: 0002_operator_metadata
"""
from typing import Sequence, Union

from alembic import op

from app.metadata.tables import METADATA_SCHEMA

revision: str = "0003_audit_model_detail"
down_revision: Union[str, None] = "0002_operator_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = f"{METADATA_SCHEMA}.audit_runs"


def upgrade() -> None:
    # 0002 builds the tables from current metadata, so a fresh database already has these.
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS model_detail JSON")
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS repaired_from_sql TEXT")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS repaired_from_sql")
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS model_detail")
