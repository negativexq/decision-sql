"""Lazily imported database drivers, one module per connector kind."""

from __future__ import annotations

import importlib

from app.metadata.connectors import Connector

_MODULES = {
    "postgresql": "app.metadata.drivers.postgres",
    "mysql": "app.metadata.drivers.mysql",
    "sqlserver": "app.metadata.drivers.sqlserver",
    "trino": "app.metadata.drivers.trino",
    "snowflake": "app.metadata.drivers.snowflake",
    "bigquery": "app.metadata.drivers.bigquery",
}


def load_connector(kind: str) -> Connector:
    """Import the driver module on first use; raises ImportError if its package is missing."""
    connector: Connector = importlib.import_module(_MODULES[kind]).CONNECTOR
    return connector
