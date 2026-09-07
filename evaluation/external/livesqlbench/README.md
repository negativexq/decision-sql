# LiveSQLBench Base-Lite adapter

This package is an evaluation-only, provider-free adapter for the public
LiveSQLBench Base-Lite PostgreSQL release. It preserves upstream JSONL rows,
classifies `Query` versus `Management`, introspects the official PostgreSQL
image, renders deterministic schema context, and keeps reference fields out of
future provider runtime payloads.

The public Hugging Face artifact intentionally leaves `sol_sql`, `test_cases`,
and case-level `external_knowledge` empty. The adapter therefore refuses to
claim a benchmark score until a legitimately obtained complete GT/test-case
artifact is integrated.
