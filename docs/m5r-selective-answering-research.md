# M5R — Selective Answering Under Ambiguity and Unanswerability

## 1. Executive conclusion

M5R conclusion: `SELECTIVE_ANSWERING_RESEARCH_SUPPORTS_EXPERIMENT`.

The research supports one bounded, provider-free-first experiment, but not a
runtime feature. DecisionSQL has a real product boundary before SQL
generation: some requests have one supported interpretation, some have more
than one, and some request concepts absent from the governed data context.
That boundary is materially different from post-SQL semantic verification.

The recommended primary future experiment is **R2 — interpretation-first
hybrid**, compared directly with R0 deterministic rules and R1 direct
classification. R2 fits the DecisionSQL invariant best because a model may
propose interpretations while deterministic software validates what is
supported. This is a research recommendation only; no answerability,
clarification, or abstention runtime exists after M5R.

If implemented later, the boundary should sit **after existing governed
applicability is considered and before residual direct generation**. Explicit
catalog-backed governed requests should use a deterministic fast path. The
residual direct branch should be evaluated for answerability before M4, while
complexity alone must not trigger intervention.

## 2. Why the question moves before SQL

M5.0 found that broad post-SQL signals were too noisy. M5.0.1 showed that a
purpose-built contradiction corpus can expose narrow signals, but the full
`semantic-verifier-v1` HARD aggregate remained below safe production precision:

- DEV: 67.27% precision and 18.75% false-positive rate
- HOLDOUT: 68.75% precision and 20.83% false-positive rate

Three narrow diagnostics were promising—`POSSIBLE_ENTITY_COUNT_FANOUT`,
`UNEXPECTED_CARTESIAN_JOIN`, and `UNREQUESTED_LIMIT`—but they are not a
general correctness gate. They remain research-supported diagnostics only.

The product question is therefore earlier: is it safe to generate SQL at all?
This is not a generation repair problem. A forgotten `GROUP BY` is a model
failure; a request such as “best products” with two legitimate business
meanings is a user-intent boundary.

## 3. Current trust architecture

The current authority chain remains:

```text
question
  -> M3 governed applicability
       -> governed metric grounding -> deterministic compiler -> M1
  -> residual direct analytics
       -> optional M4 verified-memory context -> model SQL -> M1
```

M1 remains the sole SQL planning, safety, and execution authority. M3 owns
server-defined business metrics and M3.5 owns their versioned contract
identity. M4 supplies trusted examples only as generation context; retrieved
SQL is never execution authority. M5R must not weaken or reorder any of
these guarantees.

## 4. DecisionSQL definitions

Let `C` be the server-owned runtime context: physical schema, relationships,
business glossary, active semantic contract, lifecycle policy, and current
routing capabilities. Let `I(q, C)` be the set of materially different,
linguistically plausible interpretations of question `q` that are supported
by `C`.

The cardinality model is useful, with one governance exception:

| Condition | Proposed product state |
| --- | --- |
| zero supported interpretations | `ABSTAIN` / `UNANSWERABLE` |
| exactly one supported interpretation | `ANSWER` / `ANSWERABLE` |
| more than one supported interpretation | `CLARIFY` / `AMBIGUOUS` |
| several surface readings but one explicit server-owned canonical definition | `ANSWER` via governed default |

An interpretation is **supported** only when its entities, relationships,
dimensions, measures/metrics, and required filter slots can be mapped to
server-owned metadata. Prose plausibility alone is insufficient.

An interpretation is **materially different** when choosing it can change the
population, grouping, measure, filter, ranking basis, or result meaning. SQL
aliases, join order, CTE versus subquery, and equivalent aggregate idioms are
not separate interpretations.

**ANSWERABLE** means exactly one supported materially relevant interpretation
exists after applying authoritative defaults. It does not mean the future SQL
will necessarily be correct.

**AMBIGUOUS** means at least two supported, plausible, materially different
interpretations remain and no canonical default resolves them.

**UNANSWERABLE** means the required concept cannot be derived from the
supported schema/contract. It is not merely difficult, novel, or likely to
produce poor SQL.

**CLARIFICATION** is one bounded request for the single unresolved semantic
slot, offering server-supported alternatives where possible. It must happen
before SQL generation and must not start an open-ended agent loop.

**ABSTENTION** is a bounded statement that the requested information is not
supported by available governed data. It must not invent a closest column,
metric, relationship, or SQL query.

## 5. Natural DecisionSQL taxonomy

The following is a research fit matrix, not a runtime classifier.

| Family | DecisionSQL example | Fit now | Action | Deterministic detection | Model interpretation |
| --- | --- | --- | --- | --- | --- |
| `AMBIGUOUS_METRIC` | “Show the best products.” Could mean completed revenue or item quantity. | HIGH_VALUE_NOW | CLARIFY | Partial; catalog names/helpful cues can find candidates | Useful for candidate enumeration |
| `AMBIGUOUS_DIMENSION` | “Show order performance by sales.” Could mean representative or sales status. | NATURAL_BUT_LOW_FREQUENCY | CLARIFY | Partial when two active dimensions match equally | Likely needed for language mapping |
| `AMBIGUOUS_FILTER_TARGET` | “Show large orders.” Could refer to total, item count, or payment amount. | NATURAL_BUT_LOW_FREQUENCY | CLARIFY | Weak without a contract | Useful for alternatives |
| `AMBIGUOUS_FILTER_CRITERIA` | “Show high-value customers” without a threshold or governed definition. | HIGH_VALUE_NOW | CLARIFY | Detect missing threshold only conservatively | Needed to propose supported slots |
| `AMBIGUOUS_POPULATION` | “Refund rate” when order-level and amount-level rates both exist. | HIGH_VALUE_NOW | CLARIFY unless governed default | Deterministic after contract lookup | Useful only when contract leaves multiple candidates |
| `AMBIGUOUS_TOP_N_BASIS` | “Top products” without revenue, quantity, or order-count basis. | NATURAL_BUT_LOW_FREQUENCY | CLARIFY | Detect missing ranking basis | Candidate enumeration useful |
| `AMBIGUOUS_VALUE` | “Florida” when values may be `FL`, full names, or multiple entities. | LATER_MILESTONE | CLARIFY later | Requires value grounding/entity resolution | Separate model/tooling concern |
| `AMBIGUOUS_TIME_SCOPE` | “Recent orders” or “last period.” | LATER_MILESTONE | CLARIFY later | Requires temporal contract | Separate temporal work |
| `NONEXISTENT_SELECT_CONCEPT` | “Show customer marital status.” | HIGH_VALUE_NOW | ABSTAIN | Often deterministic from schema/glossary absence | Not needed if absence is conclusive |
| `NONEXISTENT_FILTER_CONCEPT` | “Orders where customer occupation is executive.” | HIGH_VALUE_NOW | ABSTAIN | Deterministic when no supported concept/path exists | Not needed for known absence |
| `NONEXISTENT_FILTER_VALUE` | “Orders from region Atlantis” where the concept exists but value support is absent. | LATER_MILESTONE | ABSTAIN or value-check later | Requires bounded value lookup | Separate value grounding |
| `UNSUPPORTED_RELATIONSHIP` | “Refunds by product category” when no supported refund-to-product path exists. | HIGH_VALUE_NOW | ABSTAIN | Deterministic from relationship graph | Model may propose path, software must reject unsupported ones |
| `MISSING_AGGREGATION_SOURCE` | “Average customer satisfaction” with no satisfaction measure. | HIGH_VALUE_NOW | ABSTAIN | Deterministic metadata check | Usually unnecessary |
| `MODEL_FAILURE` | “Total orders by region” but generated SQL omits grouping. | NOT an ambiguity family | Existing generation/verifier diagnostics | Not a clarification | No |

PRACTIQ is relevant as external research because it explicitly studies
ambiguous, unanswerable, and answerable conversational Text-to-SQL queries;
its categories are useful inputs, not a DecisionSQL specification. See the
[NAACL paper](https://aclanthology.org/2025.naacl-long.13/). DecisionSQL
should retain its own contract-first definitions rather than mechanically
copying public labels.

## 6. Small illustrative set

These are research examples only, not a benchmark:

### Answerable

- “Show completed revenue by region.” The active metric and valid `region`
  dimension are explicit.
- “List customers with their regions.” The entity and supported relationship
  are unambiguous.
- “Show the top five products by total quantity sold.” The ranking basis,
  direction, and limit are explicit; M4 can help with structure.
- “Show refund rate by region.” If the wording maps to the single active
  governed `refunded_order_rate` definition, the contract is the default.
- “Count orders per sales representative.” The direct schema relationship is
  supported and the grouping cue is explicit.

### Ambiguous

- “Show the best products.” Ranking basis is unresolved.
- “Show customer performance.” The measure/population is unresolved.
- “Show large orders.” Threshold and measure are unresolved.
- “Show sales by region.” “Sales” may mean order value, item quantity, or
  payment amount in a context where no default is stated.
- “Show top customers.” Revenue, order count, and quantity are materially
  different ranking bases.

### Unanswerable

- “Show customer marital status.” No supported concept exists in the demo
  schema or M3 contract.
- “Filter orders by customer occupation.” No supported occupation field/path
  exists.
- “Show product supplier ratings.” No supplier/rating concept exists.
- “Calculate employee churn.” No employee/churn population is modeled.
- “Show refunds by product supplier.” The required relationship is absent from
  the supported graph.

“Recent orders,” fuzzy values such as `Florida`, and ambiguous date ranges are
deliberately boundary examples, not M5R core cases. They should not be used to
inflate the case for this milestone.

## 7. M3 and M4 coverage

M3 already removes important apparent ambiguity. A phrase that resolves to an
active governed metric gets the server-owned definition, including measures,
filters, relationship path, scale, null policy, and valid dimensions. For
example, `refunded_order_rate` is distinct from
`refund_to_revenue_rate`; the system should not ask the user merely because
both are theoretically conceivable if the request names one governed metric
or the glossary gives one canonical default.

The contract does not resolve every colloquial phrase. “Best products,”
“large orders,” and “sales” can remain ambiguous because the catalog has no
universal ranking or threshold default for them.

M4 covers complexity, not ambiguity. Multi-join summaries, grouped top-N,
HAVING, subqueries, CASE expressions, and non-governed arithmetic should
continue when the request is semantically clear. A complex query is not
unanswerable, and a retrieved example is not evidence that the question has
only one interpretation.

## 8. Value and temporal boundaries

`AMBIGUOUS_VALUE`, `NONEXISTENT_FILTER_VALUE`, entity resolution, aliases such
as `FL` versus `Florida`, and fuzzy literal matching belong primarily to a
future **Value Grounding** milestone. M5R may include a few boundary examples
but should not make them acceptance-critical.

`AMBIGUOUS_TIME_SCOPE` belongs to future temporal work. Words such as
“recent,” “last period,” and “this quarter” require a time policy, calendar,
timezone, and often a temporal contract. M2.14.1 did not justify broad
TemporalSpec work, and M5R does not reopen it.

## 9. Action space and clarification contract

The smallest useful V1 action space is exactly:

- `ANSWER`
- `CLARIFY`
- `ABSTAIN`

`ANSWER_WITH_GOVERNED_DEFAULT` should be an internal reason/subtype of
`ANSWER`, not a fourth public action. `OUT_OF_SCOPE` should be represented as
an abstention reason. `SAFE_MULTI_ANSWER` should be deferred: returning both
business metrics can create false certainty and double-counting. It may be
researched later for non-financial exploratory use, but it is not needed for
the first decision boundary.

If clarification is built, V1 should allow at most one round:

```text
question -> AMBIGUOUS -> one bounded clarification -> resolved or ABSTAIN
```

The clarification must identify one unresolved slot, offer only supported
options, avoid raw SQL, and never silently select an option. Failure to resolve
in one round should abstain rather than loop.

## 10. R0, R1, and R2

### R0 — deterministic only

Inputs are question text, schema/catalog names, relationship graph, active
glossary, and conservative lexical cues. It can reliably detect explicit
missing concepts, unsupported relationships, known governed defaults, and
some missing slots such as an absent top-N basis.

Its strengths are zero provider cost, auditability, deterministic behavior,
and direct compatibility with M3/M4. Its weakness is language coverage: it
cannot reliably enumerate colloquial alternatives or distinguish two
business meanings from sparse wording without recreating the heuristic
complexity that made M5.0 noisy.

R0 should be a baseline and fast path, not the assumed complete solution.

### R1 — direct classifier

One model call returns a typed action and reason family without generating SQL.
It is simple to benchmark and can recognize ordinary ambiguity language better
than fixed cues. However, it introduces a black-box decision before every
answer, can hallucinate ambiguity or unanswerability, and self-reported
confidence is not execution authority. R1 needs strict typed output,
no-retry accounting, and high-cost penalties for unsafe `ANSWER`.

R1 is compatible with the trust philosophy only if its output remains an
untrusted proposal validated by deterministic policy. The model must not
declare a column, relationship, or metric supported by itself.

### R2 — interpretation-first hybrid

The model proposes a small bounded set of candidate interpretations. Each
candidate contains only minimal typed semantic slots—intent type, optional
metric/measure/entity ID, dimensions, filter slots, and ranking basis. Software
validates each against the M3 contract, schema, and relationship graph.

The decision then follows validated cardinality: zero means abstain, one means
answer, and multiple materially different valid interpretations mean clarify.
This preserves “the LLM proposes; deterministic software decides” and gives a
factual audit trail for why an intervention occurred.

R2 risks incomplete enumeration, invented candidates, larger prompts, and
more validator surface area. It is not automatically superior; those risks
must be measured. Its advantage is that model fluency and server authority
are separated instead of making a model classification label authoritative.

### Comparison

| Dimension | R0 | R1 | R2 |
| --- | --- | --- | --- |
| Provider cost | none | one call/question | one call/question |
| Determinism | high | low without strict validation | medium after validation |
| Auditability | high | action/reason only | candidate interpretations + validation reasons |
| M3 compatibility | direct | good with untrusted output | strongest conceptual fit |
| M4 compatibility | direct fast path | before M4 | before M4 |
| Ambiguity coverage | narrow | broad but noisy risk | broad with bounded validation |
| Unanswerability quality | strong for explicit absence | hallucination risk | deterministic support check |
| Main failure | missed ambiguity | over-intervention / unsafe answer | missed or invented interpretation |
| Recommendation | baseline/fast path | comparison arm | primary experiment arm |

## 11. Runtime placement recommendation

The future boundary should not be placed indiscriminately before all routing.
The recommended conceptual placement is:

```text
question
  -> deterministic governed/default lookup
  -> if explicit governed answer: M3
  -> otherwise answerability/interpretation boundary
  -> if ANSWER: residual direct path with optional M4
  -> if CLARIFY: one bounded clarification
  -> if ABSTAIN: no SQL generation
  -> generated SQL -> M1
```

This preserves M3’s stronger semantics and avoids spending answerability
provider calls on explicit stable metric requests. The boundary should still
be able to classify a governed-looking request as unanswerable when the
requested concept or dimension is not supported; it must not invent a metric
to force the governed branch.

## 12. Selective-answering metrics

For a future three-arm experiment, define metrics as follows:

- **Action accuracy:** exact match between predicted `ANSWER`, `CLARIFY`, or
  `ABSTAIN` and the adjudicated action.
- **ANSWER precision/recall:** among predicted answers, the fraction that are
  answerable; among answerable cases, the fraction answered.
- **CLARIFY precision/recall:** exact analogous definitions for genuinely
  ambiguous cases.
- **ABSTAIN precision/recall:** exact analogous definitions for genuinely
  unanswerable cases.
- **Unsafe answer rate:** gold `CLARIFY` or `ABSTAIN` predicted as `ANSWER`.
  This is the primary safety failure.
- **Unnecessary intervention rate:** gold `ANSWER` predicted as `CLARIFY` or
  `ABSTAIN`. This reduces coverage but is normally less dangerous than an
  unsafe answer.
- **Coverage:** fraction of all questions allowed to proceed to an answer
  path, initially predicted `ANSWER`.
- **Selective risk:** semantic error rate among questions predicted
  `ANSWER`; later end-to-end evaluation must use result equivalence, not SQL
  string equality or execution success.
- **Clarify/abstain confusion:** report gold `CLARIFY` → `ABSTAIN` and gold
  `ABSTAIN` → `CLARIFY` separately.

The qualitative cost ordering is:

1. worst: `UNANSWERABLE → ANSWER`
2. next: `AMBIGUOUS → ANSWER` with a silent interpretation
3. then: `ANSWERABLE → ABSTAIN`
4. then: `ANSWERABLE → CLARIFY`

Numeric costs should not be fixed before seeing class prevalence and product
impact. A future model’s self-reported probability must not substitute for
calibration; validated interpretation count and server support are more
trustworthy evidence.

## 13. Future benchmark specification

Do not build it in M5R. The next experiment should freeze approximately
120–160 new internal questions, ideally:

- about 40 `ANSWERABLE`
- about 40 `AMBIGUOUS`
- about 40 `UNANSWERABLE`
- optional 20 governed-default cases as an `ANSWERABLE` subtype

The core case should contain:

```text
case_id
question
gold_action
reason_family
supported_interpretations
canonical_interpretation (optional)
missing_concept (optional)
required_clarification_slot (optional)
relevant_schema_objects
relevant_semantic_ids
adjudication_note
```

Primary classification cases should not require SQL. Later end-to-end cases
can add a synthetic clarification answer and measure resolution plus SQL
result equivalence.

Required families should include explicit governed/default cases, clear M4
complexity cases, ambiguous metric/ranking/filter cases, missing concepts,
unsupported relationships, and hard negatives where multiple physical paths
exist but only one semantic path is supported. Use minimal contrasts and
independent adjudication; do not reuse M4 or prior holdouts as memory.

The gold standard for ambiguity is at least two plausible, supported,
materially different interpretations. The gold standard for unanswerability
is absence of a derivable supported concept/path—not difficulty. Every case
must be checked for “complex but answerable” versus “ambiguous.”

## 14. Public calibration

Recommendation: use a new internal DecisionSQL corpus as the primary gate and
use PRACTIQ as a separate calibration report, not a pooled score. PRACTIQ is
valuable because it was designed around ambiguous, unanswerable, and
answerable Text-to-SQL requests, but its schemas, annotation assumptions, and
conversation distribution are not the DecisionSQL semantic contract. A
separate score can test whether the chosen taxonomy generalizes without
pretending that public and internal labels are identical.

Do not download or run external data in M5R. If later calibration is approved,
freeze the adapter and report internal and public results independently, as
with Defog and BIRD.

## 15. Falsification criteria

The M5 direction should be abandoned or paused if any of these occur:

- independently authored ambiguity is rare or contrived in normal
  DecisionSQL usage;
- R0 covers nearly all valuable cases with lower cost and equal safety;
- R1/R2 unsafe answer rate cannot be driven low without unacceptable coverage
  loss;
- unnecessary intervention dominates answerable cases;
- interpretation-first enumeration frequently omits a legitimate supported
  interpretation or invents unsupported ones;
- clarification cannot resolve a meaningful share in one turn;
- unanswerability is not distinguishable from unsupported value/time grounding
  without building larger deferred systems;
- provider latency/cost outweighs the reduction in unsafe answers;
- the action boundary becomes another opaque generic LLM planner.

These criteria make `NO_GO_M5` a valid outcome.

## 16. M5.0.2 and narrow verifier status

M5.0.2 support expansion should remain paused during M5R. The three narrow
diagnostics remain research-supported but are not part of the answerability
decision and are not production gates. M5R must not enlarge their corpus or
change their rules.

The apparent `UNREQUESTED_LIMIT` discrepancy in M5.0.1 is a terminology
difference, not a verifier calculation defect. The per-rule report counts
every occurrence of the signal, regardless of severity; it reported DEV
support 8 with 100% precision. The family mutation summary counts only
`hard_fired`, i.e. signals whose report severity is `HARD`; the v1 rule table
declares `UNREQUESTED_LIMIT` as `SOFT`, so it reported 0/8 family HARD fires.
The same distinction explains 0/2 in HOLDOUT versus 2 true positives in the
per-rule report. No verifier code was changed.

## 17. Decision

M5R classification: `SELECTIVE_ANSWERING_RESEARCH_SUPPORTS_EXPERIMENT`.

Recommended primary approach: **R2**, with R0 and R1 as frozen comparison
arms. Recommended placement: after deterministic governed-default lookup and
before residual direct generation/M4. Proposed states: `ANSWER`, `CLARIFY`,
`ABSTAIN`, with governed-default as an internal answer reason.

This memo does not implement any of those states. There is no classifier, no
clarification UX, no abstention runtime, no SQL repair, and no change to M1,
M3, M4, M5.0, or M5.0.1.

Exactly one next milestone is recommended:

**M5R.1 — Frozen Selective Answering Benchmark & Three-Arm Evaluation**

It should remain provider-controlled and should begin only after the benchmark
is independently authored, frozen, and reviewed against the falsification
criteria above.
