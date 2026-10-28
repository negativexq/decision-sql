from __future__ import annotations

import subprocess
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import app.metadata.drivers.install as install_module
import app.metadata.service as service_module
from app.api.routes import get_connection_service, get_operator_application
from app.catalog.default import build_default_catalog
from app.config import Settings
from app.db.models import Base
from app.main import app
from app.metadata.connectors import ConnectionConfig, failure
from app.metadata.secrets import SecretBox, SecretsUnavailableError
from app.metadata.service import ConnectionService, normalize_actor
from app.metadata.store import InMemoryConnectionStore, InMemoryRunStore
from app.operator.service import OperatorApplication


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(_env_file=None, reader_role="reader")
    engine = create_engine("sqlite://")
    operator = OperatorApplication(
        engine, settings, build_default_catalog(Base.metadata), InMemoryRunStore()
    )
    connections = ConnectionService(
        InMemoryConnectionStore(), SecretBox(Fernet.generate_key().decode()), engine
    )
    app.dependency_overrides[get_operator_application] = lambda: operator
    app.dependency_overrides[get_connection_service] = lambda: connections
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _config(**overrides: object) -> dict[str, object]:
    return {
        "kind": "postgresql",
        "name": "Warehouse",
        "host": "127.0.0.1",
        "port": 1,
        "database": "analytics",
        "username": "reader",
        "password": "secret-value",
        **overrides,
    }


def test_actor_header_is_normalized_and_untrusted_values_become_anonymous() -> None:
    assert normalize_actor(" Ayse.Kaya@Example.com ") == "ayse.kaya@example.com"
    assert normalize_actor("robert'); DROP TABLE x;--") == "anonymous"
    assert normalize_actor(None) == "anonymous"


def test_secret_box_round_trips_and_refuses_without_key() -> None:
    box = SecretBox(Fernet.generate_key().decode())
    assert box.decrypt(box.encrypt("pw")) == "pw"
    with pytest.raises(SecretsUnavailableError):
        SecretBox(None).encrypt("pw")


def test_runs_are_audited_with_actor_and_filterable(client: TestClient) -> None:
    for actor in ("ayse@example.com", "mehmet@example.com"):
        response = client.post(
            "/api/playground/query",
            json={"question": "ignored", "preset_id": "policy_blocked"},
            headers={"X-Decision-Actor": actor},
        )
        assert response.status_code == 200
        assert response.json()["actor"] == actor

    everyone = client.get("/api/runs").json()["runs"]
    assert {run["actor"] for run in everyone} == {"ayse@example.com", "mehmet@example.com"}
    mine = client.get("/api/runs", params={"actor": "ayse@example.com"}).json()["runs"]
    assert [run["actor"] for run in mine] == ["ayse@example.com"]
    rejected = client.get("/api/runs", params={"outcome": "EXECUTED"}).json()["runs"]
    assert rejected == []


def test_connections_list_starts_with_the_governed_source(client: TestClient) -> None:
    connections = client.get("/api/connections").json()
    assert connections[0]["id"] == "governed"
    assert connections[0]["built_in"] is True
    assert client.delete("/api/connections/governed").status_code == 400


def test_connector_catalog_lists_every_kind_and_postgresql_is_ready(client: TestClient) -> None:
    connectors = {item["kind"]: item for item in client.get("/api/connectors").json()}
    assert set(connectors) == {"postgresql", "mysql", "sqlserver", "trino", "snowflake", "bigquery"}
    assert connectors["postgresql"]["available"] is True


def test_missing_driver_is_rejected_before_connecting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "driver_installed", lambda spec: spec.kind != "mysql")
    response = client.post("/api/connections/test", json=_config(kind="mysql", port=3306))
    assert response.status_code == 400
    assert "driver is not installed" in response.json()["detail"]


def test_required_fields_are_checked_per_connector(client: TestClient) -> None:
    response = client.post(
        "/api/connections/test", json=_config(kind="postgresql", username="", schema_name="")
    )
    assert response.status_code == 400
    assert "Username" in response.json()["detail"]


def test_driver_install_is_off_unless_enabled(client: TestClient) -> None:
    response = client.post("/api/connectors/mysql/install")
    assert response.status_code == 403


def test_driver_install_only_passes_allowlisted_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "installed", "")

    states = iter([False, True])
    monkeypatch.setattr(install_module.subprocess, "run", fake_run)
    monkeypatch.setattr(install_module, "driver_installed", lambda spec: next(states))
    result = install_module.install_driver("mysql")
    assert result.ok
    assert calls[0][-1] == "PyMySQL>=1.1,<2"
    assert install_module.install_driver("not-a-connector").ok is False


@pytest.mark.parametrize(
    ("message", "field"),
    [
        ('FATAL:  password authentication failed for user "x"', "password"),
        ("(1045, \"Access denied for user 'x'@'h' (using password: YES)\")", "password"),
        ("Login failed for user 'sa'.", "password"),
        ('FATAL:  role "bob" does not exist', "username"),
        ("port 5999 failed: Connection refused", "port"),
        ('could not translate host name "nope" to address', "host"),
        ("Access denied for user 'a'@'%' to database 'nope'", "database"),
        ('FATAL:  database "nodb" does not exist', "database"),
        ("Trino only accepts a password over HTTPS.", "http_scheme"),
        (
            "DB-Lib error message 20009, severity 9:\nUnable to connect: Adaptive Server is "
            "unavailable or does not exist (h)\nNet-Lib error during Connection refused (61)",
            "port",
        ),
    ],
)
def test_preflight_errors_point_at_the_field_to_fix(message: str, field: str) -> None:
    result = failure(message)
    assert result.ok is False
    assert result.field == field


def test_connection_config_rejects_unsafe_identifiers() -> None:
    with pytest.raises(ValueError):
        ConnectionConfig.model_validate(_config(schema_name="public; drop"))
    with pytest.raises(ValueError):
        ConnectionConfig.model_validate(_config(host="evil host"))


def test_saved_connection_never_returns_password() -> None:
    store = InMemoryConnectionStore()
    box = SecretBox(Fernet.generate_key().decode())
    config = ConnectionConfig.model_validate(_config())
    item = store.create(config, box.encrypt("secret-value"), "ayse@example.com")
    summary = item.summary().model_dump_json()
    assert "secret-value" not in summary
    assert "password_encrypted" not in summary
    assert item.summary().has_password is True


def test_questions_route_to_postgres_connections_and_refuse_other_kinds(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    unknown = client.post(
        "/api/playground/query", json={"question": "revenue", "connection_id": "nope"}
    )
    assert unknown.status_code == 404
    governed = client.post(
        "/api/playground/query",
        json={"question": "x", "preset_id": "policy_blocked", "connection_id": "governed"},
    )
    assert governed.status_code == 200

    service = app.dependency_overrides[get_connection_service]()
    mysql = service.store.create(
        ConnectionConfig.model_validate(_config(kind="mysql", name="Shop", port=3306)),
        None,
        "ayse@example.com",
    )
    postgres = service.store.create(
        ConnectionConfig.model_validate(_config(schema_name="public")), None, "ayse@example.com"
    )
    queryable = {item["id"]: item["queryable"] for item in client.get("/api/connections").json()}
    assert queryable == {"governed": True, mysql.id: False, postgres.id: True}

    refused = client.post(
        "/api/playground/query", json={"question": "revenue", "connection_id": mysql.id}
    )
    assert refused.status_code == 409
    assert "PostgreSQL connections" in refused.json()["detail"]

    preset = client.post(
        "/api/playground/query",
        json={"question": "x", "preset_id": "policy_blocked", "connection_id": postgres.id},
    )
    assert preset.status_code == 400

    def unreachable(connection_id: str) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(service, "question_source", unreachable)
    failed = client.post(
        "/api/playground/query", json={"question": "revenue", "connection_id": postgres.id}
    )
    assert failed.status_code == 502
    assert "connection refused" not in failed.json()["detail"]
