# M19 — QueryPlan V1 public benchmark transfer

M18 established fresh internal QueryPlanV1 capability. M19 measures transfer
to public benchmark cases whose gold semantics are independently proven
representable by the unchanged QueryPlanV1 contract.

Gold SQL is used only for offline coverage labeling, deterministic gold-plan
lowering, and evaluator integrity. Gold-based representability is not a
deployable runtime router. The oracle applicability scores are non-deployable
upper bounds, not production or hybrid benchmark scores.

The adapter builds server-owned catalogs from benchmark PostgreSQL metadata
and declared foreign keys. It rejects unsupported or ambiguous SQL
conservatively. QueryPlanV1 remains limited to one source, at most two
connected server-owned joins, one to three direct columns, and at most two
typed conjunctive predicates. QueryPlanWireV2, the M18 relationship-endpoint
rendering, the compiler, M1, and the public evaluators remain unchanged.

M19 uses GPT-5.6 Luna (`gpt-5.6-luna`) with the frozen M15 provider-default
decoding configuration, memory/retrieval/reranking off, no repair, and no
judge. The direct arm is named `DIRECT_PARITY_CONTROL` because both arms use
the shared parity context; M15 remains the frozen full-benchmark baseline.

The measured transfer is coverage-limited: only 1/210 Classic, 0/64
Advanced, and 26/500 BIRD cases were representable under the authoritative
catalogs and frozen semantics. The paired live run therefore contains 27
cases and 54 semantic provider calls. On the BIRD supported slice, the direct
arm was correct on 20/26 and QueryPlan on 22/26. The pooled result is
descriptive and does not claim a deployable applicability selector.

M18 established fresh internal QueryPlanV1 capability; M19 measures its
public-schema transfer. Coverage results do not authorize adding aggregation,
ordering, windows, subqueries, a router, or runtime integration.

## Frozen run result

Coverage was 1/210 (0.476%) for Defog Classic, 0/64 (0%) for Defog
Advanced, and 26/500 (5.2%) for BIRD. The representable public total was
27/774. The catalog/gold-lowering integrity gate passed for all 27 cases;
the representable case IDs and catalog hashes are in the M19 fixture.

On the representable slices, Classic was 1/1 direct and 1/1 QueryPlan. BIRD
was 20/26 direct and 22/26 QueryPlan (A/B/C/D = 18/2/4/2, paired exact
p=0.6875). The pooled descriptive result was 21/27 direct and 23/27
QueryPlan (A/B/C/D = 19/2/4/2, p=0.6875). No Advanced case was
representable. These numbers are diagnostic measurements, not valid M19
transfer evidence.

The first provider attempt exposed a runner defect: the Defog gold-integrity
preflight incorrectly rejected a valid lowered candidate, but the run still
continued to provider calls for BIRD. The evaluator path was corrected and the
27-case run was repeated. Because this changed the evaluator/runner after
provider exposure, the attempt is classified `M19_VALIDATION_INVALID` under
the milestone protocol. No public-transfer claim is made until a clean fresh
attempt is run from the corrected pre-provider gate.

The gold-based, non-deployable what-if upper bounds from the diagnostic rerun
were 141/210 Classic
exact, 47/64 Advanced exact, and 233/500 BIRD execution. The latter is only
46.6%, because the frozen QueryPlan slice replaced 20 frozen M15 BIRD
correct outcomes with 22 current QueryPlan outcomes. No gold-free
applicability mechanism is claimed.
