"""report access: visibility, editors, and viewers

Revision ID: 0008_report_access
Revises: 0007_reports
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_report_access"
down_revision: Union[str, None] = "0007_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 0007 creates the table from the current definition, so a new database has these already.
_COLUMNS = (
    # Reports made before access existed were open to everyone; they stay that way.
    sa.Column("visibility", sa.String(16), nullable=False, server_default="workspace"),
    sa.Column("editors", sa.JSON, nullable=False, server_default="[]"),
    sa.Column("viewers", sa.JSON, nullable=False, server_default="[]"),
)


def _existing() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns("reports", schema="decision_meta")}


def upgrade() -> None:
    existing = _existing()
    for column in _COLUMNS:
        if column.name not in existing:
            op.add_column("reports", column.copy(), schema="decision_meta")
    op.alter_column("reports", "visibility", server_default="private", schema="decision_meta")


def downgrade() -> None:
    for column in _COLUMNS:
        op.drop_column("reports", column.name, schema="decision_meta")
