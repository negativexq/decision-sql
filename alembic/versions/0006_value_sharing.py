"""store each connection's choice to share stored values with the model

Revision ID: 0006_value_sharing
Revises: 0005_audit_executed_sql
"""
from typing import Sequence, Union

from alembic import op

from app.metadata.tables import value_sharing

revision: str = "0006_value_sharing"
down_revision: Union[str, None] = "0005_audit_executed_sql"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    value_sharing.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    value_sharing.drop(op.get_bind(), checkfirst=True)
