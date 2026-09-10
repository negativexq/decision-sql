# M52.1 — Group-Survival / Population Invariant Validator Feasibility

Zero model/provider calls. M52.1 is post-hoc forensic feasibility; detector metrics are not independent generalization estimates.

Targets: healthcare_07, healthcare_09, insurance_02, insurance_14, marketplace_06, marketplace_08, procurement_06, procurement_15, workforce_10
Primary controls: healthcare_06, marketplace_05, procurement_11, telecom_08, workforce_12

## Target case-by-case forensics

### CASE: healthcare_07
QUESTION: For each patient, return patient ID and total posted payment amount, including patients with none.
MODEL-VISIBLE CONTRACT: preservation expressed in question; no structured population field; schema/metric definitions and requested output grain
MODEL RESPONSE: ANSWER — `SELECT p.patient_id, COALESCE(SUM(pay.amount), 0) AS total_posted_payment_amount
FROM patients AS p
LEFT JOIN payments AS pay ON pay.patient_id = p.patient_id
GROUP BY p.patient_id`
GOLD SEMANTICS: base-entity-preserving; group survival=True
REFERENCE BEHAVIOR: RefA and RefB are independently validated witnesses for the same carrier/measure contract.
MODEL BEHAVIOR: PRESERVES_BASE_ENTITIES; all-payment measure instead of posted-payment measure
BASE: {'columns': ['patient_id', 'total_posted_payment_amount'], 'row_count': 4, 'rows': [{'patient_id': 1, 'total_posted_payment_amount': '75.00'}, {'patient_id': 3, 'total_posted_payment_amount': '0'}, {'patient_id': 4, 'total_posted_payment_amount': '0'}, {'patient_id': 2, 'total_posted_payment_amount': '250.00'}], 'session_timezone': 'UTC', 'status': 'ALLOWED', 'truncated': False}
COUNTERFACTUAL: first discriminator `healthcare_07_cf1_665417`
ROOT CAUSE: population preserved; frozen row-order/result-contract mismatch or measure mismatch
RUNTIME-DETECTABLE SIGNAL: base population metadata plus AST carrier/join/predicate analysis
GOLD DEPENDENCY: truth semantic target, references, fixture outcomes; not permitted at runtime.

### CASE: healthcare_09
QUESTION: For each patient, return patient ID and count of completed encounters, including patients with none.
MODEL-VISIBLE CONTRACT: preservation expressed in question; no structured population field; schema/metric definitions and requested output grain
MODEL RESPONSE: ANSWER — `SELECT p.patient_id, COUNT(e.encounter_id) FILTER (WHERE e.status = 'completed') AS completed_encounter_count
FROM patients AS p
LEFT JOIN encounters AS e ON e.patient_id = p.patient_id
GROUP BY p.patient_id`
GOLD SEMANTICS: base-entity-preserving; group survival=True
REFERENCE BEHAVIOR: RefA and RefB are independently validated witnesses for the same carrier/measure contract.
MODEL BEHAVIOR: PRESERVES_BASE_ENTITIES; qualifying-child measure
BASE: {'columns': ['patient_id', 'completed_encounter_count'], 'row_count': 4, 'rows': [{'completed_encounter_count': 1, 'patient_id': 1}, {'completed_encounter_count': 0, 'patient_id': 3}, {'completed_encounter_count': 0, 'patient_id': 4}, {'completed_encounter_count': 1, 'patient_id': 2}], 'session_timezone': 'UTC', 'status': 'ALLOWED', 'truncated': False}
COUNTERFACTUAL: first discriminator `healthcare_09_cf1_4de581`
ROOT CAUSE: population preserved; frozen row-order/result-contract mismatch or measure mismatch
RUNTIME-DETECTABLE SIGNAL: base population metadata plus AST carrier/join/predicate analysis
GOLD DEPENDENCY: truth semantic target, references, fixture outcomes; not permitted at runtime.

### CASE: insurance_02
QUESTION: For each policy product, return product and the count of claims opened in June 2026.
MODEL-VISIBLE CONTRACT: preservation expressed in question; no structured population field; schema/metric definitions and requested output grain
MODEL RESPONSE: ANSWER — `SELECT p.product, COUNT(c.claim_id) AS claim_count
FROM policies AS p
JOIN claims AS c ON c.policy_id = p.policy_id
WHERE c.opened_on >= DATE '2026-06-01'
  AND c.opened_on < DATE '2026-07-01'
GROUP BY p.product;`
GOLD SEMANTICS: base-entity-preserving; group survival=True
REFERENCE BEHAVIOR: RefA and RefB are independently validated witnesses for the same carrier/measure contract.
MODEL BEHAVIOR: FILTERS_TO_MATCHING_ENTITIES; qualifying-child measure
BASE: {'columns': ['product', 'claim_count'], 'row_count': 2, 'rows': [{'claim_count': 2, 'product': 'auto'}, {'claim_count': 1, 'product': 'home'}], 'session_timezone': 'UTC', 'status': 'ALLOWED', 'truncated': False}
COUNTERFACTUAL: first discriminator `insurance_02_cf1_b5da38`
ROOT CAUSE: population carrier loss
RUNTIME-DETECTABLE SIGNAL: base population metadata plus AST carrier/join/predicate analysis
GOLD DEPENDENCY: truth semantic target, references, fixture outcomes; not permitted at runtime.

### CASE: insurance_14
QUESTION: For each claim, return claim ID and total paid amount, including claims with no payments.
MODEL-VISIBLE CONTRACT: preservation expressed in question; no structured population field; schema/metric definitions and requested output grain
MODEL RESPONSE: ANSWER — `SELECT c.claim_id, COALESCE(SUM(cp.amount), 0) AS total_paid_amount FROM claims AS c LEFT JOIN claim_payments AS cp ON cp.claim_id = c.claim_id GROUP BY c.claim_id`
GOLD SEMANTICS: base-entity-preserving; group survival=True
REFERENCE BEHAVIOR: RefA and RefB are independently validated witnesses for the same carrier/measure contract.
MODEL BEHAVIOR: PRESERVES_BASE_ENTITIES; qualifying-child measure
BASE: {'columns': ['claim_id', 'total_paid_amount'], 'row_count': 5, 'rows': [{'claim_id': 202, 'total_paid_amount': '300.00'}, {'claim_id': 205, 'total_paid_amount': '0'}, {'claim_id': 204, 'total_paid_amount': '0'}, {'claim_id': 203, 'total_paid_amount': '100.00'}, {'claim_id': 201, 'total_paid_amount': '500.00'}], 'session_timezone': 'UTC', 'status': 'ALLOWED', 'truncated': False}
COUNTERFACTUAL: first discriminator `insurance_14_cf1_e1fe18`
ROOT CAUSE: population preserved; frozen row-order/result-contract mismatch or measure mismatch
RUNTIME-DETECTABLE SIGNAL: base population metadata plus AST carrier/join/predicate analysis
GOLD DEPENDENCY: truth semantic target, references, fixture outcomes; not permitted at runtime.

### CASE: marketplace_06
QUESTION: For each seller, return seller ID and the count of listings with no completed sale.
MODEL-VISIBLE CONTRACT: preservation expressed in question; no structured population field; schema/metric definitions and requested output grain
MODEL RESPONSE: ANSWER — `SELECT s.seller_id, COUNT(*) FILTER (WHERE o.order_id IS NULL OR o.status <> 'completed') AS listing_count_no_completed_sale
FROM sellers AS s
JOIN listings AS l ON l.seller_id = s.seller_id
LEFT JOIN order_lines AS ol ON ol.listing_id = l.listing_id
LEFT JOIN orders AS o ON o.order_id = ol.order_id
GROUP BY s.seller_id`
GOLD SEMANTICS: base-entity-preserving; group survival=True
REFERENCE BEHAVIOR: RefA and RefB are independently validated witnesses for the same carrier/measure contract.
MODEL BEHAVIOR: FILTERS_TO_MATCHING_ENTITIES; qualifying-child measure
BASE: {'columns': ['seller_id', 'listing_count_no_completed_sale'], 'row_count': 3, 'rows': [{'listing_count_no_completed_sale': 1, 'seller_id': 1}, {'listing_count_no_completed_sale': 0, 'seller_id': 2}, {'listing_count_no_completed_sale': 1, 'seller_id': 3}], 'session_timezone': 'UTC', 'status': 'ALLOWED', 'truncated': False}
COUNTERFACTUAL: first discriminator `marketplace_06_cf1_dec12c`
ROOT CAUSE: population carrier loss
RUNTIME-DETECTABLE SIGNAL: base population metadata plus AST carrier/join/predicate analysis
GOLD DEPENDENCY: truth semantic target, references, fixture outcomes; not permitted at runtime.

### CASE: marketplace_08
QUESTION: For each seller, return seller ID and average review score across its listings.
MODEL-VISIBLE CONTRACT: preservation expressed in question; no structured population field; schema/metric definitions and requested output grain
MODEL RESPONSE: ANSWER — `SELECT s.seller_id, AVG(r.score) AS average_review_score
FROM sellers AS s
JOIN listings AS l ON l.seller_id = s.seller_id
JOIN reviews AS r ON r.listing_id = l.listing_id
GROUP BY s.seller_id`
GOLD SEMANTICS: base-entity-preserving; group survival=True
REFERENCE BEHAVIOR: RefA and RefB are independently validated witnesses for the same carrier/measure contract.
MODEL BEHAVIOR: FILTERS_TO_MATCHING_ENTITIES; qualifying-child measure
BASE: {'columns': ['seller_id', 'average_review_score'], 'row_count': 3, 'rows': [{'average_review_score': '5.0000000000000000', 'seller_id': 1}, {'average_review_score': '3.0000000000000000', 'seller_id': 2}, {'average_review_score': '4.0000000000000000', 'seller_id': 3}], 'session_timezone': 'UTC', 'status': 'ALLOWED', 'truncated': False}
COUNTERFACTUAL: first discriminator `marketplace_08_cf1_09e1c5`
ROOT CAUSE: population carrier loss
RUNTIME-DETECTABLE SIGNAL: base population metadata plus AST carrier/join/predicate analysis
GOLD DEPENDENCY: truth semantic target, references, fixture outcomes; not permitted at runtime.

### CASE: procurement_06
QUESTION: For each supplier, return supplier ID and the count of purchase orders placed in June 2026.
MODEL-VISIBLE CONTRACT: preservation expressed in question; no structured population field; schema/metric definitions and requested output grain
MODEL RESPONSE: ANSWER — `SELECT supplier_id, COUNT(*) AS purchase_order_count
FROM purchase_orders
WHERE ordered_on >= DATE '2026-06-01'
  AND ordered_on < DATE '2026-07-01'
GROUP BY supplier_id;`
GOLD SEMANTICS: base-entity-preserving; group survival=True
REFERENCE BEHAVIOR: RefA and RefB are independently validated witnesses for the same carrier/measure contract.
MODEL BEHAVIOR: FILTERS_TO_MATCHING_ENTITIES; qualifying-child measure
BASE: {'columns': ['supplier_id', 'purchase_order_count'], 'row_count': 2, 'rows': [{'purchase_order_count': 1, 'supplier_id': 1}, {'purchase_order_count': 1, 'supplier_id': 2}], 'session_timezone': 'UTC', 'status': 'ALLOWED', 'truncated': False}
COUNTERFACTUAL: first discriminator `procurement_06_cf1_91c6e5`
ROOT CAUSE: population carrier loss
RUNTIME-DETECTABLE SIGNAL: base population metadata plus AST carrier/join/predicate analysis
GOLD DEPENDENCY: truth semantic target, references, fixture outcomes; not permitted at runtime.

### CASE: procurement_15
QUESTION: Return supplier IDs and total received units, including suppliers with no receipts.
MODEL-VISIBLE CONTRACT: preservation expressed in question; no structured population field; schema/metric definitions and requested output grain
MODEL RESPONSE: ANSWER — `SELECT s.supplier_id, COALESCE(SUM(r.received_qty), 0) AS total_received_units
FROM suppliers AS s
LEFT JOIN purchase_orders AS po ON po.supplier_id = s.supplier_id
LEFT JOIN po_lines AS pl ON pl.po_id = po.po_id
LEFT JOIN receipts AS r ON r.line_id = pl.line_id
GROUP BY s.supplier_id`
GOLD SEMANTICS: base-entity-preserving; group survival=True
REFERENCE BEHAVIOR: RefA and RefB are independently validated witnesses for the same carrier/measure contract.
MODEL BEHAVIOR: PRESERVES_BASE_ENTITIES; qualifying-child measure
BASE: {'columns': ['supplier_id', 'total_received_units'], 'row_count': 4, 'rows': [{'supplier_id': 1, 'total_received_units': 12}, {'supplier_id': 3, 'total_received_units': 0}, {'supplier_id': 4, 'total_received_units': 0}, {'supplier_id': 2, 'total_received_units': 5}], 'session_timezone': 'UTC', 'status': 'ALLOWED', 'truncated': False}
COUNTERFACTUAL: first discriminator `procurement_15_cf1_85299a`
ROOT CAUSE: population preserved; frozen row-order/result-contract mismatch or measure mismatch
RUNTIME-DETECTABLE SIGNAL: base population metadata plus AST carrier/join/predicate analysis
GOLD DEPENDENCY: truth semantic target, references, fixture outcomes; not permitted at runtime.

### CASE: workforce_10
QUESTION: For each employee, return employee ID and total payroll adjustment amount.
MODEL-VISIBLE CONTRACT: preservation expressed in question; no structured population field; schema/metric definitions and requested output grain
MODEL RESPONSE: ANSWER — `SELECT e.employee_id, COALESCE(SUM(pa.amount), 0) AS total_payroll_adjustment_amount
FROM employees AS e
LEFT JOIN payroll_adjustments AS pa ON pa.employee_id = e.employee_id
GROUP BY e.employee_id`
GOLD SEMANTICS: base-entity-preserving; group survival=True
REFERENCE BEHAVIOR: RefA and RefB are independently validated witnesses for the same carrier/measure contract.
MODEL BEHAVIOR: PRESERVES_BASE_ENTITIES; qualifying-child measure
BASE: {'columns': ['employee_id', 'total_payroll_adjustment_amount'], 'row_count': 4, 'rows': [{'employee_id': 1, 'total_payroll_adjustment_amount': '100.00'}, {'employee_id': 3, 'total_payroll_adjustment_amount': '0'}, {'employee_id': 4, 'total_payroll_adjustment_amount': '0'}, {'employee_id': 2, 'total_payroll_adjustment_amount': '-20.00'}], 'session_timezone': 'UTC', 'status': 'ALLOWED', 'truncated': False}
COUNTERFACTUAL: first discriminator `workforce_10_cf1_27ffa3`
ROOT CAUSE: population preserved; frozen row-order/result-contract mismatch or measure mismatch
RUNTIME-DETECTABLE SIGNAL: base population metadata plus AST carrier/join/predicate analysis
GOLD DEPENDENCY: truth semantic target, references, fixture outcomes; not permitted at runtime.

## Negative-control case-by-case forensics

- `healthcare_06`: Matching-only population makes INNER/child-only structure valid; hypothetical detector=PASS.
- `marketplace_05`: LEFT JOIN with child qualification in ON preserves base entities; hypothetical detector=PASS.
- `procurement_11`: Matching-only population makes INNER/child-only structure valid; hypothetical detector=PASS.
- `telecom_08`: LEFT JOIN with child qualification in ON preserves base entities; hypothetical detector=PASS.
- `workforce_12`: LEFT JOIN with child qualification in ON preserves base entities; hypothetical detector=PASS.

## Feasibility conclusion

Current metadata is insufficient. A generic production-legitimate population metadata extension is technically feasible, but the first shadow invariant detects only 4/9 designated targets with 0/5 primary-control false positives. It is not ready for enforcement.

## Final feasibility verdict

VALIDATOR_FEASIBLE_ONLY_WITH_PRODUCTION_LEGITIMATE_METADATA_EXTENSION

## Historical preservation
M52 remains `EXPANSION_RESIDUAL_ROOT_CAUSES_LOCALIZED`; no historical artifacts were changed.
## M52.1 scope
Zero-call, post-hoc feasibility analysis; detector measurements are not an independent generalization estimate.
## Zero-call accounting
Provider/model/LLM/embedding/reranker calls: 0; retries, repairs, judges, selectors: 0.
## Frozen evidence integrity
Response corpus `9783930bb0dac022a926c6638822e56747630f3605910cbf8487d409c5105d9a` and benchmark hashes were verified before analysis.
## Target population
Nine GROUP_SURVIVAL-primary M52 targets: healthcare_07, healthcare_09, insurance_02, insurance_14, marketplace_06, marketplace_08, procurement_06, procurement_15, workforce_10.
## Negative controls
Five primary controls: healthcare_06, marketplace_05, procurement_11, telecom_08, workforce_12. Healthcare_06 is the fifth structurally related control found in frozen evidence; M52's artifact listed four.
## Evidence inspection methodology
Each target and control was joined to its question, rendered context hash, raw response, SQL, truth contract, witnesses, BASE/CF trace evidence, and M52/M51BR classifications.
## Gold/evaluator evidence boundary
Gold, references, and fixture outcomes were used for offline discovery and scoring only; the detector input excludes them.
## Model-response inspection
Raw response decision and SQL were retained; no response normalization or repair was applied.
Case-level target and control records are also persisted as JSONL artifacts for machine-readable review.
## Base population vs measure population
The preserved-population targets require a base carrier (supplier, policy, seller, patient, claim, or employee) while child rows contribute the measure; matching-only controls do not.
## Group-survival subtypes
Frozen M52 labels comprise one filtered-child-source case, three inner-join carrier-loss cases, and five cases whose observed failure was not confirmed as population loss.
## Gold-vs-model semantic deltas
Four targets show candidate carrier loss; five retain the carrier and fail through another result-contract or measure behavior.
## Reference A/B shared semantic invariants
Both references were treated as semantic witnesses; their shared invariant is the required carrier/measure contract, not SQL shape.
## Counterfactual discriminators
The first discriminating fixture and all frozen candidate CF outcomes are recorded per target; CFs are forensic evidence only.
## Existing structured semantic metadata
Current context exposes schema, relationships, cardinality, metrics, and policy, but no case-level population-preservation or zero-group field.
## Gold-to-visible semantic gap
0/9 fully structured; 9/9 visible only through question semantics for population preservation; no target had the required property as current structured metadata.
## Candidate runtime inputs
SQL AST, schema/cardinality, authorized relationships, and a production-legitimate structured population/base-entity/zero-group contract.
## Forbidden runtime inputs
Case/domain IDs, truth, references, expected results, fixtures, M52 labels, and arbitrary evaluator fields.
## Candidate invariants
I1: with explicit base preservation, flag child-only carriers, top-level inner child joins, or child WHERE predicates that can eliminate required base entities. Diagnostic only; fail-open and no rewrite.
## Invariant iteration ledger
One frozen generic revision (I1); no model- or case-driven tuning iterations.
## Target/control confusion matrix
Current metadata: TP 0/9, FN 9/9, FP 0/5, TN 5/5. Hypothetical metadata extension: TP 4/9, FN 5/9, FP 0/5, TN 5/5; precision 100%, recall 4/9, specificity 100%.
## Broader group-survival shadow evaluation
15 frozen group-survival-tagged SQL rows evaluated; 4 flagged, with zero correct-case flags.
## Full correct-SQL false-positive screen
Expansion-only frozen correct ANSWER SQL screen: 22 evaluated, 0 flagged (0/22). Legacy model SQL was not re-run or required.
## False positives
None in the primary controls or the expansion correct-SQL screen.
## False negatives
Hypothetical detector false negatives: healthcare_07, healthcare_09, insurance_14, procurement_15, workforce_10.
## Deterministic observability
The carrier/join/predicate evidence is deterministic once population semantics are structured; current metadata cannot supply that semantic precondition.
## Metadata gaps
Missing generic fields: metric carrier/base entity, population inclusion mode, zero-group policy, and measure-contributing population.
## Production-legitimate metadata extensions
These fields can be legitimate semantic-catalog concepts, but M52.1 does not implement or approve them for enforcement.
## Gold dependency audit
Gold used to discover the invariant: YES. Gold required at runtime: NO. Reference SQL/expected result/counterfactual/case/domain IDs required at runtime: NO.
## Detector feasibility
Current metadata alone is not sufficient. The generic extension-based signal is precise on frozen controls but partial on the designated target labels (4/9).
## Safe normalization assessment
No unique truth-free rewrite was established; automatic SQL repair is not justified.
## Negative capabilities
SQL shape alone cannot distinguish matching-only INNER JOINs from preservation-required INNER JOINs, and arbitrary NL interpretation is outside a deterministic validator.
## Feasibility decision
`VALIDATOR_FEASIBLE_ONLY_WITH_PRODUCTION_LEGITIMATE_METADATA_EXTENSION`.
## Recommended next milestone
M52.2 — Population Semantic Metadata Contract Feasibility (zero-call). Begin with metadata legitimacy and detector feasibility; keep any signal shadow-only.
## Tests
Focused M52.1 tests: 5 passed. Ruff and mypy passed for changed M52.1 files.
## Determinism
The analysis is canonicalized and replayed twice with identical detector logic and frozen input hashes.
## Repository state
M52.1 changes are audit/test artifacts only; app runtime, SQL, decisions, benchmark, and frozen evidence remain unchanged.