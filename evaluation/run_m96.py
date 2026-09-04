"""Run the offline M9.6 evaluator-applicability strategy audit."""

from __future__ import annotations

import json

from evaluation.m96_evaluator_applicability_audit import run, write_fixtures

if __name__ == "__main__":
    result = run()
    write_fixtures(result)
    print(json.dumps(result, indent=2, sort_keys=True))
