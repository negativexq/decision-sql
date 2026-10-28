"""Preflight and introspection against real MySQL and Trino servers.

Start them with:
  docker run -d -e MYSQL_ROOT_PASSWORD=rootpw -e MYSQL_DATABASE=shop -p 3307:3306 mysql:8.4
  docker run -d -p 8081:8080 trinodb/trino:476
and create a read-only MySQL user ``analyst`` / ``analystpw`` with SELECT on ``shop``.
"""

from __future__ import annotations

import importlib.util
import os
import socket

import pytest

from app.metadata.connectors import ConnectionConfig
from app.metadata.drivers import load_connector

MYSQL_PORT = int(os.getenv("CONNECTOR_TEST_MYSQL_PORT", "3307"))
TRINO_PORT = int(os.getenv("CONNECTOR_TEST_TRINO_PORT", "8081"))


def _require(module: str, port: int) -> None:
    if importlib.util.find_spec(module) is None:
        pytest.skip(f"{module} driver is not installed")
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", port)) != 0:
            pytest.skip(f"nothing listening on port {port}")


def test_mysql_preflight_and_introspection() -> None:
    _require("pymysql", MYSQL_PORT)
    connector = load_connector("mysql")
    config = ConnectionConfig(
        kind="mysql",
        name="shop",
        host="127.0.0.1",
        port=MYSQL_PORT,
        database="shop",
        username="analyst",
        ssl_mode="disable",
    )
    result = connector.test(config, "analystpw")
    assert result.ok, result.message
    assert result.can_write is False
    assert connector.test(config, "wrong").field == "password"
    tables = {table.name for table in connector.introspect(config, "analystpw")}
    assert "customers" in tables


def test_trino_preflight_and_introspection() -> None:
    _require("trino", TRINO_PORT)
    connector = load_connector("trino")
    config = ConnectionConfig(
        kind="trino",
        name="lake",
        host="127.0.0.1",
        port=TRINO_PORT,
        database="tpch",
        schema_name="tiny",
        username="analyst",
        options={"http_scheme": "http"},
    )
    result = connector.test(config, None)
    assert result.ok, result.message
    assert result.table_count == 8
    missing = config.model_copy(update={"schema_name": "nosuch"})
    assert connector.test(missing, None).field == "schema_name"
    tables = {table.name for table in connector.introspect(config, None)}
    assert {"orders", "customer"} <= tables
