"""Run the frozen, provider-free M9.5R.1 binder-v2 validation."""

from __future__ import annotations

import json

from evaluation.m95r1_binding_v2_validation import run

if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
