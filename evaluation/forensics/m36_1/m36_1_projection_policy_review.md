# M36.1 — Projection Contract Policy Review

Provider calls: `0`.

M35R1 and M36 artifacts were read-only inputs. The benchmark content hash remains:

```text
f30f5bc061920c59f7a7b58e85d8fcb911ab7dbd83fb1505a2412c7043dba9a8
```

## Public-contract audit

The public model contract says the model must return one JSON object with four fields and one read-only SELECT. It does **not** say:

```text
Return exactly and only the SQL columns requested by the question.
Do not include additional informative columns.
```

The governed prompt also does not say this. The evaluator does enforce the semantic target’s exact `column_count`, but that result contract is evaluator-side and hidden from the model.

Therefore:

```text
STRICT_PROJECTION_POLICY_HIDDEN_OR_UNSPECIFIED = YES
```

## Adjudication result

| Case | Adjudication | Responsibility | Core semantics | Normalized match |
|---|---|---|---:|---:|
| `commerce_05` | IMPLIED_PROJECTION | CONTRACT_AMBIGUITY | YES | YES |
| `fleet_03` | IMPLIED_PROJECTION | CONTRACT_AMBIGUITY | YES | YES |
| `fleet_05` | IMPLIED_PROJECTION | CONTRACT_AMBIGUITY | YES | YES |
| `support_01` | IMPLIED_PROJECTION | CONTRACT_AMBIGUITY | YES | YES |
| `support_02` | UNDERSPECIFIED_PROJECTION | BENCHMARK_REVIEW_REQUIRED | YES* | NO |
| `support_03` | IMPLIED_PROJECTION | CONTRACT_AMBIGUITY | YES | YES |
| `support_05` | UNDERSPECIFIED_PROJECTION | CONTRACT_AMBIGUITY | YES | YES |

`YES*` for `support_02` means Luna follows the visible question/context. It does not match the frozen references because the references omit the visible `event_at > opened_at` SLA rule.

## Projection policy tradeoff

### KEEP_STRICT_PROJECTION

Not recommended as the current public policy. Exact columns are useful for deterministic evaluation, but the current model-facing contract does not communicate exact projection semantics. This penalizes reasonable additions such as entity names and diagnostic SLA fields.

### SEMANTIC-SUPERSET-TOLERANT / RELAX_PROJECTION

Not recommended globally. It would make extra-column dumping an easy way to evade a meaningful output contract and would complicate typed result comparison.

### CASE-SPECIFIC_PROJECTION

Would be fairer for natural-language tasks, but requires every case to declare whether projection is exact and which fields are required. It is a possible long-term design, not a policy to silently introduce into this frozen benchmark.

### CLARIFY_STRICT_PROJECTION

Recommended. Preserve strict exact-column evaluation when exact projection is intended, but make the rule public and case-facing. A future contract should distinguish:

```text
required output fields
optional informative fields
exact projection required: yes/no
```

This avoids accepting arbitrary extra columns while avoiding hidden evaluator expectations.

## Important semantic review: `support_02`

Projection normalization was independently executed on base and every fixture. Six cases matched after removing only Luna-only columns. `support_02` did not.

Visible authority says:

```text
The first agent_response event later than opened_at plus the plan SLA is a breach.
```

Luna applies `e.event_at > t2.opened_at` before taking `MIN(event_at)`. Both frozen reference implementations take `MIN(event_at)` for `agent_response` events without that lower-bound predicate. The frozen reference summaries expect one base ticket, while the visible-rule implementation returns 125 tickets in the diagnostic execution.

This is not a projection-only disagreement. It is a benchmark semantic review candidate that M36’s projection-only label missed. No benchmark file is changed here.

## Recommendation

Policy: **CLARIFY_STRICT_PROJECTION**.

Next milestone: **M36.2 — Projection Policy Repair**.

Why:

- Six of seven projection cases are reasonable answers rejected only by hidden exact-column enforcement.
- One case also exposes a separate frozen reference/context semantic inconsistency.
- A model ablation before resolving these contracts would confound model behavior with benchmark policy.

M36.2 should freeze a public projection policy and independently reconcile `support_02`’s visible SLA rule against its references before any new Luna experiment.

What stays frozen:

- M35R1 raw outputs and score.
- M35R1 benchmark content and contract hashes.
- M36 forensic artifacts.

No M37 model experiment is justified until that policy/semantic audit is complete.
