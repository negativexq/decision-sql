# M2.12 — Deterministic Window IR Compiler

M2.12 tested a window-only semantic IR followed by deterministic PostgreSQL
compilation. The IR arm had exactly one provider call per question and did not
make a second SQL-generation call. Compiled SQL was still represented as
`SqlCandidate(source=WINDOW_COMPILER)` and passed through M1 planning and
execution.

This is an **Internal Window Compositional Stress Suite**, not a general
Text-to-SQL accuracy benchmark. Its deliberate concentration of window
patterns makes it a capability stress test; M2.13 provides the external
PostgreSQL calibration.

## Frozen inputs

- Development: 48 questions, 6 per semantic family.
- Holdout: 32 questions, 4 per semantic family; created and validated but not
  consumed.
- Development SHA-256:
  `bfb04992e860d7e10add010db350e94357fcedb20037540eaf9a0dfc17704ac3`
- Holdout SHA-256:
  `e2cf624233937374e9aeff91cbcae239b6e65151f6ecbb449126b7fc25339fc4`

Gold IR validation was complete before provider calls: development 48/48 and
holdout 32/32 passed IR validation, deterministic compilation, M1 planning,
execution, and compiled-vs-gold result equivalence.

## IR boundary

`WindowQueryIR` contains one queryable source relation, physical outputs, a
bounded pattern enum, and typed computation specifications. Supported patterns
are `LATEST_PER_GROUP`, `TOP_N_PER_GROUP`, `LAG`, `LEAD`,
`RUNNING_AGGREGATE`, `MOVING_AGGREGATE`, `RANKING`, `SHARE_OF_TOTAL`, and
`MIXED_MULTI_WINDOW`. Frames are typed `ROWS` frames; time-based `RANGE`
frames are explicitly out of scope.

The IR forbids arbitrary SQL, SQL expressions, joins, filters, CTE syntax,
window syntax, and authorization fields. Identifiers are resolved against the
server-owned catalog before compilation. The compiler is deterministic and
does not access the provider.

## Development result

Run artifact: `evaluation/results/m212/dev/20260903T025751Z/`

| Arm | Result equivalence | Provider calls | Plan accepted | Executed | Policy rejection |
|---|---:|---:|---:|---:|---:|
| Baseline one-shot | 2/48 (4.17%) | 48/48/0 | 40/48 | 40/48 | 8 |
| Window IR compiler | 0/48 | 48/0/48 | 0/48 | 0/48 | 0 |

The 48 IR calls failed before usable structured IR parsing. No fallback SQL
call was made. Subsequent provider-boundary audits established that this run
was invalid for semantic Window capability comparison: the deterministic
compiler was not reached by a usable model IR. The pre-committed development
screen therefore failed, so the 32-question holdout was not consumed.

Baseline provider usage was 44,351 input tokens and 4,167 output tokens, with
1,734.6 ms average generation latency, 1,635.3 ms p50, and 2,273.6 ms p95.
The IR arm produced no usable token accounting because no IR response parsed.
API cost was not computed; no versioned pricing snapshot was configured.

## Decision

M2.12 did not measure the compiler architecture on this run because the IR arm
never produced a usable proposal. Its historical score remains unchanged, but
its semantic interpretation is `INVALID_FOR_SEMANTIC_CAPABILITY_COMPARISON`
with root cause `PROVIDER_STRUCTURED_OUTPUT_BOUNDARY_FAILURE`. M2.12.1,
M2.12R, and M2.12R.0/R.1 record the subsequent provider-boundary and semantic
slot investigations. M1, the evaluator, FULL_COMPACT, and the baseline SQL
prompt were unchanged. M2.7 and M2.11 holdouts remain untouched.
