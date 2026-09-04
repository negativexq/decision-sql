# M10.4 — Fresh Provenance-Complete Diagnostic Run

Status: `M104_RUN_INVALID`

Run identity: `decisionsql-m104-provenance-diagnostic-v1`

Checkpoint: `960c1cd37b487e47f9323d81fcbd1f2b538a21b8`

## Scope and freeze

M10.4 used one current-architecture arm, with the M10.3 diagnostic sink
enabled, on a 160-case candidate corpus intended as fresh. The corpus
contained 40 expected-governed cases
and 120 expected-direct cases, partitioned deterministically into DIAG_A and
DIAG_B (80 each). Each direct diagnostic family contributed ten cases; each of
the ten governed metrics contributed four cases.

The protocol, causal taxonomy, evidence rules, semantic phenotypes, memory
transfer rules, selection gate, provider protocol, and execution order were
frozen before provider execution. Reference validation succeeded for all
160/160 cases before the first architecture call. No draft candidate was
inspected during corpus construction.

M10.4 did not modify M4, M3, routing, prompts, provider configuration,
compiler semantics, M1, evaluators, or binders. It did not run a memory-off
counterfactual, a judge, repair, sampling, or post-hoc model call.

## Fresh evaluation

Evaluator V1 remained authoritative:

| Slice | Correct |
| --- | ---: |
| Overall | 33/160 |
| DIAG_A | 17/80 |
| DIAG_B | 16/80 |
| Expected governed | 4/40 |
| Expected direct | 29/120 |
| Actual governed | 4/43 |
| Actual direct | 29/117 |

The V1 output is a raw execution result from the exposed candidate corpus, but
it is invalidated as fresh diagnostic evidence by the canonical freshness
failure below. It is not a regression comparison with M7 or M10R and does not
create an accepted M10.4 or intervention accuracy.

M10R remains consumed historical evidence at 28/200 (14%). M7 remains a
separate historical workload at 99/150 (66%).

## Provenance and pipeline

Runtime provenance was 160/160 complete (100%). The ledger had zero parse,
ordering, payload-hash, gold-leakage, secret, and raw-result violations.
The ledger is append-only typed JSONL and is separate from post-run labels.

The run made 160 logical grounding calls and 117 logical direct-generation
calls, 277 total logical provider calls, within the 320-call budget. There were
two provider failures, eight M1 rejections, and no execution failures. No DB
mutations occurred. V2 was evaluated for 40 cases and unavailable for 120;
V2 did not overwrite V1 or alter the denominator.

Routing was correct on 157/160 cases: 40/40 governed recall, 40/43 governed
precision, three false-governed direct cases, and zero false-direct governed
cases.

Memory retrieval was attempted on 105 cases, with three selected entries per
retrieval. There were 29 correct and 76 incorrect memory-use cases. Selected
memory incompatibility and motif-transfer evidence are observational
diagnostics; memory use plus an incorrect result alone is not treated as
causal harm. No no-memory counterfactual was run.

## Causal attribution attempt (invalidated)

The 127 failures received exactly one primary mechanism. Counts were:

| Primary mechanism | Count |
| --- | ---: |
| Memory retrieval selectivity failure | 68 |
| Unresolved | 37 |
| M1 rejection | 8 |
| Memory overtransfer evidence | 8 |
| Routing overreach | 3 |
| Transport failure | 2 |
| Downstream generation failure | 1 |

Evidence grades were E1=13, E2=69, E3=8, E4=0, E5=37. E1+E2 coverage was
82/127. The memory-selectivity mechanism had 68 E1/E2 failures (53.54% of
all failures), with 37 in DIAG_A and 31 in DIAG_B. It therefore met the
precommitted gates of at least 12 failures, at least 20% of failures, both
partitions, at least five failures in each partition, and a coherent bounded
decision boundary.

The apparent automated table would have made memory selectivity pass the
precommitted gates, but it is not admissible evidence. A post-run canonical
freshness audit found seven references that canonicalize to prior internal
reference SQL. The initial gate compared trimmed raw SQL and therefore failed
to enforce the required canonical-SQL freshness rule. Because provider
execution had already begun, the exposed corpus cannot be repaired or rerun
under this milestone. No M11 mechanism is selected.

Observational provenance also would not prove that the model would have
succeeded with memory disabled; that requires a separately precommitted
counterfactual experiment.

## Artifacts and hashes

Bounded artifacts are under the M10.4 result directory and the provider-blind
protocol fixtures. The final summary file hash is
`4b37dc70f768247608dbf98d504820124a900615a73fef70ce9362df479d03f7`.
The runtime ledger hash is
`0f4cac8bc885aae84f7c9a3b572bee70ed0429e26f92191b4020c10ea5b3bdf6`.
The automated attribution hash is
`375c2508ffc11d8665af43432d593218c8319ca96bffe9b634e3cab1ee916e26`.

The pre-run master lock was
`94ff1675f253cc99cd3135459215d60488a9a9ab8c97db206a60ebb9cb0c6e7d`.

The protocol-integrity failure artifact is
`e2c86bccf62e7ae91623cc4fe37fc4ebaaee40a336a00e5c325be83b0ce8d45c` and
lists the seven overlapping case IDs. The invalidated selection-decision
artifact hash is
`86b40f5b8b4acedb273ad607edfba04675a6c53d05c9e0cf7c903d802bf35c82`.
The runtime ledger and run outputs are retained as invalidated research
artifacts; they are not used to select an intervention.

## Next milestone

`M10.4R — Fresh Provenance-Complete Diagnostic Run Repair Audit` is required
before another fresh diagnostic run. The repair must canonicalize freshness
checks before a new corpus is constructed. Public Defog and BIRD reruns remain
deferred.
