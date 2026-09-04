"""Deterministic comparison helpers for sink OFF versus sink ON fixtures."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EquivalenceReport:
    compared_fields: tuple[str, ...]
    mismatches: tuple[str, ...]

    @property
    def equivalent(self) -> bool:
        return not self.mismatches


def compare_snapshots(
    sink_off: Mapping[str, Any], sink_on: Mapping[str, Any], fields: tuple[str, ...]
) -> EquivalenceReport:
    """Compare only semantic decision outputs, never diagnostic event payloads."""
    mismatches = tuple(field for field in fields if sink_off.get(field) != sink_on.get(field))
    return EquivalenceReport(compared_fields=fields, mismatches=mismatches)
