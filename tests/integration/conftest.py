from __future__ import annotations

import os
from typing import NoReturn

import pytest


def postgres_unavailable(reason: str) -> NoReturn:
    """Skip locally; fail in CI, where a skipped PostgreSQL suite would hide regressions."""
    if os.getenv("DECISION_SQL_REQUIRE_POSTGRES") == "1":
        pytest.fail(reason)
    pytest.skip(reason)
