"""Command-line entry point for the offline M9.5R safety-boundary audit."""

from __future__ import annotations

import json

from evaluation.m95r_binding_safety_audit import run

if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
