# DecisionSQL

DecisionSQL is **Governed Text-to-SQL for Enterprise Analytics**.

> The LLM proposes. Deterministic software decides what may execute.

The system keeps generation separate from authority. A natural-language
question is combined with server-owned schema and business context, then an
LLM proposes PostgreSQL. The proposal remains untrusted until deterministic
SQL policy, catalog checks, PostgreSQL `EXPLAIN` cost gates, and read-only
execution accept it.

```text
Question
  -> server-owned context
  -> LLM SQL proposal
  -> deterministic SQL safety and planning
  -> PostgreSQL EXPLAIN cost gate
  -> read-only, timed, bounded execution
  -> result and provenance
```

## What is implemented

- Python 3.12, FastAPI, Pydantic Settings, SQLAlchemy, Alembic, `sqlglot`,
  pytest, Ruff, mypy, and OpenTelemetry.
- PostgreSQL-only V1 with separate admin and restricted reader connections.
- Deterministic M1 SQL safety and planning: one statement, SELECT/SELECT-CTE
  policy, catalog/object and function policy, EXPLAIN cost gates, read-only
  transactions, timeouts, and bounded results.
- An optional one-shot OpenAI-compatible Text-to-SQL generation path. Model
  output becomes an untrusted `SqlCandidate` and always passes through M1.
- Reproducible internal generation experiments and a bounded typed Window IR
  with a deterministic PostgreSQL compiler.
- Evaluation-only adapters for Defog SQL-Eval PostgreSQL and BIRD Mini-Dev
  PostgreSQL. Their benchmark executors are isolated from the product M1 path.

There is no public Text-to-SQL endpoint yet. Tenant policy, RLS, governed
semantic metrics, answer synthesis, and user-facing product integration remain
future work.

## External evaluation

These are execution-based results from different datasets and evaluators. They
must not be averaged into a single accuracy number.

| Benchmark | Metric | Result |
| --- | --- | ---: |
| Defog SQL-Eval PostgreSQL — Classic | Exact | **142/210 (67.62%)** |
| Defog SQL-Eval PostgreSQL — Advanced | Exact | **48/64 (75.00%)** |
| Defog Advanced — official Window category | Exact | **6/8 (75.00%)** |
| BIRD Mini-Dev PostgreSQL | EX | **225/500 (45.00%)** |
| BIRD — Simple | EX | **87/148 (58.78%)** |
| BIRD — Moderate | EX | **108/250 (43.20%)** |
| BIRD — Challenging | EX | **30/102 (29.41%)** |

The BIRD run parsed 499/500 generated queries and executed 496/500; the main
loss was semantic result correctness rather than basic SQL executability.
Defog and BIRD provenance and run details are retained under
[`evaluation/external/`](evaluation/external/) and the milestone documents.

## Current limitations

Derived and temporal semantics remain weak across the external evidence:

- Defog ratio: **12/35 (34.29%)** exact.
- BIRD ratio/division: **17/101 (16.83%)** EX.
- BIRD temporal: **4/19 (21.05%)** EX.

The current bottleneck is semantic/compositional grounding, especially derived
business metrics and temporal logic, rather than SQL syntax validity alone.

## Window research

DecisionSQL includes a deterministic compiler for a bounded, typed Window IR.
Gold IR compiled, passed safety planning, executed, and matched results on
**80/80** controlled development and holdout cases. LLM-generated Window IR
did not outperform direct SQL. The original 48-question suite is therefore
retained as an **Internal Window Compositional Stress Suite**, not as a general
product-accuracy or general Window benchmark.

## Local startup

Requirements: Docker with Compose support.

```bash
cp .env.example .env
docker compose up --build
```

The database becomes healthy, then migrations and deterministic seed data run
before the API starts. Check the API:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"ok","database":"ok"}
```

To include the optional Jaeger-compatible service:

```bash
docker compose --profile observability up --build
```

## Development

With Python 3.12 and the development dependencies installed:

```bash
python -m pytest
ruff check .
mypy app demo
```

See [`docs/architecture.md`](docs/architecture.md),
[`docs/security.md`](docs/security.md),
[`docs/evaluation.md`](docs/evaluation.md), and
[`docs/evaluation-research-history.md`](docs/evaluation-research-history.md).

## Roadmap

Completed: M0 Foundation, M1 Deterministic SQL Safety, M2 generation and
evaluation research, M2.12 Window compiler research, M2.13 Defog external
calibration, and M2.14 BIRD external validation.

Broad benchmark acquisition is now frozen. Existing evidence consists of the
Defog PostgreSQL benchmark, BIRD Mini-Dev PostgreSQL, the internal DecisionSQL
benchmark, and the internal Window stress suite.

Next product milestone: **M3 — Governed Semantic Metrics**.

Later work may cover temporal/value grounding, verified query memory,
clarification and controlled repair, answer synthesis/provenance, final
external evaluation, UserContext/RLS, and product integration.
