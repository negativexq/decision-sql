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
- A governed semantic catalog and deterministic metric compiler for
  catalog-covered business metrics. M3 validates this path internally; it is
  not yet the default production route.
- Reproducible internal generation experiments and a bounded typed Window IR
  with a deterministic PostgreSQL compiler.
- Evaluation-only adapters for Defog SQL-Eval PostgreSQL and BIRD Mini-Dev
  PostgreSQL. Their benchmark executors are isolated from the product M1 path.

There is no public Text-to-SQL endpoint yet. Tenant policy, RLS, answer
synthesis, and user-facing product integration remain future work.

## Two generation paths

For arbitrary analytics, the current path is direct Text-to-SQL followed by
M1. For catalog-covered business metrics, the validated M3 path selects a
metric and dimensions, then lets the server-owned semantic catalog and
deterministic compiler own physical mappings, aggregation, formulas, grain,
cardinality, eligibility filters, scale, and zero/NULL policy. Both paths
remain subject to M1; governed routing is feature-flagged/default-off pending
the next integration milestone.

The semantic catalog is organized around entities, relationships, dimensions,
measures, and metrics. The LLM does not provide physical columns, numerator or
denominator definitions, join paths, or formulas.

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

M2.14.1 found the largest BIRD ratio failure classes were wrong aggregation
(30), missing filters (13), arithmetic structure (13), join path (11), and
join fanout (6). This evidence motivated the governed metric compiler; it does
not imply that M3 solves arbitrary Text-to-SQL.

## Governed metric evidence

These results apply only to frozen internal questions whose requested business
metric is covered by the governed semantic catalog.

| Evaluation | Direct SQL | Governed compiler | Delta |
| --- | ---: | ---: | ---: |
| M3 DEV | 26/48 (54.17%) | 48/48 (100.00%) | +45.83 pp |
| M3 HOLDOUT | 13/32 (40.63%) | 32/32 (100.00%) | +59.38 pp |

The compiler itself reached 32/32 independent reference targets, with 80/80
correctly grounded DEV/HOLDOUT requests compiling to equivalent results.

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
calibration, M2.14 BIRD external validation, M2.14.1 semantic failure audit,
and M3 Governed Semantic Metrics.

Broad benchmark acquisition is now frozen. Existing evidence consists of the
Defog PostgreSQL benchmark, BIRD Mini-Dev PostgreSQL, the internal DecisionSQL
benchmark, the Internal Window Compositional Stress Suite, and the M3 governed
metric benchmark.

Next product milestone: **M3.4 — Feature-Flagged Governed Metric Routing &
Observability**.

Later work may cover semantic contract hardening, temporal/value grounding,
verified query memory, clarification and controlled repair, answer
synthesis/provenance, final external evaluation, UserContext/RLS, and
production hardening.
