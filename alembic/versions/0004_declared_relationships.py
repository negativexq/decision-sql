"""relationships an administrator declares for sources without foreign keys

Revision ID: 0004_declared_relationships
Revises: 0003_audit_model_detail
"""
from typing import Sequence, Union

from alembic import op

from app.metadata.tables import declared_relationships

revision: str = "0004_declared_relationships"
down_revision: Union[str, None] = "0003_audit_model_detail"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 0002 builds the tables from current metadata, so a fresh database already has it.
    declared_relationships.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    declared_relationships.drop(bind=op.get_bind(), checkfirst=True)
