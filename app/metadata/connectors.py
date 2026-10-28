"""Connector catalog and connection contracts.

A connector can test a connection and read a source schema. Drivers live in
``app.metadata.drivers`` and are imported only when used, so a missing optional
driver package disables that connector instead of breaking the API.

Connecting is not the same as answering questions: governed execution (SQL
policy, EXPLAIN cost gate, read-only enforcement) is PostgreSQL-only today.
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

ConnectorKind = Literal["postgresql", "mysql", "sqlserver", "trino", "snowflake", "bigquery"]
SslMode = Literal["disable", "prefer", "require", "verify-full"]

# Fields stored as columns; every other declared field is kept in ``options``.
CORE_FIELDS = frozenset(
    {"host", "port", "database", "username", "password", "schema_name", "ssl_mode"}
)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$-]{0,127}$")
_HOST = re.compile(r"^[A-Za-z0-9._-]{1,255}$")
_OPTION_VALUE = re.compile(r"^[A-Za-z0-9_.$:/-]{0,255}$")


class ConnectorField(BaseModel):
    name: str
    label: str
    kind: Literal["text", "number", "password", "select", "textarea"] = "text"
    required: bool = True
    placeholder: str | None = None
    options: list[str] = Field(default_factory=list)
    default: str | None = None


class ConnectorSpec(BaseModel):
    kind: ConnectorKind
    label: str
    description: str
    default_port: int | None
    fields: list[ConnectorField]
    driver_module: str
    extra: str
    # Exact requirement specs the install endpoint may pass to pip; never user input.
    packages: tuple[str, ...] = ()


class ConnectorType(BaseModel):
    kind: ConnectorKind
    label: str
    description: str
    available: bool
    installable: bool = False
    unavailable_reason: str | None = None
    default_port: int | None
    fields: list[ConnectorField]


def _field(name: str, label: str, **kwargs: object) -> ConnectorField:
    return ConnectorField.model_validate({"name": name, "label": label, **kwargs})


def _ssl(*options: str) -> ConnectorField:
    return _field("ssl_mode", "SSL", kind="select", options=list(options), default=options[0])


CONNECTOR_SPECS: tuple[ConnectorSpec, ...] = (
    ConnectorSpec(
        kind="postgresql",
        label="PostgreSQL",
        description="PostgreSQL 13 or later, including RDS, Cloud SQL and Azure Database.",
        default_port=5432,
        driver_module="psycopg",
        extra="",
        fields=[
            _field("host", "Host", placeholder="db.internal.example.com"),
            _field("port", "Port", kind="number"),
            _field("database", "Database"),
            _field("username", "Username", placeholder="read-only role"),
            _field("password", "Password", kind="password", required=False),
            _field("schema_name", "Schema", default="public"),
            _ssl("prefer", "require", "verify-full", "disable"),
        ],
    ),
    ConnectorSpec(
        kind="mysql",
        label="MySQL",
        description="MySQL 8 and MariaDB 10.6 or later.",
        default_port=3306,
        driver_module="pymysql",
        extra="mysql",
        packages=("PyMySQL>=1.1,<2",),
        fields=[
            _field("host", "Host"),
            _field("port", "Port", kind="number"),
            _field("database", "Database"),
            _field("username", "Username", placeholder="read-only user"),
            _field("password", "Password", kind="password", required=False),
            _ssl("require", "verify-full", "disable"),
        ],
    ),
    ConnectorSpec(
        kind="sqlserver",
        label="SQL Server",
        description="Microsoft SQL Server 2017 or later and Azure SQL Database.",
        default_port=1433,
        driver_module="pymssql",
        extra="sqlserver",
        packages=("pymssql>=2.3,<3",),
        fields=[
            _field("host", "Host"),
            _field("port", "Port", kind="number"),
            _field("database", "Database"),
            _field("username", "Username"),
            _field("password", "Password", kind="password", required=False),
            _field("schema_name", "Schema", default="dbo"),
        ],
    ),
    ConnectorSpec(
        kind="trino",
        label="Trino",
        description="Trino and Starburst clusters. Reads one catalog and schema.",
        default_port=8080,
        driver_module="trino",
        extra="trino",
        packages=("trino>=0.330,<1",),
        fields=[
            _field("host", "Coordinator host"),
            _field("port", "Port", kind="number"),
            _field("database", "Catalog", placeholder="hive"),
            _field("schema_name", "Schema", default="default"),
            _field("username", "Username"),
            _field(
                "password",
                "Password",
                kind="password",
                required=False,
                placeholder="Requires HTTPS",
            ),
            _field(
                "http_scheme",
                "Protocol",
                kind="select",
                options=["https", "http"],
                default="https",
            ),
        ],
    ),
    ConnectorSpec(
        kind="snowflake",
        label="Snowflake",
        description="Snowflake accounts with user and password authentication.",
        default_port=None,
        driver_module="snowflake.connector",
        extra="snowflake",
        packages=("snowflake-connector-python>=3.12,<5",),
        fields=[
            _field("host", "Account identifier", placeholder="orgname-accountname"),
            _field("username", "Username"),
            _field("password", "Password", kind="password"),
            _field("database", "Database"),
            _field("schema_name", "Schema", default="PUBLIC"),
            _field("warehouse", "Warehouse"),
            _field("role", "Role", required=False, placeholder="read-only role"),
        ],
    ),
    ConnectorSpec(
        kind="bigquery",
        label="BigQuery",
        description="Google BigQuery datasets through a service account.",
        default_port=None,
        driver_module="google.cloud.bigquery",
        extra="bigquery",
        packages=("google-cloud-bigquery>=3.25,<4",),
        fields=[
            _field("host", "Project ID", placeholder="my-gcp-project"),
            _field("database", "Dataset"),
            _field("password", "Service account key (JSON)", kind="textarea"),
        ],
    ),
)


def connector_spec(kind: str) -> ConnectorSpec | None:
    return next((item for item in CONNECTOR_SPECS if item.kind == kind), None)


def driver_installed(spec: ConnectorSpec) -> bool:
    try:
        return importlib.util.find_spec(spec.driver_module) is not None
    except ModuleNotFoundError:
        return False


def connector_types(install_allowed: bool = False) -> tuple[ConnectorType, ...]:
    types = []
    for spec in CONNECTOR_SPECS:
        installed = driver_installed(spec)
        types.append(
            ConnectorType(
                kind=spec.kind,
                label=spec.label,
                description=spec.description,
                available=installed,
                installable=not installed and install_allowed and bool(spec.packages),
                unavailable_reason=(
                    None
                    if installed
                    else "The driver is not installed on the server."
                    if install_allowed
                    else f'Ask an administrator to run: pip install "decision-sql[{spec.extra}]"'
                ),
                default_port=spec.default_port,
                fields=spec.fields,
            )
        )
    return tuple(types)


class ConnectionConfig(BaseModel):
    """Connection details submitted by an operator. The password never leaves the server."""

    model_config = ConfigDict(extra="forbid")

    kind: ConnectorKind
    name: str = Field(min_length=1, max_length=120)
    host: str
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=255)
    username: str = Field(default="", max_length=255)
    password: SecretStr | None = Field(default=None, max_length=20_000)
    schema_name: str = ""
    ssl_mode: SslMode = "prefer"
    options: dict[str, str] = Field(default_factory=dict, max_length=8)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name is required")
        return value.strip()

    @field_validator("host")
    @classmethod
    def valid_host(cls, value: str) -> str:
        if not _HOST.match(value.strip()):
            raise ValueError("Host must be a hostname, IP address or account identifier")
        return value.strip()

    @field_validator("schema_name")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value and not _IDENTIFIER.match(value):
            raise ValueError("Schema must be a plain identifier")
        return value

    @field_validator("options")
    @classmethod
    def valid_options(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if not _IDENTIFIER.match(key) or not _OPTION_VALUE.match(item):
                raise ValueError(f"Option {key} has an unsupported value")
        return value

    def option(self, name: str, default: str = "") -> str:
        return self.options.get(name) or default

    def with_defaults(self, spec: ConnectorSpec) -> ConnectionConfig:
        """Fill empty fields from the connector's declared defaults and default port."""
        updates: dict[str, object] = {}
        options = dict(self.options)
        if self.port is None and spec.default_port is not None:
            updates["port"] = spec.default_port
        for field in spec.fields:
            if field.default is None:
                continue
            if field.name in CORE_FIELDS:
                if getattr(self, field.name) in (None, ""):
                    updates[field.name] = field.default
            elif not options.get(field.name):
                options[field.name] = field.default
        if options != self.options:
            updates["options"] = options
        return self.model_copy(update=updates) if updates else self

    def missing_fields(self, spec: ConnectorSpec) -> list[str]:
        """Labels of required fields this config leaves empty for the given connector."""
        missing = []
        for field in spec.fields:
            if not field.required:
                continue
            value = (
                getattr(self, field.name)
                if field.name in CORE_FIELDS
                else self.options.get(field.name)
            )
            if isinstance(value, SecretStr):
                value = value.get_secret_value()
            if value is None or value == "":
                missing.append(field.label)
        unknown = set(self.options) - {field.name for field in spec.fields}
        if unknown:
            missing.extend(f"unknown option {name}" for name in sorted(unknown))
        return missing


PreflightError = Literal[
    "authentication",
    "user",
    "unreachable",
    "timeout",
    "database",
    "schema",
    "tls",
    "config",
    "unknown",
]


class ConnectionTestResult(BaseModel):
    ok: bool
    message: str
    error_kind: PreflightError | None = None
    # The form field most likely at fault, so the UI can point at it.
    field: str | None = None
    detail: str | None = None
    server_version: str | None = None
    latency_ms: float | None = None
    table_count: int | None = None
    can_write: bool | None = None


# Ordered: the first matching pattern wins. Patterns cover PostgreSQL, MySQL,
# SQL Server, Trino, Snowflake and BigQuery driver messages.
_ERROR_RULES: tuple[tuple[re.Pattern[str], PreflightError, str | None, str], ...] = (
    (
        re.compile(r"404 not found: post .*snowflakecomputing", re.I),
        "unreachable",
        "host",
        "This Snowflake account identifier was not found.",
    ),
    (
        re.compile(r"deserialize key|private key|invalid service account", re.I),
        "config",
        "password",
        "The service account key could not be read. Paste the full JSON key file.",
    ),
    (
        re.compile(r"password over https", re.I),
        "config",
        "http_scheme",
        "A password can only be sent over HTTPS. Switch the protocol or remove the password.",
    ),
    (
        re.compile(r"service account key|not valid json", re.I),
        "config",
        "password",
        "The service account key is missing or is not valid JSON.",
    ),
    (
        re.compile(r"access denied for user .* to database", re.I),
        "database",
        "database",
        "This user is not allowed to open this database.",
    ),
    (
        re.compile(r"role \".*\" does not exist|unknown user|user .* does not exist", re.I),
        "user",
        "username",
        "This user does not exist on the server.",
    ),
    (
        re.compile(
            r"password authentication failed|access denied for user|login failed for user"
            r"|incorrect username or password|authentication failed|401|invalid_grant"
            r"|no password supplied|password is required",
            re.I,
        ),
        "authentication",
        "password",
        "The username or password is wrong.",
    ),
    (
        re.compile(
            r"could not translate host name|name or service not known|nodename nor servname"
            r"|unknown host|getaddrinfo|name resolution|unknown mysql server host",
            re.I,
        ),
        "unreachable",
        "host",
        "The host name could not be resolved.",
    ),
    (
        re.compile(
            r"connection refused|actively refused|errno 61|errno 111"
            r"|adaptive server is unavailable",
            re.I,
        ),
        "unreachable",
        "port",
        "Nothing is accepting connections on this host and port.",
    ),
    (
        re.compile(
            r"timed? ?out|timeout expired|can't connect to mysql server|unable to connect", re.I
        ),
        "timeout",
        "host",
        "The server did not answer in time. Check the host, port and firewall.",
    ),
    (
        re.compile(
            r"database \".*\" does not exist|unknown database|cannot open database"
            r"|catalog .* (does not exist|not found)|dataset .* not found"
            r"|database .* does not exist",
            re.I,
        ),
        "database",
        "database",
        "This database does not exist or the user cannot open it.",
    ),
    (
        re.compile(r"schema .* (does not exist|not found)", re.I),
        "schema",
        "schema_name",
        "This schema does not exist.",
    ),
    (
        re.compile(r"ssl|tls|certificate", re.I),
        "tls",
        "ssl_mode",
        "The secure connection could not be set up. Check the SSL setting.",
    ),
)


def failure(error: BaseException | str) -> ConnectionTestResult:
    """Turn a driver error into a plain message and the field to fix."""
    # Some drivers (pymssql) put the useful part on later lines; classify the whole text.
    detail = " ".join(str(error).split())[:300] or "Connection failed."
    for pattern, kind, field, message in _ERROR_RULES:
        if pattern.search(detail):
            return ConnectionTestResult(
                ok=False, message=message, error_kind=kind, field=field, detail=detail
            )
    return ConnectionTestResult(
        ok=False, message="The connection failed.", error_kind="unknown", detail=detail
    )


def missing_schema(schema: str) -> ConnectionTestResult:
    return ConnectionTestResult(
        ok=False,
        message=f"Schema {schema} was not found, or this user cannot see it.",
        error_kind="schema",
        field="schema_name",
    )


class IntrospectedColumn(BaseModel):
    name: str
    type: str
    nullable: bool


class IntrospectedTable(BaseModel):
    name: str
    row_estimate: int | None
    columns: list[IntrospectedColumn]


# Kinds whose sessions support the full question path: read-only transaction, EXPLAIN cost gate.
QUESTION_KINDS = frozenset({"postgresql"})


class ConnectionSummary(BaseModel):
    id: str
    name: str
    kind: ConnectorKind
    host: str
    port: int | None
    database: str
    username: str
    schema_name: str
    ssl_mode: SslMode
    options: dict[str, str] = Field(default_factory=dict)
    has_password: bool
    built_in: bool = False
    governed: bool = False
    queryable: bool = False
    created_by: str
    created_at: datetime | None
    last_tested_at: datetime | None = None
    last_test_ok: bool | None = None
    last_test_message: str | None = None


class Connector(Protocol):
    def test(self, config: ConnectionConfig, password: str | None) -> ConnectionTestResult: ...

    def introspect(
        self, config: ConnectionConfig, password: str | None
    ) -> list[IntrospectedTable]: ...


def connected_message(table_count: int, scope: str, can_write: bool | None) -> str:
    message = f"{table_count} tables in {scope}."
    if can_write:
        message += " This user can write; a read-only user is recommended."
    return message


def group_columns(
    rows: Iterable[Sequence[Any]], estimates: Mapping[str, int | None]
) -> list[IntrospectedTable]:
    """Build tables from ``(table, column, type, nullable)`` rows in column order."""
    tables: dict[str, list[IntrospectedColumn]] = {}
    for table, column, data_type, nullable in rows:
        tables.setdefault(str(table), []).append(
            IntrospectedColumn(name=str(column), type=str(data_type), nullable=bool(nullable))
        )
    result = []
    for name, items in tables.items():
        estimate = estimates.get(name)
        result.append(
            IntrospectedTable(
                name=name,
                row_estimate=int(estimate) if estimate is not None and int(estimate) >= 0 else None,
                columns=items,
            )
        )
    return result
