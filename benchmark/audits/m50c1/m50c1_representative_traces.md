# M50C.1 Representative Traces

## DECISION_FALSE_ABSTENTION: subscription_06

- trace hash: `e98bab9632217f9bb5ffa3079e80351189892c8a7a71117dcc15a4c7e4e312cc`
- first runtime failure: `NONE`
- first evaluator divergence: `DECISION_FALSE_ABSTENTION`

### INPUT_CONTEXT

- status: `PASS`

### FROZEN_PROVIDER_RESPONSE

- status: `PASS`

### SUBMISSION_PARSE

- status: `PASS`

### DECISION_BRANCH

- status: `PASS`
- reason: `NON_ANSWER_BRANCH`
- diagnostics: `{"decision": "NEEDS_CLARIFICATION"}`

### SQL_CANDIDATE

- status: `SKIPPED`
- skipped: `NON_ANSWER_SUBMISSION`

### SQL_PARSE

- status: `SKIPPED`
- skipped: `NON_ANSWER_SUBMISSION`

### POLICY

- status: `SKIPPED`
- skipped: `NON_ANSWER_SUBMISSION`

### GRAIN_INPUT_DIAGNOSTIC

- status: `SKIPPED`
- skipped: `NON_ANSWER_SUBMISSION`

### GRAIN_NORMALIZATION

- status: `SKIPPED`
- skipped: `NON_ANSWER_SUBMISSION`

### POST_NORMALIZATION_PARSE

- status: `SKIPPED`
- skipped: `NON_ANSWER_SUBMISSION`

### POST_NORMALIZATION_POLICY

- status: `SKIPPED`
- skipped: `NON_ANSWER_SUBMISSION`

### POST_NORMALIZATION_GRAIN

- status: `SKIPPED`
- skipped: `NON_ANSWER_SUBMISSION`

### RESTRICTED_READER_SETUP

- status: `SKIPPED`
- skipped: `NON_ANSWER_SUBMISSION`

### EXPLAIN

- status: `SKIPPED`
- skipped: `NON_ANSWER_SUBMISSION`

### COST_GATE

- status: `SKIPPED`
- skipped: `NON_ANSWER_SUBMISSION`

### QUERY_PLAN

- status: `SKIPPED`
- skipped: `NON_ANSWER_SUBMISSION`

### EXECUTION

- status: `SKIPPED`
- skipped: `NON_ANSWER_SUBMISSION`

### BASE_RESULT

- status: `SKIPPED`
- skipped: `NON_ANSWER_SUBMISSION`

### COUNTERFACTUAL_RESULT

- status: `SKIPPED`
- skipped: `NON_ANSWER_SUBMISSION`

## DECISION_FALSE_ANSWER: subscription_18

- trace hash: `696f9f0c3b57abe35f4e19f9c92e26ce0ca4e4400bf0074b31ff12c36d1b02a4`
- first runtime failure: `NONE`
- first evaluator divergence: `DECISION_FALSE_ANSWER`

### INPUT_CONTEXT

- status: `PASS`

### FROZEN_PROVIDER_RESPONSE

- status: `PASS`

### SUBMISSION_PARSE

- status: `PASS`

### DECISION_BRANCH

- status: `PASS`
- reason: `ANSWER_BRANCH`
- diagnostics: `{"decision": "ANSWER"}`

### SQL_CANDIDATE

- status: `PASS`

### SQL_PARSE

- status: `PASS`
- diagnostics: `{"grains": [{"child_measure_ids": [], "fanout_relationship_ids": [], "input_diagnostic": {"aggregate_expressions": [], "code": "NOT_APPLICABLE", "evidence": {}, "fanout_edges": [], "grouping_columns": [], "measure_ids": [], "message": "No additive parent measure was aggregated across a modeled fanout edge.", "native_grains": []}, "input_sql_hash": "cd05689d26cb20f6ecec0f5cb745b0ee5bc8445ecf9a71797558927cf54fa080", "normalization_reason": null, "normalization_status": null, "output_diagnostic": {"aggregate_expressions": [], "code": "NOT_APPLICABLE", "evidence": {}, "fanout_edges": [], "grouping_columns": [], "measure_ids": [], "message": "No additive parent measure was aggregated across a modeled fanout edge.", "native_grains": []}, "parent_measure_ids": [], "runtime_reason": "ALREADY_SAFE", "selected_sql": "SELECT DISTINCT a.account_id, a.account_name\nFROM accounts AS a\nJOIN subscriptions AS s ON s.account_id = a.account_id\nWHERE s.status = 'active'\n  AND s.starts_on <= DATE '2026-06-30'\n  AND (s.ends_on IS NULL OR s.ends_on >= DATE '2026-06-30');", "selected_sql_hash": "cd05689d26cb20f6ecec0f5cb745b0ee5bc8445ecf9a71797558927cf54fa080", "status": "UNCHANGED"}], "states": ["base"]}`

### POLICY

- status: `PASS`

### GRAIN_INPUT_DIAGNOSTIC

- status: `PASS`
- diagnostics: `{"grains": [{"child_measure_ids": [], "fanout_relationship_ids": [], "input_diagnostic": {"aggregate_expressions": [], "code": "NOT_APPLICABLE", "evidence": {}, "fanout_edges": [], "grouping_columns": [], "measure_ids": [], "message": "No additive parent measure was aggregated across a modeled fanout edge.", "native_grains": []}, "input_sql_hash": "cd05689d26cb20f6ecec0f5cb745b0ee5bc8445ecf9a71797558927cf54fa080", "normalization_reason": null, "normalization_status": null, "output_diagnostic": {"aggregate_expressions": [], "code": "NOT_APPLICABLE", "evidence": {}, "fanout_edges": [], "grouping_columns": [], "measure_ids": [], "message": "No additive parent measure was aggregated across a modeled fanout edge.", "native_grains": []}, "parent_measure_ids": [], "runtime_reason": "ALREADY_SAFE", "selected_sql": "SELECT DISTINCT a.account_id, a.account_name\nFROM accounts AS a\nJOIN subscriptions AS s ON s.account_id = a.account_id\nWHERE s.status = 'active'\n  AND s.starts_on <= DATE '2026-06-30'\n  AND (s.ends_on IS NULL OR s.ends_on >= DATE '2026-06-30');", "selected_sql_hash": "cd05689d26cb20f6ecec0f5cb745b0ee5bc8445ecf9a71797558927cf54fa080", "status": "UNCHANGED"}], "states": ["base"]}`

### GRAIN_NORMALIZATION

- status: `UNCHANGED`

### POST_NORMALIZATION_PARSE

- status: `NOT_APPLICABLE`

### POST_NORMALIZATION_POLICY

- status: `NOT_APPLICABLE`

### POST_NORMALIZATION_GRAIN

- status: `NOT_APPLICABLE`

### RESTRICTED_READER_SETUP

- status: `PASS`

### EXPLAIN

- status: `PASS`

### COST_GATE

- status: `PASS`

### QUERY_PLAN

- status: `PASS`

### EXECUTION

- status: `PASS`

## RESULT_BASE: warehouse_03

- trace hash: `6bf847df20e9a2446117dc1d5420246d9dbc8697638567bf1d8d836428235700`
- first runtime failure: `NONE`
- first evaluator divergence: `RESULT_BASE`

### INPUT_CONTEXT

- status: `PASS`

### FROZEN_PROVIDER_RESPONSE

- status: `PASS`

### SUBMISSION_PARSE

- status: `PASS`

### DECISION_BRANCH

- status: `PASS`
- reason: `ANSWER_BRANCH`
- diagnostics: `{"decision": "ANSWER"}`

### SQL_CANDIDATE

- status: `PASS`

### SQL_PARSE

- status: `PASS`
- diagnostics: `{"grains": [{"child_measure_ids": [], "fanout_relationship_ids": [], "input_diagnostic": {"aggregate_expressions": [], "code": "NOT_APPLICABLE", "evidence": {}, "fanout_edges": [], "grouping_columns": [], "measure_ids": [], "message": "No additive parent measure was aggregated across a modeled fanout edge.", "native_grains": []}, "input_sql_hash": "4edc60df2d8b7b9c253e92c6f88be0b55921c4483eb5cff3629839f3d80d76ed", "normalization_reason": null, "normalization_status": null, "output_diagnostic": {"aggregate_expressions": [], "code": "NOT_APPLICABLE", "evidence": {}, "fanout_edges": [], "grouping_columns": [], "measure_ids": [], "message": "No additive parent measure was aggregated across a modeled fanout edge.", "native_grains": []}, "parent_measure_ids": [], "runtime_reason": "ALREADY_SAFE", "selected_sql": "SELECT s.carrier_id, COUNT(DISTINCT s.shipment_id) AS late_shipments\nFROM shipments AS s\nJOIN carriers AS c ON c.carrier_id = s.carrier_id\nJOIN delivery_events AS de ON de.shipment_id = s.shipment_id\nWHERE de.event_type = 'delivered'\n  AND de.event_at > s.promised_at\nGROUP BY s.carrier_id", "selected_sql_hash": "4edc60df2d8b7b9c253e92c6f88be0b55921c4483eb5cff3629839f3d80d76ed", "status": "UNCHANGED"}], "states": ["base"]}`

### POLICY

- status: `PASS`

### GRAIN_INPUT_DIAGNOSTIC

- status: `PASS`
- diagnostics: `{"grains": [{"child_measure_ids": [], "fanout_relationship_ids": [], "input_diagnostic": {"aggregate_expressions": [], "code": "NOT_APPLICABLE", "evidence": {}, "fanout_edges": [], "grouping_columns": [], "measure_ids": [], "message": "No additive parent measure was aggregated across a modeled fanout edge.", "native_grains": []}, "input_sql_hash": "4edc60df2d8b7b9c253e92c6f88be0b55921c4483eb5cff3629839f3d80d76ed", "normalization_reason": null, "normalization_status": null, "output_diagnostic": {"aggregate_expressions": [], "code": "NOT_APPLICABLE", "evidence": {}, "fanout_edges": [], "grouping_columns": [], "measure_ids": [], "message": "No additive parent measure was aggregated across a modeled fanout edge.", "native_grains": []}, "parent_measure_ids": [], "runtime_reason": "ALREADY_SAFE", "selected_sql": "SELECT s.carrier_id, COUNT(DISTINCT s.shipment_id) AS late_shipments\nFROM shipments AS s\nJOIN carriers AS c ON c.carrier_id = s.carrier_id\nJOIN delivery_events AS de ON de.shipment_id = s.shipment_id\nWHERE de.event_type = 'delivered'\n  AND de.event_at > s.promised_at\nGROUP BY s.carrier_id", "selected_sql_hash": "4edc60df2d8b7b9c253e92c6f88be0b55921c4483eb5cff3629839f3d80d76ed", "status": "UNCHANGED"}], "states": ["base"]}`

### GRAIN_NORMALIZATION

- status: `UNCHANGED`

### POST_NORMALIZATION_PARSE

- status: `NOT_APPLICABLE`

### POST_NORMALIZATION_POLICY

- status: `NOT_APPLICABLE`

### POST_NORMALIZATION_GRAIN

- status: `NOT_APPLICABLE`

### RESTRICTED_READER_SETUP

- status: `PASS`

### EXPLAIN

- status: `PASS`

### COST_GATE

- status: `PASS`

### QUERY_PLAN

- status: `PASS`

### EXECUTION

- status: `PASS`

## NONE: commerce_01

- trace hash: `572f2b0e60067dd92c282afc12c11b12aeefe5587515487e12fea7abbeeabb03`
- first runtime failure: `NONE`
- first evaluator divergence: `NONE`

### INPUT_CONTEXT

- status: `PASS`

### FROZEN_PROVIDER_RESPONSE

- status: `PASS`

### SUBMISSION_PARSE

- status: `PASS`

### DECISION_BRANCH

- status: `PASS`
- reason: `ANSWER_BRANCH`
- diagnostics: `{"decision": "ANSWER"}`

### SQL_CANDIDATE

- status: `PASS`

### SQL_PARSE

- status: `PASS`
- diagnostics: `{"grains": [{"child_measure_ids": [], "fanout_relationship_ids": [], "input_diagnostic": {"aggregate_expressions": [], "code": "NOT_APPLICABLE", "evidence": {}, "fanout_edges": [], "grouping_columns": [], "measure_ids": [], "message": "No additive parent measure was aggregated across a modeled fanout edge.", "native_grains": []}, "input_sql_hash": "d9592d1fca26f84490c712cafa496af1df27dcdf33a2c382125ba8bcab222d2d", "normalization_reason": null, "normalization_status": null, "output_diagnostic": {"aggregate_expressions": [], "code": "NOT_APPLICABLE", "evidence": {}, "fanout_edges": [], "grouping_columns": [], "measure_ids": [], "message": "No additive parent measure was aggregated across a modeled fanout edge.", "native_grains": []}, "parent_measure_ids": [], "runtime_reason": "ALREADY_SAFE", "selected_sql": "SELECT customer_id, customer_name\nFROM customers\nWHERE is_active = TRUE\n  AND region = 'North';", "selected_sql_hash": "d9592d1fca26f84490c712cafa496af1df27dcdf33a2c382125ba8bcab222d2d", "status": "UNCHANGED"}], "states": ["base"]}`

### POLICY

- status: `PASS`

### GRAIN_INPUT_DIAGNOSTIC

- status: `PASS`
- diagnostics: `{"grains": [{"child_measure_ids": [], "fanout_relationship_ids": [], "input_diagnostic": {"aggregate_expressions": [], "code": "NOT_APPLICABLE", "evidence": {}, "fanout_edges": [], "grouping_columns": [], "measure_ids": [], "message": "No additive parent measure was aggregated across a modeled fanout edge.", "native_grains": []}, "input_sql_hash": "d9592d1fca26f84490c712cafa496af1df27dcdf33a2c382125ba8bcab222d2d", "normalization_reason": null, "normalization_status": null, "output_diagnostic": {"aggregate_expressions": [], "code": "NOT_APPLICABLE", "evidence": {}, "fanout_edges": [], "grouping_columns": [], "measure_ids": [], "message": "No additive parent measure was aggregated across a modeled fanout edge.", "native_grains": []}, "parent_measure_ids": [], "runtime_reason": "ALREADY_SAFE", "selected_sql": "SELECT customer_id, customer_name\nFROM customers\nWHERE is_active = TRUE\n  AND region = 'North';", "selected_sql_hash": "d9592d1fca26f84490c712cafa496af1df27dcdf33a2c382125ba8bcab222d2d", "status": "UNCHANGED"}], "states": ["base"]}`

### GRAIN_NORMALIZATION

- status: `UNCHANGED`

### POST_NORMALIZATION_PARSE

- status: `NOT_APPLICABLE`

### POST_NORMALIZATION_POLICY

- status: `NOT_APPLICABLE`

### POST_NORMALIZATION_GRAIN

- status: `NOT_APPLICABLE`

### RESTRICTED_READER_SETUP

- status: `PASS`

### EXPLAIN

- status: `PASS`

### COST_GATE

- status: `PASS`

### QUERY_PLAN

- status: `PASS`

### EXECUTION

- status: `PASS`
