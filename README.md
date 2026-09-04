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
  |
  v
Governed applicability
  |
  +-- catalog-covered metric
  |      |
  |      v
  |   Governed semantic grounding
  |      |
  |      v
  |   Deterministic metric compiler
  |
  +-- residual direct
         |
         v
     Verified Query Memory
         |
         v
     LLM SQL proposal
         |
         +-------------------+
                             |
                             v
               Deterministic M1 trust boundary
                             |
                   sqlglot AST / policy
                             |
                   PostgreSQL EXPLAIN
                             |
                   read-only execution
                             |
                        bounded result
                             |
                    versioned evaluation
                    /         |          \
                  V1       V2 explicit   DUAL_SHADOW
             historical/      explicit    V1 authoritative;
              default        contract     V2 diagnostic
                             |
                         provenance
```

## Current system

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
  not yet the default production route. M3.5 adds stable semantic identity,
  lifecycle/version metadata, and execution provenance.
- A provider-evaluated, verified-query-memory path for residual direct SQL
  composition. M4 uses deterministic local retrieval and M4.1 integrates it
  behind explicit OFF/SHADOW/ON controls; it remains disabled by default.
  Retrieved SQL is context only and generated SQL still passes M1.
- Reproducible internal generation experiments and a bounded typed Window IR
  with a deterministic PostgreSQL compiler.
- Evaluation-only versioned result evaluation: V1 preserves historical and
  default semantics, V2 is an explicit contract-aware path, and DUAL_SHADOW
  keeps V1 authoritative while recording V2 diagnostics. This does not change
  M1's SQL execution authority or infer result contracts automatically.
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
remain subject to M1; governed routing is feature-flagged and default-off after
M3.4 integration validation.

The semantic catalog is organized around entities, relationships, dimensions,
measures, and metrics. The LLM does not provide physical columns, numerator or
denominator definitions, join paths, or formulas.

## Current combined-system evidence

M7 evaluated the current accepted architecture as one system on a new frozen
internal mixed-workload benchmark: `decisionsql-combined-mixed-workload`,
version `m7-combined-v1`, with 150 answerable cases (100 DEV, 50 HOLDOUT).
This is execution-equivalence evidence for a frozen internal workload, not
production accuracy or general Text-to-SQL accuracy.

| Arm | DEV | HOLDOUT | Combined |
| --- | ---: | ---: | ---: |
| P0 direct one-shot | 35/100 (35%) | 16/50 (32%) | 51/150 (34%) |
| P1 current combined architecture | 63/100 (63%) | 36/50 (72%) | 99/150 (66%) |

The combined gain was **+32 percentage points**. The governed path resolved all
30 catalog-covered governed-metric cases (30/30); the residual direct path
resolved 69/120 (57.5%). Routing accuracy was 130/150 (86.67%), with 100%
governed recall, 60% governed precision, 20 false-governed routes, and no
false-direct routes. P1 averaged 3,186 ms versus 2,757 ms for P0 in this
evaluation, roughly 429 ms slower; these are evaluation measurements, not an
SLA.

The 51 incorrect P1 cases were manually and deterministically attributed. The
largest residuals were `ROUTING_ERROR` (13, 25.5%),
`QUERY_STRUCTURE_ERROR` (12, 23.5%), and `FILTER_ERROR` (9, 17.6%), together
66.7% of residual failures. The resulting classification is
**COMBINED_ARCHITECTURE_PARTIALLY_VALIDATED** with
**RESIDUAL_FAILURES_CONCENTRATED**. Full methodology, provenance, and hashes
are in [`docs/m7-combined-product-evaluation.md`](docs/m7-combined-product-evaluation.md).

## Result equivalence and evaluation provenance

SQL-string equality is not the correctness criterion. Historical evaluator V1
remains frozen so M7, M8, and M9 results stay reproducible. M9–M9.3 were
evaluation-only research; they did not change SQL generation or runtime
behavior.

M9.1 separated the 31 historical result-shape/projection failures into 10
evaluator artifacts, 19 genuine projection-contract violations, and 2 deeper
semantic/computation errors. M9.2 then validated a deterministic contract
candidate that recovered the known harmless result supersets without accepting
known genuine errors. M9.3 independently validated the unchanged candidate:

| Validation | Legitimate positives/controls | False accepts |
| --- | ---: | ---: |
| M9.2 frozen regression | 85/85 retained or recovered; 30/30 synthetic | 0 across 28 historical negatives |
| M9.3 independent corpus | 70/70 accepted | 0 across 90 negative/invalid cases |

The M9.3 corpus contained 160 cases: 40 contract-equivalent positives, 30
strict controls, and 90 projection, grain, value, ordering/duplicate, or
invalid-contract negatives. It had no M9.1/M9.2 case overlap or fixture reuse;
the candidate also passed 12/12 metamorphic and 12/12 mutation-safety checks.
This validates evaluation semantics on a frozen internal corpus, not universal
evaluator correctness or a benchmark score increase.

Evaluator V1 is the historical frozen behavior. The result-equivalence V2
candidate is independently validated and is now available through an explicit
versioned evaluation path. V1 remains the default; DUAL_SHADOW keeps V1
authoritative and preserves any V1/V2 disagreement. New V2 results carry
bounded evaluator, contract, comparator, and matched-reference provenance.
V2 requires an explicit valid contract; M9.4 did not add automatic or
LLM-generated contract inference. See the detailed
[M9 direct-path audit](docs/m9-direct-path-query-structure-audit.md),
[M9.1 result-shape audit](docs/m91-direct-result-shape-projection-contract-audit.md),
[M9.2 contract regression](docs/m92-result-equivalence-contract-regression-suite.md),
[M9.3 independent validation](docs/m93-result-equivalence-v2-independent-validation.md),
and [M9.4 versioned evaluator integration](docs/m94-versioned-evaluator-v2-integration-provenance.md).

Before the M10 clean rebaseline could begin, the V2 path exposed a missing
generated-result binding protocol: the execution path provides `QueryExecution`,
while V2 requires a `BoundResult` with semantic bindings. The offline
`benchmark-result-binding-v1` study was rejected as unsafe after 8 incorrect
semantic bindings across 117 independent projections and the
`A17_SELF_JOIN_AMBIGUITY` false binding. A follow-up safety-boundary audit
identified a narrower fail-closed Boundary B that retained 109/109 known-correct
projection bindings and 49/52 correct-case evaluability opportunities in
counterfactual analysis. This does not validate binder-v2: the M9.5 validation
set is consumed, no M10 cases were constructed or run, and a new binder version
requires a new independent validation corpus.
Detailed records are in the [M9.5 protocol report](docs/m95-benchmark-result-contract-binding-protocol.md)
and [M9.5R safety-boundary audit](docs/m95r-result-binding-safety-boundary-audit.md).

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

The external evidence still shows weak derived and temporal semantics. In the
combined internal evaluation, M8 showed that routing errors were not the main
recoverable bottleneck. M9 showed that broad structure failures were real but
mostly concentrated in result-shape/projection mismatches rather than a
generic SQL-planning deficit. M9.1–M9.3 then separated evaluator artifacts
from genuine projection violations and validated a narrow contract-aware
candidate without changing historical scores.

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

## Verified query memory evidence

M4 evaluated a frozen internal direct-path benchmark using 50 independently
verified examples. The memory arm used the same model, schema context, and M1
path as the baseline, with only the retrieved examples added to the prompt.

| Evaluation | Direct SQL | Verified memory | Delta |
| --- | ---: | ---: | ---: |
| M4 DEV | 15/48 (31.25%) | 29/48 (60.42%) | +29.17 pp |
| M4 HOLDOUT | 10/32 (31.25%) | 17/32 (53.13%) | +21.88 pp |

These results apply only to the frozen internal residual direct-path questions;
they are not general Text-to-SQL accuracy. Verified Query Memory is not
production-default and has no automatic repair or learning-from-traffic path.

## M5–M9 research status

M5 post-SQL semantic verification was too noisy for a broad production
correctness gate. Its pre-SQL selective-answering follow-up also did not
justify a production subsystem: R0 and R2 were rejected, while R1 remained
unresolved because of intermittent provider structured-output reliability.
Selective answering is parked and is not part of the current runtime.

M6 value-grounding research concluded **VALUE_GROUNDING_WEAK_EVIDENCE**: no
confirmed or plausible historical value-grounding failures were found. A
no-guessing resolution contract remains useful future design guidance, but a
resolver, fuzzy matching, embeddings, and M6.1 are parked.

M7 remains the current combined-system product evaluation: 66% execution
equivalence on its frozen internal workload, not production or general
Text-to-SQL accuracy. M8 found routing was not the primary recoverable
bottleneck. M9 found structural failures were real but fragmented, with
result-shape/projection dominating its structural labels. M9.1–M9.3 validated
the evaluation-contract direction, and M9.4 integrated that path explicitly
without changing the default V1 semantics. See the complete research records in
[`docs/m50-semantic-verification-signal-audit.md`](docs/m50-semantic-verification-signal-audit.md),
[`docs/m501-adjudicated-semantic-verification-corpus.md`](docs/m501-adjudicated-semantic-verification-corpus.md),
[`docs/m5r-selective-answering-research.md`](docs/m5r-selective-answering-research.md),
[`docs/m6-value-grounding-evidence-contract.md`](docs/m6-value-grounding-evidence-contract.md),
and [`docs/m7-combined-product-evaluation.md`](docs/m7-combined-product-evaluation.md),
plus the M8–M9.4 documents linked above.

The M2.8 provider-generated ResultShape proposal remains rejected; the later
M9 evaluator work is offline contract/evaluation infrastructure, not a revival
of that provider stage.

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
The M3.4 routing contract and observability validation are documented in
[`docs/m34-governed-routing-observability.md`](docs/m34-governed-routing-observability.md).
Semantic contract lifecycle and provenance are documented in
[`docs/m35-semantic-contract-hardening.md`](docs/m35-semantic-contract-hardening.md).
Verified Query Memory evaluation and its frozen corpus are documented in
[`docs/m4-verified-query-memory.md`](docs/m4-verified-query-memory.md); its
default-off runtime integration is documented in
[`docs/m41-verified-memory-integration-observability.md`](docs/m41-verified-memory-integration-observability.md).

## Roadmap

Completed: M0 Foundation, M1 Deterministic SQL Safety, M2 generation and
evaluation research, M2.12 Window compiler research, M2.13 Defog external
calibration, M2.14 BIRD external validation, M2.14.1 semantic failure audit,
M3 Governed Semantic Metrics, M3.4 Governed Routing & Observability, M3.5
Semantic Contract Hardening, M4 Verified Query Memory, and M4.1 Verified Query
Memory Integration & Observability, M5–M6 research, and M7 Combined Product
Evaluation, M8 Routing Error Audit, M9 Direct-Path Query Structure Audit,
M9.1 Result-Shape / Projection Contract Audit, M9.2 Result Equivalence Contract
Regression, M9.3 Result Equivalence V2 Independent Validation, and M9.4
Versioned Evaluator V2 Integration & Provenance. M9.5 Benchmark Result-Contract
Binding Protocol was rejected as unsafe, and M9.5R identified a bounded
fail-closed safety boundary for a new experiment.

Broad benchmark acquisition is now frozen. Existing evidence consists of the
Defog PostgreSQL benchmark, BIRD Mini-Dev PostgreSQL, the internal DecisionSQL
benchmark, the Internal Window Compositional Stress Suite, and the M3 governed
metric benchmark.

M3.4 Feature-Flagged Governed Metric Routing & Observability and M3.5 Semantic
Contract Hardening are now validated as explicit, bounded integration and
governance layers. M4 Verified Query Memory is accepted for the frozen internal
direct-path evaluation, and M4.1 integrates it behind a default-off runtime
feature mode. The production routing default remains **OFF**; activation is a
separate operational decision. M4.1 does not change the frozen retriever or
corpus. M7 shows a substantial gain from the combined architecture on its new
internal workload, but its 66% execution-equivalence result is not a broad
production claim. M5 selective answering and M6 value grounding remain parked;
no related runtime subsystem has been added. M9.3 independently validated the
result-equivalence V2 candidate, and M9.4 integrated it as an explicit,
versioned evaluation path. Evaluator V1 remains historical and default;
DUAL_SHADOW remains V1-authoritative.

M10 is blocked before corpus construction because no independently validated
generated-result binding protocol was available. The next milestone is
**M9.5R.1 — Fail-Closed Result Binder V2 & New Independent Validation**. It must
preserve binder-v1, implement only the audited fail-closed boundary, and use
new independent cases. Only if that succeeds should **M10 — Clean Residual
Rebaseline** run as a new frozen internal evaluation identity using explicit V2
provenance and benchmark-owned contracts; it will not rescore M7. Public
revalidation remains deferred until after M10, a measured generation or
architecture intervention, and new independent internal validation.
