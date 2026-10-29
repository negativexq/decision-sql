"""report filters: columns a reader can narrow every card by

Revision ID: 0009_report_filters
Revises: 0008_report_access
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_report_filters"
down_revision: Union[str, None] = "0008_report_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("reports", schema="decision_meta")}
    # 0007 creates the table from the current definition, so a new database has it already.
    if "filters" not in existing:
        op.add_column(
            "reports",
            sa.Column("filters", sa.JSON, nullable=False, server_default="[]"),
            schema="decision_meta",
        )


def downgrade() -> None:
    op.drop_column("reports", "filters", schema="decision_meta")
