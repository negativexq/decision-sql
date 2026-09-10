# ruff: noqa: E501

"""Versioned stable provider contract selection.

Candidate C was selected and reproduced by M56R/M57.  Its bytes are kept here
as the single default-contract source; historical CONTROL and experiment
artifacts remain available through :func:`benchmark.m46b_contract.m43_prompt`.
"""

from __future__ import annotations

from benchmark.m46b_contract import m43_prompt
from benchmark.model_contract import sha256_text

STABLE_CONTRACT_VERSION = "CANDIDATE_C"
STABLE_CONTRACT_HASH = "3fb439e68cc158db6da5ea4bea31085d00776eb9573e5d743522922f74fb5587"

STABLE_CONTRACT_INTERVENTION = """

## Final contract check before emitting a decision

1. Meaning: what exact population, grouping, measure, filters, time range, and projection does the question request? Clarify only if one of these material choices is still unresolved in the visible governed context.
2. Authority: is the meaning clear, but a needed relation or path absent from `authorized_relationships`? If so, return `BLOCKED_AUTHORITY`. Do not use a technically visible table or a matching column as authorization.
3. Semantics: does each aggregate operate at its native grain? Use existence or key-preserving logic when a qualifying child could duplicate a parent measure. Use the governed duration formula rather than counting events, and retain required governed status or decision filters.

Do not turn a plausible schema interpretation into a business rule. Do not omit a governed predicate or replace a governed measure with a convenient proxy merely because the BASE data does not expose the difference.
"""


def stable_contract_prompt() -> str:
    prompt = m43_prompt() + STABLE_CONTRACT_INTERVENTION
    actual = sha256_text(prompt)
    if actual != STABLE_CONTRACT_HASH:
        raise RuntimeError(f"STABLE_CONTRACT_HASH_MISMATCH:{actual}")
    return prompt
