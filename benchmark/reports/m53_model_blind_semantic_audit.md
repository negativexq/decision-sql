# M53 model-blind semantic audit

This Pass A report was generated without reading the M51B response corpus. The defect ledger is frozen before Pass B.

## Scope

90/90 expansion cases were audited: 60 answerable and 30 non-answerable. Provider/model calls: 0.

## Defect counts

- `CONTEXT_SUFFICIENCY_DEFECT`: 8
- `HIDDEN_NULL_SEMANTICS_DEFECT`: 10
- `HIDDEN_TEMPORAL_BOUNDARY_DEFECT`: 1
- `QUESTION_GOLD_POPULATION_AMBIGUITY`: 13
- `RESULT_ORDER_CONTRACT_DEFECT`: 52

## Severity

- `BLOCKING`: 22
- `SCORING_MATERIAL`: 62

## Answerable case ledger

| Case | Question | Requested row order | Defects |
|---|---|---:|---|
| `healthcare_01` | Return patient IDs whose profile age is at least 65. | no | RESULT_ORDER_CONTRACT_DEFECT, CONTEXT_SUFFICIENCY_DEFECT |
| `healthcare_02` | For each provider, return provider ID and total billed charge amount for completed encounters. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `healthcare_05` | For each provider, return provider ID and the count of distinct patients seen in completed encounters. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `healthcare_06` | List patient IDs with completed encounters. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `healthcare_07` | For each patient, return patient ID and total posted payment amount, including patients with none. | no | RESULT_ORDER_CONTRACT_DEFECT, CONTEXT_SUFFICIENCY_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT |
| `healthcare_09` | For each patient, return patient ID and count of completed encounters, including patients with none. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `healthcare_10` | Rank providers by total posted payment-related charge amount, highest first, returning provider ID and rank. | yes | NO_DEFECT |
| `healthcare_11` | For each patient, return the date of the latest encounter. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `healthcare_12` | For each procedure code, return the code and count of procedures performed in June 2026. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `healthcare_15` | List patient IDs with no diagnosis code beginning with E. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `insurance_02` | For each policy product, return product and the count of claims opened in June 2026. | no | RESULT_ORDER_CONTRACT_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY |
| `insurance_04` | List policy IDs with no open claim. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `insurance_05` | Return claim IDs whose loss data severity is at least 4. | no | RESULT_ORDER_CONTRACT_DEFECT, CONTEXT_SUFFICIENCY_DEFECT |
| `insurance_06` | List policy IDs for active policies, in policy ID order. | yes | NO_DEFECT |
| `insurance_07` | For each policyholder region, return region and total reported loss for open claims. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `insurance_08` | For each claim, return claim ID and the number of posted payments. | no | RESULT_ORDER_CONTRACT_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY |
| `insurance_10` | Rank policyholders by total reported loss, returning holder ID and rank. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `insurance_11` | For each claim, return the timestamp of its latest lifecycle event. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `insurance_12` | For each claim, return claim ID and the number of days from opening to its latest event. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `insurance_14` | For each claim, return claim ID and total paid amount, including claims with no payments. | no | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT |
| `marketplace_01` | Return buyer IDs with no disputed order. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `marketplace_03` | Rank listings by unit price from highest to lowest, returning listing ID and rank. | yes | NO_DEFECT |
| `marketplace_04` | For each seller, return seller ID and gross merchandise value for completed orders. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `marketplace_05` | For each buyer, return buyer ID and the number of completed orders. | no | RESULT_ORDER_CONTRACT_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY |
| `marketplace_06` | For each seller, return seller ID and the count of listings with no completed sale. | no | RESULT_ORDER_CONTRACT_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY |
| `marketplace_07` | For each seller, return seller ID and total posted payout amount. | no | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY |
| `marketplace_08` | For each seller, return seller ID and average review score across its listings. | no | RESULT_ORDER_CONTRACT_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY |
| `marketplace_10` | For each listing, return listing ID and total quantity sold in completed orders. | no | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY |
| `marketplace_13` | List active listing IDs in listing order. | yes | NO_DEFECT |
| `marketplace_14` | Return dispute IDs opened in June 2026 that remain open. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `procurement_02` | List supplier IDs with no open purchase order. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `procurement_05` | For each department, return department and the estimated amount of approved requisitions requested in June 2026. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `procurement_06` | For each supplier, return supplier ID and the count of purchase orders placed in June 2026. | no | RESULT_ORDER_CONTRACT_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY |
| `procurement_07` | List active supplier IDs in ascending order. | yes | NO_DEFECT |
| `procurement_08` | Rank suppliers by total ordered spend, highest spend first, returning supplier ID and rank. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `procurement_10` | For each purchase-order line, return line ID and fulfillment rate, received units divided by ordered units. | no | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT |
| `procurement_11` | For each supplier, return supplier ID and ordered spend for open purchase orders. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `procurement_12` | Return requisition IDs whose metadata risk score is at least 70. | no | RESULT_ORDER_CONTRACT_DEFECT, CONTEXT_SUFFICIENCY_DEFECT |
| `procurement_14` | For each purchase-order line, return its latest receipt timestamp. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `procurement_15` | Return supplier IDs and total received units, including suppliers with no receipts. | no | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT |
| `telecom_02` | List subscriber IDs with active subscriptions. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `telecom_04` | Rank plans by monthly fee from highest to lowest, returning plan ID and rank. | yes | NO_DEFECT |
| `telecom_05` | For each market, return market and the number of outages that began in June 2026. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `telecom_06` | Return usage IDs whose detail payload reports at least 500 kilobytes of overhead. | no | RESULT_ORDER_CONTRACT_DEFECT, CONTEXT_SUFFICIENCY_DEFECT |
| `telecom_07` | For each subscriber, return subscriber ID and the plan ID of the latest subscription record. | no | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_TEMPORAL_BOUNDARY_DEFECT |
| `telecom_08` | For each plan, return plan ID and the count of active subscribers, including plans with none. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `telecom_09` | List subscriber IDs with no posted payment. | no | RESULT_ORDER_CONTRACT_DEFECT, CONTEXT_SUFFICIENCY_DEFECT |
| `telecom_10` | For each market, return market and total June megabytes used. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `telecom_11` | For each subscriber, return subscriber ID and the average invoice amount, preserving subscribers with no invoice. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `telecom_12` | For each invoice, return invoice ID and the amount still unpaid. | no | RESULT_ORDER_CONTRACT_DEFECT, CONTEXT_SUFFICIENCY_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY |
| `workforce_01` | For each employee, return employee ID and total hours including approved timesheets only. | no | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY |
| `workforce_02` | For each employee, return employee ID and the count of absence days for approved absences. | no | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY |
| `workforce_04` | Return employee IDs whose profile risk score is at least 3. | no | RESULT_ORDER_CONTRACT_DEFECT, CONTEXT_SUFFICIENCY_DEFECT |
| `workforce_06` | List employee IDs with no approved timesheet in June 2026. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `workforce_07` | Rank teams by total approved hours, highest first, returning team ID and rank. | yes | NO_DEFECT |
| `workforce_08` | For each team, return team ID and approved hours recorded in June 2026. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `workforce_10` | For each employee, return employee ID and total payroll adjustment amount. | no | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY |
| `workforce_12` | For each team, return team ID and count of employees with a shift starting in June 2026, including teams with none. | no | RESULT_ORDER_CONTRACT_DEFECT |
| `workforce_13` | List active employee IDs in ascending order. | yes | NO_DEFECT |
| `workforce_14` | For each employee, return employee ID and the latest completed training date. | no | RESULT_ORDER_CONTRACT_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY |

## Non-answerable audit

All 15 authority, 9 ambiguity, and 6 policy cases were inspected model-blind. Missing-evidence contract defects, if any, are recorded in the JSON ledger.

## Freeze boundary

No M51B response, candidate SQL, score, or response-derived artifact was read while deciding the classifications above.
