"""UI-safe runtime trace records for the local operator console.

The trace collector is deliberately observational.  It receives stage results
from the existing application and never decides whether a query may execute.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from time import perf_counter
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class TraceStageStatus(StrEnum):
    PASS = "PASS"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class TraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    event_type: str
    at: datetime
    message: str
    stage: str | None = None


class TraceStage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    label: str
    status: TraceStageStatus
    duration_ms: float | None = None
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunTrace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trace_id: str
    run_id: str
    status: str
    started_at: datetime
    duration_ms: float | None = None
    stages: tuple[TraceStage, ...] = ()
    events: tuple[TraceEvent, ...] = ()


class TraceStageSink(Protocol):
    def record_stage(
        self,
        name: str,
        status: TraceStageStatus,
        *,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Record one actual runtime stage result."""


_LABELS = {
    "request": "Request",
    "context": "Governed context",
    "generation": "Model generation",
    "decision_admission": "Production decision admission",
    "proposal_replay": "Frozen proposal replay",
    "sql_parse": "SQL parse",
    "global_policy": "Global policy",
    "execution_authority": "Execution authority",
    "grain_safety": "Grain safety",
    "planning_connection": "Planning database connection",
    "explain": "EXPLAIN",
    "cost_gate": "Cost gate",
    "execution_connection": "Execution database connection",
    "execution": "Read-only execution",
    "response": "Response",
}


class RunTraceCollector:
    """Mutable request-local collector whose output is an immutable RunTrace."""

    def __init__(self, run_id: str | None = None) -> None:
        self.run_id = run_id or f"run_{uuid4().hex}"
        self.trace_id = f"tr_{uuid4().hex}"
        self.started_at = datetime.now(UTC)
        self._started = perf_counter()
        self._stages: list[TraceStage] = []
        self._events: list[TraceEvent] = []
        self._lock = Lock()

    def event(self, event_type: str, message: str, stage: str | None = None) -> None:
        with self._lock:
            self._events.append(
                TraceEvent(
                    event_id=f"evt_{uuid4().hex}",
                    event_type=event_type,
                    at=datetime.now(UTC),
                    message=message,
                    stage=stage,
                )
            )

    def record_stage(
        self,
        name: str,
        status: TraceStageStatus,
        *,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> None:
        stage = TraceStage(
            name=name,
            label=_LABELS.get(name, name.replace("_", " ").title()),
            status=status,
            duration_ms=duration_ms,
            reason=reason,
            metadata=_safe_metadata(metadata or {}),
        )
        with self._lock:
            self._stages = [item for item in self._stages if item.name != name]
            self._stages.append(stage)
            self._events.append(
                TraceEvent(
                    event_id=f"evt_{uuid4().hex}",
                    event_type=f"stage.{name.lower()}.{status.value.lower()}",
                    at=datetime.now(UTC),
                    message=reason or f"{stage.label}: {status.value}",
                    stage=name,
                )
            )

    def skip_unrecorded(self, names: tuple[str, ...], reason: str) -> None:
        recorded = {stage.name for stage in self._stages}
        for name in names:
            if name not in recorded:
                self.record_stage(name, TraceStageStatus.SKIPPED, reason=reason)

    def finish(self, status: str) -> RunTrace:
        with self._lock:
            ordered_stages = tuple(
                sorted(
                    self._stages,
                    key=lambda item: (
                        TRACE_STAGE_ORDER.index(item.name)
                        if item.name in TRACE_STAGE_ORDER
                        else len(TRACE_STAGE_ORDER)
                    ),
                )
            )
            return RunTrace(
                trace_id=self.trace_id,
                run_id=self.run_id,
                status=status,
                started_at=self.started_at,
                duration_ms=(perf_counter() - self._started) * 1000,
                stages=ordered_stages,
                events=tuple(self._events),
            )


def _safe_metadata(value: dict[str, Any]) -> dict[str, Any]:
    """Keep trace metadata scalar/bounded and never include rows or secrets."""
    safe: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key).lower()
        if key_text in {"rows", "password", "api_key", "authorization", "connection_string"}:
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            safe[str(key)] = item if not isinstance(item, str) else item[:500]
        elif isinstance(item, (list, tuple)):
            safe[str(key)] = [str(entry)[:200] for entry in item[:20]]
        else:
            safe[str(key)] = str(item)[:500]
    return safe


TRACE_STAGE_ORDER: tuple[str, ...] = (
    "request",
    "context",
    "generation",
    "proposal_replay",
    "decision_admission",
    "sql_parse",
    "global_policy",
    "execution_authority",
    "grain_safety",
    "planning_connection",
    "explain",
    "cost_gate",
    "execution_connection",
    "execution",
    "response",
)
