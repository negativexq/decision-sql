"""create decision_meta schema for the audit log and data connections

Revision ID: 0002_operator_metadata
Revises: 0001_commerce_schema
"""
from typing import Sequence, Union

from alembic import op

from app.metadata.tables import METADATA_SCHEMA, metadata

revision: str = "0002_operator_metadata"
down_revision: Union[str, None] = "0001_commerce_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {METADATA_SCHEMA}")
    # Model SQL runs as the reader role; it must never see audit rows or secrets.
    op.execute(f"REVOKE ALL ON SCHEMA {METADATA_SCHEMA} FROM PUBLIC")
    metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    op.execute(f"DROP SCHEMA IF EXISTS {METADATA_SCHEMA} CASCADE")
