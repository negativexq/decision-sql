"""SQLAlchemy Core tables in the ``decision_meta`` schema.

The schema is deliberately separate from ``public``: the reader role has no
grant on it, and the SQL policy only admits ``public`` relations, so model SQL
can never read audit records or connection secrets.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

METADATA_SCHEMA = "decision_meta"

metadata = MetaData(schema=METADATA_SCHEMA)

audit_runs = Table(
    "audit_runs",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("actor", String(120), nullable=False, index=True),
    Column("connection_id", String(64), nullable=False),
    Column("question", Text, nullable=False),
    Column("preset_id", String(80)),
    Column("proposal_source", String(40), nullable=False),
    Column("model_decision", String(40)),
    Column("model_reason_code", String(60)),
    Column("model_detail", JSON),
    Column("runtime_outcome", String(40), nullable=False, index=True),
    Column("runtime_reason", String(120)),
    Column("provider", String(80)),
    Column("model", String(120)),
    Column("proposed_sql", Text),
    Column("repaired_from_sql", Text),
    Column("executed_sql", Text),
    Column("sql_changes", JSON),
    Column("columns", JSON, nullable=False),
    Column("row_count", Integer, nullable=False),
    Column("truncated", Boolean, nullable=False),
    Column("duration_ms", Float),
    Column("model_context", JSON),
    Column("model_context_hash", String(64)),
    Column("trace", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
)

connections = Table(
    "connections",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(120), nullable=False, unique=True),
    Column("kind", String(40), nullable=False),
    Column("host", String(255), nullable=False),
    Column("port", Integer),
    Column("database", String(255), nullable=False),
    Column("username", String(255), nullable=False),
    Column("password_encrypted", LargeBinary),
    Column("schema_name", String(255), nullable=False),
    Column("ssl_mode", String(40), nullable=False),
    Column("options", JSON, nullable=False),
    Column("created_by", String(120), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_tested_at", DateTime(timezone=True)),
    Column("last_test_ok", Boolean),
    Column("last_test_message", Text),
)

declared_relationships = Table(
    "declared_relationships",
    metadata,
    Column("id", String(64), primary_key=True),
    Column(
        "connection_id",
        String(64),
        ForeignKey(f"{METADATA_SCHEMA}.connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("from_table", String(255), nullable=False),
    Column("from_column", String(255), nullable=False),
    Column("to_table", String(255), nullable=False),
    Column("to_column", String(255), nullable=False),
    Column("created_by", String(120), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("connection_id", "from_table", "from_column", "to_table", "to_column"),
)

value_sharing = Table(
    "value_sharing",
    metadata,
    Column(
        "connection_id",
        String(64),
        ForeignKey(f"{METADATA_SCHEMA}.connections.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("enabled", Boolean, nullable=False),
    Column("excluded", JSON, nullable=False),
    Column("updated_by", String(120), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
