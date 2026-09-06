# M11.2P2 — Bounded Counterexample Differential Diagnostic

M11.2P2 implements the selected D2B direction: independently authored,
deterministic PostgreSQL fixture-state counterexample search. It reuses M1
read-only admission and execution in an isolated diagnostic database and has
bounded statement timeout, transaction scope, connection lifetime, and result
row limits.

The only positive semantic state is `NON_EQUIVALENCE_WITNESSED`. The states
`NO_COUNTEREXAMPLE_FOUND`, `EXECUTION_INCONCLUSIVE`, `FIXTURE_INVALID`, and
`QUERY_NOT_EXECUTABLE_UNDER_DIAGNOSTIC_CONTRACT` are abstention or safety
outcomes. In particular, `NO_COUNTEREXAMPLE_FOUND` is never equivalence.
Typed values preserve NULL, Decimal, exact-case strings, dates/timestamps, and
row multiplicity. Unentitled row order is not semantic authority.

## Attempt history and validation

Validation corpus v1 is preserved as a failed first attempt. Its two objective
control defects were a PostgreSQL-valid empty-target SELECT and a Window-frame
pair without deterministic top-level order entitlement. Corpus v2 changes only
those controls. A non-authoritative aborted run (attempt 2) then exposed and
reproduced the `VALUE_BAG` raw-row-order hashing defect. The repaired engine
uses comparison-mode-aware semantic hashes: bag order is ignored while
multiplicity and typed values remain significant. Authoritative attempt 3
restarts the entire v2 corpus from recreated diagnostic PostgreSQL state and
passes V1–V7 with 0/12 equivalent false witnesses and 18/18 required
non-equivalent witnesses. The three P1 controls—literal case, OFFSET, and
JOIN USING key—are all witnessed. The consumed 24/15 populations are excluded
from independent validation.

## Shadow result

The validated mechanism was applied in shadow mode to exactly 24 STATIC and 15
COMPOUND historical pairs, preserving OFF/ON arms. It produced independent
counterexamples for 20/24 STATIC and 12/15 COMPOUND pair cases, or 32/39
pair-level cases overall. These are behavioral counterexample evidence only;
they do not assign a hidden generation-failure family or establish causality.

## Authority and operating rule

This diagnostic remains additive, diagnostic-only, and independent from the
generator. It does not weaken M1 or change production behavior. The LLM
proposes. Deterministic software decides what may execute. Don’t improve what
looks suspicious. Improve the decision boundary the evidence proves is
failing.

After M11.2P2, new research/review milestones are not the default.
Implementation experiments are the default. A new review is opened only when
a failed experiment exposes a genuinely new scientific or evaluator-integrity
question. Once a bounded intervention or diagnostic direction passes selection
gates, implement it. Do not repeatedly re-select it.
