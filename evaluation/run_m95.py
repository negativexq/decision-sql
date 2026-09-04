"""Command-line entry point for the offline M9.5 binding validation."""

from __future__ import annotations

import json

from evaluation.m95_binding_protocol import run

if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
