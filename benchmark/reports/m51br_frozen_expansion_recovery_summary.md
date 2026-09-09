# M51B-R — Frozen Expansion Response Recovery & Generalization Adjudication

## Historical preservation

M51B remains `M51B_ABORTED_POST_RESPONSE_CONTRACT_DEFECT`. Official legacy results remain 78/90 governed and 51/60 Answerable Runtime TSA.

## M51B historical abort status

Preserved. The original post-response `None`/string sorting `TypeError` remains historical evidence.

## M51B-R scope

Zero-call recovery analysis over the exact frozen M51B expansion corpus.

## Zero-call accounting

Provider calls: 0. Model calls: 0. Retries, repairs, judges, and selectors: 0.

## Starting repository state

M51B-R started at `342b9b0f702233f1075faa6e5dbf58aba9d6743f` with a clean synchronized worktree.

## Frozen benchmark integrity

All expansion, full-benchmark, and manifest hashes matched. The 90-case benchmark was not modified.

## Frozen response corpus integrity

90/90 responses were processed. Corpus hash: `9783930bb0dac022a926c6638822e56747630f3605910cbf8487d409c5105d9a`. Frozen responses modified: NO.

## Historical analysis defect

Reproduced: sorting mixed `None` and string keys during decision-matrix serialization.

## Serializer correction

A generic typed-key serializer preserves null distinctly and orders null first, followed by strings lexicographically. No rows were dropped.

## Evidence non-mutation proof

Responses, benchmark, truth, references, fixtures, prompt, evaluator semantics, and runtime semantics remained unchanged.

## Decision distribution

ANSWER 53; NEEDS_CLARIFICATION 19; BLOCKED_AUTHORITY 12; BLOCKED_POLICY 6.

## Truth × decision matrix

Complete 90-case matrix: see [m51br_truth_decision_matrix.json](/Users/ofk/DecisionSQL/benchmark/audits/m51br/m51br_truth_decision_matrix.json).

## Expansion Governed Task Success

47/90 (52.22%).

## Expansion Answerable Runtime TSA

22/60 (36.67%).

## BASE correctness

29/60.

## Full counterfactual correctness

22/60.

## BASE-only accidental correctness

7 cases: insurance_07, telecom_02, telecom_05, marketplace_06, marketplace_08, workforce_06, healthcare_11.

## Conditional ANSWER correctness

22/50 (44.00%).

## Wrong refusals

10 answerable cases.

## Authority safety

12/15 correct; unauthorized ANSWER count 1. Safety label: `AUTHORITY_SAFETY_REGRESSION`.

## Ambiguity correctness

7/9.

## Policy correctness

6/6.

## Domain results

Healthcare 9/15; insurance 8/15; marketplace 9/15; procurement 6/15; telecom 6/15; workforce 9/15.

## Mechanism results

Frozen mechanism-tag metrics are recorded in [m51br_mechanism_metrics.json](/Users/ofk/DecisionSQL/benchmark/audits/m51br/m51br_mechanism_metrics.json).

## Counterfactual failure forensics

26 historical `RESULT_COUNTERFACTUAL` signals were recovered; 7 were BASE-correct/counterfactual-only failures. Reference and ResultContract evidence remained frozen.

## Population/group-survival results

Frozen-tag results are recorded without adding a population contract.

## Grain/fanout results

2 GRAIN first failures; unsafe raw fallback 0; execution outside QueryPlan 0.

## JSON typing results

Five JSON-typing cases are recorded in [m51br_json_typing_metrics.json](/Users/ofk/DecisionSQL/benchmark/audits/m51br/m51br_json_typing_metrics.json).

## NULL/anti-join results

See [m51br_null_antijoin_metrics.json](/Users/ofk/DecisionSQL/benchmark/audits/m51br/m51br_null_antijoin_metrics.json).

## Temporal results

See [m51br_temporal_metrics.json](/Users/ofk/DecisionSQL/benchmark/audits/m51br/m51br_temporal_metrics.json).

## Window/CTE/subquery results

See [m51br_window_cte_subquery_metrics.json](/Users/ofk/DecisionSQL/benchmark/audits/m51br/m51br_window_cte_subquery_metrics.json).

## Runtime first failures

NONE 88; GRAIN 2.

## Evaluator first divergences

NONE 47; DECISION_FALSE_ABSTENTION 10; DECISION_FALSE_ANSWER 3; DECISION_WRONG_BLOCK_TYPE 2; GRAIN 2; RESULT_BASE 19; RESULT_COUNTERFACTUAL 7.

## Failure decomposition

43 failures: DECISION_FALSE_ABSTENTION 10; DECISION_FALSE_ANSWER 3; DECISION_WRONG_BLOCK_TYPE 2; GRAIN 2; RESULT_BASE 19; RESULT_COUNTERFACTUAL 7.

## Token accounting

Prompt median 4,740, p90 5,006, max 5,013, total 425,162. Completion median 53, p90 105, max 143, total 5,914.

## Latency

Median 1,489.62 ms; p90 1,842.81 ms; max 5,727.61 ms.

## Legacy vs expansion governed comparison

Legacy 78/90 (86.67%); expansion 47/90 (52.22%).

## Legacy vs expansion Answerable TSA comparison

Legacy 51/60 (85.00%); expansion 22/60 (36.67%).

## Governed generalization gap

-34.44 percentage points.

## Answerable generalization gap

-48.33 percentage points.

## Generalization label

`MATERIAL_GENERALIZATION_DROP`.

## Combined 180-case Governed Task Success

125/180 (69.44%).

## Combined 120-case Answerable Runtime TSA

73/120 (60.83%).

## Combined authority result

27/30.

## Combined ambiguity result

13/18.

## Combined policy result

12/12.

## Combined unauthorized ANSWER

1/30; `AUTHORITY_SAFETY_REGRESSION`.

## Suspected benchmark defects

None identified by the frozen-evidence screen; no repair was performed.

## Determinism

PASS. Two full zero-call replays were byte-identical and preserved the response hash.

## Tests

13 focused serializer/parent tests passed. Ruff, format, mypy, and diff checks passed. No new M51B-R regression was observed.

## Repository state

Final `HEAD == origin/main` at `342b9b0f702233f1075faa6e5dbf58aba9d6743f`; worktree clean after final commit.

## Final M51B-R scientific verdict

`FROZEN_M51B_EVIDENCE_FULLY_ADJUDICATED`.

## Recommended next milestone

`M52 — Expansion Residual Semantic Forensics`, zero-call first. Investigate the counterfactual concentration before any new model run.
