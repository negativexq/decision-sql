# M18 QueryPlan V1 residual repair

M18 is bounded debugging and independent validation. M17 remains a valid
negative result under its precommitted superiority gates: QueryPlan was 86/90
(95.556%) versus DIRECT 79/90 (87.778%), with a +7.778 percentage-point delta,
but the exact paired p-value was 0.11846923828125. Contract acquisition was
90/90 and D was zero; neither observation authorizes a gold-free ensemble or
router.

## Four-case forensic result

The authoritative M17 B-state case IDs were:

| Case | Finding |
| --- | --- |
| `m17-queryplan-primary-044` | Gold and question valid/unambiguous. Predicted relationship 2 selected `customers.sales_rep_id` instead of `sales_representatives.region_id`; compiled result had 40 rows instead of 80. |
| `m17-queryplan-primary-048` | Gold and question valid/unambiguous; the compact M17 artifact preserves only predicted hashes, so the exact predicted field/SQL difference is not recoverable. |
| `m17-queryplan-primary-078` | Same recoverable relationship-selection defect as 044; predicted result had 40 rows instead of 80. |
| `m17-queryplan-primary-091` | Same recoverable relationship-selection defect as 044; predicted result had 40 rows instead of 80. |

Three of four cases therefore supported one repeated, generalizable defect:
two-join relationship-selection ambiguity. The four cases were development
evidence only; they are not re-used in any accuracy denominator.

## One bounded fix

Repeated defect: the provider-facing relationship context did not make both
server-owned relationship endpoints explicit. Fix: render each relationship
with its left table/column and referenced right table/column in the existing
QueryPlan V1 context. This changes neither QueryPlan V1 semantics nor compiler
ownership. WireV2, the compiler, M1, P0, memory, retrieval, and runtime
integration remain unchanged.

The first targeted attempt was invalid because its questions were cosmetic
rephrasings of M17 semantic plans; its partial result is retained separately
and is not evidence. A second attempt used 40 fresh questions and 40 fresh
canonical plans with no M17 overlap. Gold preparation passed 40/40. Provider
contract acquisition passed 40/40, QueryPlan correctness was 39/40 (97.5%),
and DIRECT was 25/40. The relationship-selection diagnostic recurred in 3/40
cases; one was a result mismatch. The targeted gate passed.

The fresh broad validation used 150 cases with the frozen M18 shape mix. The
120-case primary was DIRECT 108/120 versus QueryPlan 120/120 (+10.0 pp,
A/B/C/D 108/0/12/0, exact paired p=0.00048828125). The 30-case confirmation
was DIRECT 24/30 versus QueryPlan 30/30 (+20.0 pp, A/B/C/D 24/0/6/0). Across
all 150 cases, DIRECT was 132/150 and QueryPlan was 150/150 (+12.0 pp,
A/B/C/D 132/0/18/0, descriptive exact paired p=0.00000762939453125).
Contract acquisition was 150/150, with zero QueryPlan compiler, M1-invariant,
execution, or result-mismatch failures. DIRECT had 7 M1 rejections and 11
result mismatches. The broad result therefore passed the precommitted B1-B7
gates and confirmation gates.

Available timing evidence showed aggregate total latency p50/p95 of 1519/1831
ms for DIRECT and 1340/1648 ms for QueryPlan. The runner did not persist
separate wire, canonicalization, compiler, M1, or DB timings, and the provider
did not expose token usage in the persisted proposals; those fields are
reported as not instrumented/not available rather than inferred. Technical
retries, repair calls, judges, retrieval, and memory were zero.

## Classification

`M18_GENERAL_QUERYPLAN_V1_CAPABILITY_VALIDATED`

After repairing one repeated, evidence-supported residual defect and validating
the change only on fresh data, the unchanged bounded QueryPlan V1 semantic
scope materially and significantly outperformed direct SQL generation with the
same GPT-5.6 Luna model while preserving deterministic compilation and M1
safety.

The four M17 cases and the first invalid targeted attempt remain development or
invalid-run evidence only; neither enters the fresh denominators.

M12R established the bounded capability. M13 proved its runtime integration.
M14 measured operation. M16 operationalized prior research rather than
reopening it. M18 inspects only the four M17 native mismatches and tests one
fresh fix. Operational failures produce bounded implementation evidence, not
architecture reselection. M1 remains immutable execution authority, P0 typed
results remain correctness authority, and D2 is validation infrastructure only.
Memory remains disabled because P3 did not establish decision-grade causal
benefit; historical D3/P4 residual mining remains closed.

No Defog/BIRD benchmark, runtime integration, QueryPlan V2 expansion, repair
call, judge, memory, retrieval, or ensemble was run.
