# ruff: noqa: E501

"""Compatibility exports for the product-owned Candidate C contract."""

from __future__ import annotations

from app.generation.decision_contract import (
    PRODUCTION_DECISION_CONTRACT_HASH,
    PRODUCTION_DECISION_CONTRACT_VERSION,
    production_decision_prompt,
)
from benchmark.model_contract import sha256_text

STABLE_CONTRACT_VERSION = PRODUCTION_DECISION_CONTRACT_VERSION
STABLE_CONTRACT_HASH = PRODUCTION_DECISION_CONTRACT_HASH

# Compatibility export for historical experiment builders.  The bytes are
# derived from the product-owned prompt instead of being a second contract.
_prompt = production_decision_prompt()
STABLE_CONTRACT_INTERVENTION = _prompt[_prompt.index("\n\n## Final contract check") :]


def stable_contract_prompt() -> str:
    prompt = production_decision_prompt()
    actual = sha256_text(prompt)
    if actual != STABLE_CONTRACT_HASH:
        raise RuntimeError(f"STABLE_CONTRACT_HASH_MISMATCH:{actual}")
    return prompt
