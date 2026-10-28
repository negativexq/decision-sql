"""Connection management for the operator API."""

from __future__ import annotations

import re

from sqlalchemy import Engine

from app.metadata.connectors import (
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
from app.metadata.secrets import SecretBox
from app.metadata.store import ConnectionStore

GOVERNED_CONNECTION_ID = "governed"
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


class ConnectionService:
    def __init__(
        self,
        store: ConnectionStore,
        secrets: SecretBox,
        governed_engine: Engine,
        allow_driver_install: bool = False,
    ) -> None:
        self.store = store
        self.secrets = secrets
        self.governed_engine = governed_engine
        self.allow_driver_install = allow_driver_install

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
        return self.store.delete(connection_id)

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
