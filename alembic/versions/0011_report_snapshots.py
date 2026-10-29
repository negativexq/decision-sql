"""keep report snapshots and refresh reports on a schedule

Revision ID: 0011_report_snapshots
Revises: 0010_audit_follow_up
"""
from typing import Sequence, Union

from alembic import op

from app.metadata.tables import METADATA_SCHEMA

revision: str = "0011_report_snapshots"
down_revision: Union[str, None] = "0010_audit_follow_up"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REPORTS = f"{METADATA_SCHEMA}.reports"
_SNAPSHOTS = f"{METADATA_SCHEMA}.report_snapshots"


def upgrade() -> None:
    # 0002 builds the tables from current metadata, so a fresh database already has them.
    op.execute(
        f"ALTER TABLE {_REPORTS} ADD COLUMN IF NOT EXISTS schedule VARCHAR(16) "
        "NOT NULL DEFAULT 'off'"
    )
    op.execute(
        f"ALTER TABLE {_REPORTS} ADD COLUMN IF NOT EXISTS next_refresh_at TIMESTAMPTZ"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{METADATA_SCHEMA}_reports_next_refresh_at "
        f"ON {_REPORTS} (next_refresh_at)"
    )
    op.execute(
        f"CREATE TABLE IF NOT EXISTS {_SNAPSHOTS} ("
        "id VARCHAR(64) PRIMARY KEY, "
        "report_id VARCHAR(64) NOT NULL, "
        "taken_at TIMESTAMPTZ NOT NULL, "
        "taken_by VARCHAR(120) NOT NULL, "
        "scheduled BOOLEAN NOT NULL, "
        "cards JSON NOT NULL)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{METADATA_SCHEMA}_report_snapshots_report_id "
        f"ON {_SNAPSHOTS} (report_id)"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_SNAPSHOTS}")
    op.execute(f"DROP INDEX IF EXISTS {METADATA_SCHEMA}.ix_{METADATA_SCHEMA}_reports_next_refresh_at")
    op.execute(f"ALTER TABLE {_REPORTS} DROP COLUMN IF EXISTS next_refresh_at")
    op.execute(f"ALTER TABLE {_REPORTS} DROP COLUMN IF EXISTS schedule")
