import os

import pytest

from evaluation.external.livesqlbench.schema import (
    PostgresConnectionConfig,
    introspect_database,
)


@pytest.mark.skipif(
    os.getenv("RUN_LIVESQLBENCH_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_LIVESQLBENCH_POSTGRES_INTEGRATION=1 for the official PostgreSQL image",
)
def test_official_postgresql_catalog_is_readable() -> None:
    config = PostgresConnectionConfig.from_environment()
    catalog = introspect_database("solar", config)
    assert catalog.table_count > 0
    assert catalog.column_count > 0
