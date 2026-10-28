"""Connection management for the operator API."""

from __future__ import annotations

import re
from dataclasses import dataclass
from threading import Lock

import psycopg
from sqlalchemy import URL, Engine, create_engine

from app.catalog.models import SchemaCatalog
from app.metadata.connectors import (
    QUESTION_KINDS,
    ConnectionConfig,
    ConnectionSummary,
    ConnectionTestResult,
    Connector,
    ConnectorType,
    IntrospectedTable,
    connector_spec,
    connector_types,
    driver_installed,
    failure,
)
from app.metadata.drivers import load_connector
from app.metadata.drivers.install import DriverInstallResult, install_driver
from app.metadata.drivers.postgres import PostgresConnector
from app.metadata.relationships import (
    ConnectionRelationships,
    DeclaredRelationship,
    InMemoryRelationshipStore,
    RelationshipDeclaration,
    RelationshipRejectedError,
    RelationshipStore,
    candidate_relationships,
    check_declaration,
    foreign_keys,
    merge_declared,
)
from app.metadata.secrets import SecretBox
from app.metadata.store import ConnectionStore, StoredConnection

GOVERNED_CONNECTION_ID = "governed"
_MIN_MATCH_RATE = 0.5
ANONYMOUS_ACTOR = "anonymous"
_ACTOR = re.compile(r"^[A-Za-z0-9._@+-]{1,120}$")


def normalize_actor(value: str | None) -> str:
    """Self-declared operator identity until SSO; malformed values are not trusted."""
    if value and _ACTOR.match(value.strip()):
        return value.strip().lower()
    return ANONYMOUS_ACTOR


class ConnectorUnavailableError(ValueError):
    pass


class DriverInstallDisabledError(PermissionError):
    pass


@dataclass(frozen=True)
class QuestionSource:
    """A saved connection resolved for one question: its live catalog and a reader engine."""

    connection_id: str
    engine: Engine
    catalog: SchemaCatalog
    reader_role: str


class ConnectionService:
    def __init__(
        self,
        store: ConnectionStore,
        secrets: SecretBox,
        governed_engine: Engine,
        allow_driver_install: bool = False,
        relationships: RelationshipStore | None = None,
    ) -> None:
        self.store = store
        self.relationship_store = relationships or InMemoryRelationshipStore()
        self.secrets = secrets
        self.governed_engine = governed_engine
        self.allow_driver_install = allow_driver_install
        self._engines: dict[str, Engine] = {}
        self._engines_lock = Lock()

    def connector_types(self) -> tuple[ConnectorType, ...]:
        return connector_types(self.allow_driver_install)

    def install_driver(self, kind: str) -> DriverInstallResult:
        if not self.allow_driver_install:
            raise DriverInstallDisabledError(
                "Driver installation is turned off on this server "
                "(DECISION_SQL_ALLOW_DRIVER_INSTALL)."
            )
        return install_driver(kind)

    def connections(self) -> list[ConnectionSummary]:
        return [self._governed(), *(item.summary() for item in self.store.list())]

    def test(self, config: ConnectionConfig) -> ConnectionTestResult:
        config = self._complete(config)
        connector = self._connector(config.kind, config)
        password = config.password.get_secret_value() if config.password else None
        return _safe_test(connector, config, password)

    def test_saved(self, connection_id: str) -> ConnectionTestResult | None:
        item = self.store.get(connection_id)
        if item is None:
            return None
        result = _safe_test(
            self._connector(item.kind), item.config(), self._password(item.password_encrypted)
        )
        self.store.record_test(connection_id, result)
        return result

    def create(
        self, config: ConnectionConfig, actor: str
    ) -> tuple[ConnectionSummary, ConnectionTestResult]:
        """Test before saving so a stored connection was reachable at least once."""
        config = self._complete(config)
        result = self.test(config)
        if not result.ok:
            return self._unsaved(config, actor), result
        encrypted = (
            self.secrets.encrypt(config.password.get_secret_value()) if config.password else None
        )
        item = self.store.create(config, encrypted, actor)
        self.store.record_test(item.id, result)
        saved = self.store.get(item.id) or item
        return saved.summary(), result

    def delete(self, connection_id: str) -> bool:
        with self._engines_lock:
            engine = self._engines.pop(connection_id, None)
        if engine is not None:
            engine.dispose()
        # The SQL store cascades; the in-memory one needs the explicit cleanup.
        for relationship in self.relationship_store.list(connection_id):
            self.relationship_store.delete(connection_id, relationship.id)
        return self.store.delete(connection_id)

    def question_source(self, connection_id: str) -> QuestionSource | None:
        """Read the connection's schema now, so the question sees its current tables."""
        resolved = self._postgres(connection_id)
        if resolved is None:
            return None
        item, connector, password = resolved
        config = item.config()
        catalog = merge_declared(
            connector.catalog(config, password), self.relationship_store.list(item.id)
        )
        return QuestionSource(
            connection_id=item.id,
            engine=self._reader_engine(item.id, config, password),
            catalog=catalog,
            reader_role=config.username,
        )

    def relationships(self, connection_id: str) -> ConnectionRelationships | None:
        """Foreign keys, declared relationships, and checked suggestions for one source."""
        resolved = self._postgres(connection_id)
        if resolved is None:
            return None
        item, connector, password = resolved
        config = item.config()
        catalog = connector.catalog(config, password)
        declared = self.relationship_store.list(item.id)
        candidates = candidate_relationships(
            merge_declared(catalog, declared), connector.unique_keys(config, password)
        )
        rates = connector.match_rates(
            config,
            password,
            [((c.from_table, c.from_column), (c.to_table, c.to_column)) for c in candidates],
        )
        suggestions = sorted(
            (
                candidate.model_copy(update={"match_rate": rate})
                for candidate, rate in zip(candidates, rates, strict=True)
                # Matching names whose values mostly do not match are not a relationship.
                if rate is None or rate >= _MIN_MATCH_RATE
            ),
            key=lambda item: -(item.match_rate or 0.0),
        )
        return ConnectionRelationships(
            foreign_keys=foreign_keys(catalog), declared=declared, suggestions=suggestions
        )

    def declare_relationship(
        self, connection_id: str, declaration: RelationshipDeclaration, actor: str
    ) -> DeclaredRelationship | None:
        """Save a relationship after checking names, types, and that the target is unique."""
        resolved = self._postgres(connection_id)
        if resolved is None:
            return None
        item, connector, password = resolved
        config = item.config()
        catalog = connector.catalog(config, password)
        declared = self.relationship_store.list(item.id)
        checked = check_declaration(catalog, declaration, [*foreign_keys(catalog), *declared])
        target = (checked.to_table, checked.to_column)
        if target not in connector.unique_keys(config, password):
            try:
                repeated = connector.has_duplicates(config, password, *target)
            except psycopg.Error as error:
                raise RelationshipRejectedError(
                    f"Could not confirm that {checked.to_table}.{checked.to_column} is unique "
                    "within the time limit. Add a unique index on it, then declare again."
                ) from error
            if repeated:
                raise RelationshipRejectedError(
                    f"{checked.to_table}.{checked.to_column} has repeated values, so one "
                    f"{checked.from_table} row could match several {checked.to_table} rows. "
                    "Point the relationship at a column whose values are unique."
                )
        return self.relationship_store.create(item.id, checked, actor)

    def remove_relationship(self, connection_id: str, relationship_id: str) -> bool:
        return self.relationship_store.delete(connection_id, relationship_id)

    def _postgres(
        self, connection_id: str
    ) -> tuple[StoredConnection, PostgresConnector, str | None] | None:
        item = self.store.get(connection_id)
        if item is None:
            return None
        if item.kind not in QUESTION_KINDS:
            raise ConnectorUnavailableError(
                "Questions run on PostgreSQL connections for now. "
                "This connection can be tested and its tables browsed."
            )
        connector = self._connector(item.kind)
        if not isinstance(connector, PostgresConnector):
            raise ConnectorUnavailableError("This connection does not support questions.")
        return item, connector, self._password(item.password_encrypted)

    def _reader_engine(
        self, connection_id: str, config: ConnectionConfig, password: str | None
    ) -> Engine:
        with self._engines_lock:
            engine = self._engines.get(connection_id)
            if engine is None:
                url = URL.create(
                    "postgresql+psycopg",
                    username=config.username,
                    password=password,
                    host=config.host,
                    port=config.port or 5432,
                    database=config.database,
                )
                # schema_name is validated as a plain identifier, so it is safe in options.
                engine = create_engine(
                    url,
                    pool_pre_ping=True,
                    pool_size=2,
                    max_overflow=2,
                    connect_args={
                        "sslmode": config.ssl_mode,
                        "connect_timeout": 5,
                        "options": f"-c search_path={config.schema_name or 'public'}",
                    },
                )
                self._engines[connection_id] = engine
            return engine

    def introspect(self, connection_id: str) -> list[IntrospectedTable] | None:
        item = self.store.get(connection_id)
        if item is None:
            return None
        return self._connector(item.kind).introspect(
            item.config(), self._password(item.password_encrypted)
        )

    @staticmethod
    def _complete(config: ConnectionConfig) -> ConnectionConfig:
        spec = connector_spec(config.kind)
        return config.with_defaults(spec) if spec is not None else config

    def _password(self, token: bytes | None) -> str | None:
        return self.secrets.decrypt(token) if token is not None else None

    @staticmethod
    def _connector(kind: str, config: ConnectionConfig | None = None) -> Connector:
        """Resolve the driver; validate required fields when a new config is submitted."""
        spec = connector_spec(kind)
        if spec is None:
            raise ConnectorUnavailableError(f"Unknown connector {kind}.")
        if not driver_installed(spec):
            raise ConnectorUnavailableError(f"The {spec.label} driver is not installed.")
        if config is not None:
            missing = config.missing_fields(spec)
            if missing:
                raise ConnectorUnavailableError("Missing: " + ", ".join(missing) + ".")
        try:
            return load_connector(kind)
        except ImportError as error:
            raise ConnectorUnavailableError(
                f"The {spec.label} driver could not be loaded: {error}"
            ) from error

    def _governed(self) -> ConnectionSummary:
        url = self.governed_engine.url
        return ConnectionSummary(
            id=GOVERNED_CONNECTION_ID,
            name=url.database or "governed",
            kind="postgresql",
            host=url.host or "localhost",
            port=url.port or 5432,
            database=url.database or "",
            username=url.username or "",
            schema_name="public",
            ssl_mode="prefer",
            has_password=True,
            built_in=True,
            governed=True,
            queryable=True,
            created_by="system",
            created_at=None,
        )

    @staticmethod
    def _unsaved(config: ConnectionConfig, actor: str) -> ConnectionSummary:
        return ConnectionSummary(
            id="",
            **config.model_dump(exclude={"password"}),
            has_password=config.password is not None,
            created_by=actor,
            created_at=None,
        )


def _safe_test(
    connector: Connector, config: ConnectionConfig, password: str | None
) -> ConnectionTestResult:
    """A driver bug or unexpected error is a failed preflight, never a server error."""
    try:
        return connector.test(config, password)
    except Exception as error:
        return failure(error)
