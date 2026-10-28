from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from google.api_core.exceptions import GoogleAPIError
from google.cloud import bigquery
from google.oauth2 import service_account

from app.metadata.connectors import (
    ConnectionConfig,
    ConnectionTestResult,
    IntrospectedColumn,
    IntrospectedTable,
    connected_message,
    failure,
)

_MAX_TABLES = 500
_READ_ONLY_SCOPES = ["https://www.googleapis.com/auth/bigquery.readonly"]


class BigQueryConnector:
    """Authenticates with a service account key restricted to read-only scopes."""

    def _client(self, config: ConnectionConfig, password: str | None) -> Any:
        if not password:
            raise ValueError("A service account key is required.")
        try:
            info = json.loads(password)
        except json.JSONDecodeError as error:
            raise ValueError("The service account key is not valid JSON.") from error
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=_READ_ONLY_SCOPES
        )
        return bigquery.Client(project=config.host, credentials=credentials)

    def test(self, config: ConnectionConfig, password: str | None) -> ConnectionTestResult:
        started = perf_counter()
        try:
            client = self._client(config, password)
            dataset = client.get_dataset(f"{config.host}.{config.database}", timeout=15)
            tables = list(client.list_tables(dataset, max_results=_MAX_TABLES, timeout=15))
        except (GoogleAPIError, ValueError) as error:
            return failure(error)
        return ConnectionTestResult(
            ok=True,
            message=connected_message(len(tables), f"dataset {config.database}", False),
            latency_ms=(perf_counter() - started) * 1000,
            table_count=len(tables),
            can_write=False,
        )

    def introspect(self, config: ConnectionConfig, password: str | None) -> list[IntrospectedTable]:
        client = self._client(config, password)
        result = []
        for item in client.list_tables(
            f"{config.host}.{config.database}", max_results=_MAX_TABLES, timeout=15
        ):
            table = client.get_table(item.reference, timeout=15)
            result.append(
                IntrospectedTable(
                    name=table.table_id,
                    row_estimate=table.num_rows,
                    columns=[
                        IntrospectedColumn(
                            name=field.name,
                            type=field.field_type,
                            nullable=field.mode != "REQUIRED",
                        )
                        for field in table.schema
                    ],
                )
            )
        return result


CONNECTOR = BigQueryConnector()
