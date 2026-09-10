# M52.2 — Population Semantic Metadata Contract Feasibility

M52.2 is a zero-call, audit-only feasibility study. Gold and references are forensic inputs, never proposed runtime inputs.

## Historical preservation
M52 and M52.1 verdicts remain unchanged. No benchmark, truth, response, prompt, or runtime artifact was modified.
## M52.2 scope
The question is whether a real semantic catalog needs generic population metadata independently of this benchmark.
## Zero-call accounting
Provider/model/LLM/embedding/reranker calls: 0; retries, repairs, judges, selectors: 0.
## Frozen evidence integrity
Response corpus verified: `9783930bb0dac022a926c6638822e56747630f3605910cbf8487d409c5105d9a`. Expansion and full-truth manifests were verified.
## M52.1 parent finding
M52.1 found 4 direct carrier-loss cases, 5 remaining group-labeled cases, and no current structured preservation semantics.
## Research question
Can a minimal production semantic contract express carrier, inclusion, result grain, and measure lineage without gold or model self-report?
## Evidence inspection methodology
All nine targets and five controls were re-inspected through question, visible context, raw response, SQL, gold, RefA/RefB, BASE, and counterfactual trace evidence.
## Four direct carrier-loss targets
insurance_02, marketplace_06, marketplace_08, procurement_06. Gold-to-V0 mappings are lossless for population scope; the V0+AST shadow signal flags 4/4.
## Remaining five group-survival targets
healthcare_07, healthcare_09, insurance_14, procurement_15, workforce_10. Their population component is representable, but their observed failures are not carrier-population contradictions and should not expand V0.
## Primary negative controls
healthcare_06, marketplace_05, procurement_11, telecom_08, workforce_12. All 5 are naturally representable; matching-only and preserving cases are both present.
## Candidate semantic concepts
Retained: result grain, carrier entity, population inclusion, measure source entity, and contributing population reference.
## Result-grain analysis
Result grain is production-legitimate and needed to distinguish output grouping from measure source; it may be static for named metrics or request-level for ad hoc requests.
## Carrier-entity analysis
Carrier entity is the possible output-group population before measure qualification. It is distinct from the measure source and from relationship authorization.
## Population-inclusion analysis
ALL_BASE_ENTITIES and MATCHING_ENTITIES_ONLY are sufficient for the motivating cases and are generic semantic concepts, not SQL syntax.
## Zero-group-policy analysis
For V0 this is derivable from inclusion; zero-versus-NULL representation remains a ResultContract/metric concern. It is not retained as a separate field.
## Measure-source analysis
Measure source entity is legitimate metric lineage and supports AST dependency analysis; it belongs to a metric/lineage catalog when known.
## Measure-contributing-population analysis
A canonical semantic-definition reference is legitimate for named measures. Arbitrary per-case SQL filters are not.
## Separation from ResultContract
Projection, ordering, null representation, and tolerances remain ResultContract concerns.
## Separation from temporal semantics
Time windows, latest-row, and effective intervals remain separate semantic dimensions.
## Separation from authority semantics
Entity references do not imply authorized relationships; the authority catalog remains independent.
## Static catalog vs request-level semantics
Static catalog alone is insufficient. Request-level population intent is required for ad hoc all-versus-matching distinctions.
## Metadata ownership
Metric/lineage catalog owns source and canonical populations; semantic query plan or user/application structured intent owns request-level carrier/inclusion.
## Independent authorability
The concepts are production-legitimate, but current repository context cannot independently populate the request-level values as structured data.
## Candidate V0 schema
`PopulationSemanticContractV0` has 5 semantic leaves: result_grain.entity_id, carrier.entity_id, carrier.inclusion, measure.source_entity_id, and optional measure.contributing_population_ref. Schema hash: `42aa05bc588c4e8f0cfaf3067c45ed7612242d6e58671fad2dcadcf6e9fbd974`.
## Minimality analysis
Removed zero_group_policy as derivable, measure_grain as existing catalog/native-grain information, null policy as ResultContract/metric semantics, and qualification_semantics as redundant.
## Gold-to-contract mapping
Gold mappings are audit-only and lossless for the four direct population failures.
## Model-visible-to-contract mapping
The visible-only pass found no structured request-level carrier/inclusion value; mappings are partial rather than gold-compatible guesses.
## Gold-visible parity
60 relevant expansion cases: 0 exact/semantic parity, 60 partial parity because current context lacks request-level fields.
## Gold-independence substudy
17 cases were mapped from visible structured context before gold comparison; gold was consulted only afterward.
## Correct-case expressiveness
All five primary controls are representable without case-specific fields.
## Matching-only expressiveness
Matching-only inclusion is explicitly representable and prevents a simplistic INNER JOIN false positive.
## Preservation-case expressiveness
ALL_BASE_ENTITIES is explicitly representable and supports the four direct carrier-loss diagnoses.
## Ratio/non-aggregate boundaries
Ratios require separate numerator/denominator semantics; non-aggregate queries may leave the optional metric contract unapplied.
## Independent semantic signal analysis
Catalog truth plus structured request intent is an independent signal; same-model self-report is only a self-declared signal.
## M50C self-report risk
Adding model-generated population claims would repeat the M50C perturbation/independence risk and is not recommended as the sole source.
## Future validator feasibility
With V0 supplied, the existing diagnostic AST signal flags 4/4 direct targets and 0/5 primary controls falsely.
## Future false-positive surface
The broader relevant expansion screen evaluated 60 cases and flagged 0 correct cases.
## Metadata authoring cost
Moderate: static lineage/population definitions need domain expertise; ad hoc request intent needs structured application/user input.
## Maintenance risks
Missing, stale, ambiguous, or conflicting metadata must fail open; authority and temporal definitions remain separately maintained.
## Negative capabilities
SQL shape alone cannot identify intended population, and current structured context cannot supply the request-level distinction.
## Final contract feasibility verdict
`PRODUCTION_POPULATION_SEMANTIC_CONTRACT_PARTIALLY_FEASIBLE`.
## Recommended next milestone
M52.3 — Query Population Intent Source Feasibility (zero-call). Establish an independent application/user/semantic-plan source before any shadow validator.
## Tests
Typed contract tests and parent M52/M52.1 tests pass; changed-file static checks pass. Full-suite historical failures remain separate.
## Determinism
Two canonical replays matched. Analysis hash: `8833d7d3fb2206475042b6c15198a51eef46a0c9a4444d90d1a123b8adc2f3dc`.
## Repository state
No mainline/runtime integration, model-context change, provider-schema change, benchmark edit, or decision/SQL override was made.
