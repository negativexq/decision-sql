# M16 — Bounded General QueryPlan V1

M16 operationalizes prior research rather than reopening it. M12R established
server-owned semantic ownership as the strongest measured intervention; M1
remains immutable execution authority and P0 typed-result semantics remain the
correctness authority.

QueryPlan V1 is a deliberately small production/domain contract. It accepts a
server-owned source table, zero to two connected server-owned relationships,
one to three explicit columns, and zero to two typed predicates joined by AND.
The supported predicate operators are `EQ`, `NE`, `LT`, `LTE`, `GT`, `GTE`,
`IS_NULL`, and `IS_NOT_NULL`. Join conditions, identifiers, literal syntax, and
SQL assembly belong to the deterministic compiler. Raw SQL, expressions,
aggregates, ordering, limits, subqueries, CTEs, windows, and set operations are
not part of V1.

The provider selects relationship IDs; it never writes an `ON` clause. A
catalog instance supplies table, column, type, and relationship facts, so the
compiler is reusable with another supplied catalog without becoming a semantic
layer framework.

The fresh experiment compares direct PostgreSQL SQL generation with strict
QueryPlan V1 generation plus the compiler using GPT-5.6 Luna, equal schema
information, zero-shot prompts, and memory/retrieval disabled. Intervention
failures do not fall back to direct SQL. Primary correctness is typed execution
result equivalence using the repaired P0 value-bag comparator, not SQL-string or
weak structural similarity.

D2 differential execution is reused as validation infrastructure only. It can
witness semantic differences; absence of a witness is not an equivalence
claim. Memory remains disabled because P3 did not establish decision-grade
causal benefit. Historical D3/P4 residual mining remains closed, and M3/M12R,
M13, and M14 remain unchanged. No runtime execution mode or automatic router is
added by M16.

The frozen M16 population is 120 fresh internal questions: 90 primary and 30
confirmation, stratified across source projection/filter, one-join
projection/filter, and two-join projection/filter shapes. Defog and BIRD are
not run in this milestone. A positive result authorizes M17 public benchmark
transfer; it does not claim aggregate, window, public-benchmark, or general SQL
coverage.

## Frozen primary result

The 90-case primary run used 180 attempted arm calls with GPT-5.6 Luna. Direct
SQL generation was correct on 65/90 (72.222%); native QueryPlan V1 generation
was correct on 25/90 (27.778%). The paired states were A/B/C/D = 20/45/5/20,
with C−B = −40 and exact two-sided paired p = 4.209852022540872e-09. Gold
integrity was 120/120 for validation, compilation, M1 admission, execution,
and reproducibility. Confirmation was not authorized because the primary gain
gate failed. The classification is
`M16_GENERAL_QUERYPLAN_V1_CAPABILITY_NOT_PROVEN`.

The intervention had 63 calls recorded as provider failures and two returned
plans that produced result mismatches; the compact first-run ledger does not
separate transport failures from malformed structured responses within that
provider-failure count. No semantic repair, judge, retrieval, or fallback call
was used. This negative result applies only to this exact broader QueryPlan V1
contract; it does not invalidate the earlier M3/M12R result.
