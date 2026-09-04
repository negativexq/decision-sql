"""No-op and append-only JSONL provenance sinks."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from pydantic import ValidationError

from app.provenance.canonical import semantic_hash
from app.provenance.models import ProvenanceEvent

PROHIBITED_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "authorization_header",
        "password",
        "database_password",
        "connection_string",
        "credentialed_connection_string",
        "provider_headers",
        "environment_secrets",
        "reference_sql",
        "reference_result",
        "expected_route",
        "expected_metric",
        "correctness",
        "benchmark_split",
        "residual_label",
        "unbounded_result_rows",
        "rows",
    }
)


def _prohibited_key(value: object) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in PROHIBITED_KEYS:
                return str(key)
            found = _prohibited_key(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _prohibited_key(item)
            if found:
                return found
    return None


class NoOpProvenanceSink:
    """The default product sink; it performs no persistence or decision work."""

    enabled = False

    def record(self, event: ProvenanceEvent) -> None:
        del event


class DiagnosticJsonlProvenanceSink:
    """Bounded append-only event sink with fail-open product behavior."""

    enabled = True

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self._next_sequence: dict[tuple[str, str], int] = {}
        self.error_count = 0
        self.last_error: str | None = None
        self._closed = False

    def record(self, event: ProvenanceEvent) -> None:
        """Write one complete line; failures are retained as diagnostic state only."""
        try:
            prohibited = _prohibited_key(event.payload)
            if prohibited:
                raise ValueError(f"prohibited provenance field: {prohibited}")
            with self._lock:
                if self._closed:
                    raise ValueError("provenance sink is closed")
                key = (event.run_id, event.case_id)
                sequence = self._next_sequence.get(key, 0) + 1
                self._next_sequence[key] = sequence
                materialized = event.model_copy(update={"sequence": sequence})
                line = json.dumps(
                    materialized.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                if semantic_hash(materialized.payload) != materialized.payload_hash:
                    raise ValueError("provenance payload hash mismatch")
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                    handle.flush()
        except (OSError, TypeError, ValueError, ValidationError) as error:
            self.error_count += 1
            self.last_error = str(error)

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def __enter__(self) -> DiagnosticJsonlProvenanceSink:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
