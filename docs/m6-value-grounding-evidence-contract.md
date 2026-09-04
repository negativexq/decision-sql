# M6 — Value Grounding Evidence & Contract Design

Status: research / evidence / contract design. No production value resolver,
fuzzy matcher, embedding index, provider call, or clarification UX was added.

## 1. Executive conclusion

The demo domain contains a real but narrow value-grounding surface:
low-cardinality statuses, product categories, currency, region names, and
entity-name fields. Exact and case/whitespace-normalized matching are natural
and can be deterministic. However, the current fixture has no governed value
alias registry, no abbreviation vocabulary, no duplicate entity names, no
observed fuzzy-typo cases, and no accepted historical failure attributable to
value grounding.

M6 classification: **VALUE_GROUNDING_WEAK_EVIDENCE**.

This does not justify a general production resolver or an LLM value resolver.
It does justify a small provider-free experiment to measure the currently
unquantified boundary. The smallest defensible future contract is:

~~~
known target field + user value phrase
        ↓
server-owned candidate inventory / aliases
        ↓
0 candidates → NO_MATCH
1 canonical candidate → RESOLVED
2+ canonical candidates → AMBIGUOUS
~~~

No nearest-candidate rule is permitted. Candidate generation and field
grounding remain separate.

Exactly one next milestone is recommended: **M6.1 — Frozen Value Grounding
Benchmark & Resolver Baselines**. This is an isolated evaluation
recommendation, not runtime integration.

## 2. Definition and boundaries

Value grounding is the bounded mapping of a user-supplied value phrase to a
canonical value for an already-known target field. It answers whether a phrase
such as “successful” can map to a server-supported value of payments.status.
It does not decide which field the phrase targets, what a business metric
means, or how SQL is composed.

| Boundary | Question | M6 status |
| --- | --- | --- |
| Field grounding | Which field does “active” or “North” constrain? | Separate precondition |
| Schema grounding | Which table/entity/relationship is relevant? | Existing catalog/retrieval |
| Value grounding | Which canonical value belongs to a known field? | M6 subject |
| Governed metric | What does “completed revenue” mean? | M3 authoritative |
| Temporal grounding | What does “recent” or “this quarter” mean? | Deferred |
| SQL generation | How is supported intent expressed? | Existing direct/M4 path |

“High-value customer” may require a metric, threshold, or population
definition. “Best products” is ranking/metric ambiguity. “Florida” versus “FL”
is value grounding only after the target field is known. Temporal expressions,
time zones, fiscal periods, and numeric thresholds are out of scope.

## 3. Current DecisionSQL context

The M3 catalog contains 8 entities, 9 relationships, 8 dimensions, 13
measures, and 10 metrics. The frozen semantic contract hash is:

0463a10ecd3dbb414d11559f8559c604748f2b131a078d66ec79c4993d70eb3c

The catalog has stable semantic IDs and human-facing table aliases, but no
value-alias registry. Existing aliases are schema/table vocabulary, not
canonical mappings such as successful → settled.

M3 resolves business meaning before SQL. For example, completed_revenue has a
server-owned measure, eligibility filter, and allowed dimensions. M6 must only
resolve a literal after that semantic target is known; it must not redefine the
metric or silently change its population.

## 4. Read-only value inventory

The inventory was obtained from the running commerce-v1 PostgreSQL fixture
using bounded metadata queries and samples. No rows were modified. The
machine-readable bounded inventory is
[m6_value_inventory.json](/Users/ofk/DecisionSQL/evaluation/fixtures/m6_value_inventory.json).

Twelve textual/value-bearing columns were inspected:

| Category | Count | Fields |
| --- | ---: | --- |
| ENUM_LIKE | 4 | products.category, orders.status, orders.currency, payments.status |
| ENTITY_NAME | 4 | regions.name, sales_representatives.name, customers.name, products.name |
| FREE_TEXT | 1 | refunds.reason |
| IDENTIFIER | 2 | products.sku, orders.order_number |
| IDENTIFIER_INTERNAL | 1 | customers.external_key |

All inspected columns are non-null in the current fixture. Distinct counts:
regions 4, representatives 8, customers 40, products 24, categories 4,
order statuses 3, order currencies 1, payment statuses 1, refund reasons 1,
SKUs 24, order numbers 120, and internal customer keys 40.

Low-cardinality examples are orders.status = {cancelled, completed, pending},
products.category = {Electronics, Home, Office, Outdoor}, regions.name =
{East, North, South, West}, payments.status = {settled}, and orders.currency =
{USD}. Refund reason is treated as free text despite its current cardinality.

Entity-like examples are customer names (40), product names (24),
representative names (8), and region names (4). Customer external keys are
internal and non-queryable. SKUs and order numbers are identifiers, not general
semantic values.

Safe/provider-exposable values should be limited to approved low-cardinality
status/category/currency and region vocabularies. Customer names,
representative names, product names, SKUs, order numbers, refund reasons, and
internal keys must not be dumped into prompts or telemetry. They require
field-specific privacy policy and bounded internal candidate lookup.

## 5. Naturalness and historical evidence

| Family | Current domain evidence | Assessment |
| --- | --- | --- |
| EXACT_VALUE | completed, pending, Electronics, North, settled | Natural, high value now |
| NORMALIZED_EXACT_VALUE | electronics versus stored Electronics | Natural, high value now |
| ENUM_ALIAS | No value alias registry | Natural in products, absent here |
| ABBREVIATION | No region/state abbreviation data; only USD observed | Synthetic-only here |
| SYNONYM | Table aliases exist; value synonyms do not | Defer |
| ENTITY_NAME | Customer, product, representative, region names | Natural, separate policy |
| MULTI_MATCH | Current names are unique | Synthetic/counterexample needed |
| NO_MATCH | Users can request absent values | Natural action, no historical evidence |
| FUZZY_TYPO | No observed typo evidence | Synthetic-only here |
| DERIVED_OR_UNSUPPORTED_VALUE | high-performing has no literal meaning | Usually metric ambiguity |

Historical review found no confirmed or plausible value-grounding failure.
The M2.14.1 design artifact reports general_literal_value_count = 0. Its
failure classes are aggregation, filters, joins, table/column grounding,
output shape, and arithmetic; these were not relabeled as value errors.
Exact filters such as category = Electronics and region = North were known
values, while the reviewed problems were output/table/field issues.

Confirmed value-grounding failures: 0. Plausible value-grounding failures: 0.
Reviewed not-value-grounding cases include missing/extra filters, wrong fields,
wrong tables, and wrong joins. Remaining cases are undetermined rather than
inflated into M6 evidence.

The domain therefore supports a narrow benchmark, but not a claim that value
grounding is a measured production bottleneck.

## 6. Proposed resolution contract

The contract should receive a typed target field identity plus a user value
phrase. It should not solve arbitrary schema linking.

~~~text
ValueResolution
- status: RESOLVED | AMBIGUOUS | NO_MATCH | NOT_APPLICABLE
- target_field_id
- canonical_value_id (optional)
- canonical_value_display (optional)
- candidate_ids (bounded)
- match_method
- inventory_version/hash
- alias_id (optional)
~~~

RESOLVED means exactly one supported canonical candidate was selected by an
eligible method. AMBIGUOUS means two or more supported candidates remain and
none may be chosen silently. NO_MATCH means no supported candidate exists and
the nearest value must not be substituted. NOT_APPLICABLE means the phrase is
not a value slot for the known intent or the target is outside M6.

Canonical identity and display label are separate. An enum identity might be
order_status:COMPLETED with display label “Completed”. An entity may use its
internal primary key as identity while exposing a controlled display name.
Do not invent a global value ontology before the benchmark requires it.

Automatic resolution should initially be limited to exact, safe-normalized
exact, and explicitly governed aliases. Fuzzy or model-proposed matches should
remain candidate-generation or clarification inputs until measured.

## 7. Alias governance and freshness

A minimal future alias record is:

~~~text
ValueAlias
- field_id
- alias
- canonical_value_id
- status
- source/provenance
- version
~~~

Aliases should be manually governed or derived from an approved server-owned
vocabulary. Model-proposed aliases may be review suggestions, never runtime
authority. Retired canonical values and ambiguous field targets must invalidate
an alias.

Static aliases and dynamic DB inventories need separate version/freshness
policies. A future dynamic inventory would require field identity, source,
version, hash, and refresh semantics. No cache or refresh mechanism is
implemented here.

## 8. Cardinality and privacy

Low-cardinality fields and high-cardinality entities need different handling.

For a small enum-like field, a bounded complete inventory may be reasonable if
completeness and provider exposure are explicitly governed. Snowflake’s
Cortex Analyst documentation describes sample-value search for relatively
low-cardinality dimensions and a separate search service for larger text
inventories; this is an analogy, not a DecisionSQL specification:
[Snowflake literal search](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/cortex-analyst-search-integration).

For high-cardinality entity fields, use internal candidate retrieval with a
bounded result set. Do not send all names to a provider. Customer names and
free text should default to internal-only.

OTel should record field ID, match method, candidate count, status, and
inventory version/hash—not raw names, emails, free text, or unbounded
candidate arrays.

## 9. Candidate-generation approaches

| Approach | Precision | Coverage | Cost/latency | Recommendation |
| --- | --- | --- | --- | --- |
| G0 exact/normalized | Very high when normalization is safe | Low–medium | Minimal, provider-free | Required baseline |
| G1 governed alias | High if curated | Registry-dependent | Minimal runtime, maintenance cost | Evaluate only with explicit aliases |
| G2 bounded lexical retrieval | Variable; candidate-only is safer | Medium/high for names | Local work; inventory-dependent | Evaluate for entities, never rank-only resolve |
| G3 model-assisted proposal + deterministic validation | Unknown; may omit/invent | Potentially broad | Provider latency/tokens | Defer until deterministic evidence fails |

The likely future design is exact → safe normalization → governed alias →
bounded candidate retrieval → 0/1/many decision. Fallback tiers must not
widen authority silently.

Embeddings are not a V1 requirement: no measured semantic-synonym gap exists
in this domain to justify extra infrastructure, privacy exposure, latency, or
false-neighbor risk. Typo correction and semantic synonym matching are
different problems. Electrnics → Electronics may merit a bounded typo study;
phones → Mobile Devices requires governed business vocabulary.

Current enterprise patterns are consistent with this separation: Snowflake
documents semantic-model synonyms, sample values, verified queries, and
human-reviewed suggestions. Databricks documents column synonyms, example
values, value dictionaries, and clarification when an answer cannot be
supported. These are useful primitives, not specifications to copy:
[Snowflake verified queries](https://docs.snowflake.com/user-guide/snowflake-cortex/cortex-analyst/verified-query-repository),
[Databricks Genie setup](https://docs.databricks.com/aws/genie/set-up), and
[Databricks Genie concepts](https://docs.databricks.com/aws/en/genie-agents/concepts).

Academic Text-to-SQL work treats value grounding and column-value mapping as
distinct structure-grounding tasks, supporting a separate evaluation boundary:
[Structure-Grounded Pretraining for Text-to-SQL](https://arxiv.org/abs/2010.12773).

## 10. M3, M4, and M5 interaction

M3 remains authoritative for metric definitions. In “completed revenue in
North”, M3 identifies completed_revenue while M6 may resolve North for
dimension:region. Neither layer may redefine the other.

M4 examples containing literals are context only. A retrieved example’s value
must never become authority for the current question; future generation should
use a current resolved value, if any.

M5’s lesson is not to hide uncertainty. MULTI_MATCH and NO_MATCH are bounded
states, not nearest-value answers. The rejected semantic verifier and closed
selective-answering branches do not justify folding value grounding into M5.

## 11. Future benchmark specification

Do not build the large benchmark in M6. If M6.1 is approved, freeze an
evaluation-only corpus of approximately 120–180 cases:

| Class | Suggested count | Content |
| --- | ---: | --- |
| RESOLVABLE | 50–60 | exact, normalized, governed aliases, unique entities |
| AMBIGUOUS | 30–40 | concrete two-or-more supported candidates |
| NO_MATCH | 30–40 | absent supported values |
| FIELD_AMBIGUOUS | separate diagnostic | target field unclear, not pure value grounding |

Each case should contain case_id, target_field_id, user_value_phrase,
gold_status, canonical value if resolved, valid candidates, family, source
type, split, and concise adjudication note. No SQL is required.

Use actual values first, then server-owned aliases, then manually adjudicated
natural variants. Use minimal contrasts: exact unique name, normalized
spelling, governed alias, ambiguous prefix, and absent value. Add a
counterexample fixture only if current data cannot provide a genuine
multi-match case, and label it as fixture evidence.

Candidate Recall@K must be separate from decision quality. Public calibration,
if later approved, should be reported separately from internal DecisionSQL
scores. BIRD highlights dirty values and value comprehension, but its evidence
format is not a production canonical-value contract:
[BIRD paper](https://arxiv.org/abs/2305.03111).

## 12. Future metrics

- **Unsafe auto-resolution rate**: true state is AMBIGUOUS or NO_MATCH, but
  output is RESOLVED.
- **Resolution precision**: among RESOLVED outputs, fraction mapped to the
  correct canonical value.
- **Resolution coverage**: RESOLVED divided by all value cases.
- **Ambiguity precision**: among AMBIGUOUS outputs, fraction with multiple
  supported candidates.
- **No-match precision**: among NO_MATCH outputs, fraction truly lacking a
  supported candidate.
- **Candidate Recall@K**: correct canonical candidate appears in generated
  candidates.

Report by family, field category, cardinality, and match method. An eventual
end-to-end test may evaluate value grounding → SQL → M1 → result equivalence,
but the first experiment must isolate grounding quality.

## 13. Falsification conditions

Stop or defer runtime work if:

- accepted errors contain almost no value-grounding contribution;
- exact/normalized matching covers nearly all natural cases;
- aliases, abbreviations, and multi-match cases remain synthetic;
- value grounding repeatedly collapses into field/schema grounding;
- useful cases require a separate search product;
- privacy policy prevents safe candidate exposure;
- G0/G1 cannot achieve high precision without speculative matching;
- G2 candidate recall is poor or candidate sets are too large;
- G3 adds cost without reducing unsafe auto-resolution;
- NO_MATCH and MULTI_MATCH cannot be distinguished reliably;
- no natural, independently adjudicated benchmark can be authored.

The key falsifier is not low coverage. A narrow exact/alias resolver could be
useful. The key falsifier is that no natural measurable safety problem remains
after M3 and ordinary field grounding.

## 14. Decision

Value grounding is plausible but currently weakly evidenced in DecisionSQL.
The fixture provides enough exact/normalized and entity-name material for a
small benchmark, but not enough evidence for a production resolver, fuzzy
matching, embeddings, or model assistance.

Final classification: **VALUE_GROUNDING_WEAK_EVIDENCE**.

No production code was added. No provider, embedding, reranker, SQL-generation,
benchmark, external rerun, or DB mutation was performed. M1, M3, M4, and M5
remain unchanged.

### Sources consulted

- [Snowflake Cortex Analyst literal search](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/cortex-analyst-search-integration)
- [Snowflake Cortex Analyst verified queries](https://docs.snowflake.com/user-guide/snowflake-cortex/cortex-analyst/verified-query-repository)
- [Databricks Genie setup](https://docs.databricks.com/aws/genie/set-up)
- [Databricks Genie concepts](https://docs.databricks.com/aws/en/genie-agents/concepts)
- [Structure-Grounded Pretraining for Text-to-SQL](https://arxiv.org/abs/2010.12773)
- [BIRD benchmark](https://arxiv.org/abs/2305.03111)
