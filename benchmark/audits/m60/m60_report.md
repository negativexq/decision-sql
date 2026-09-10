# M60 — LiveSQLBench-Base-Lite SELECT-only applicability evaluation

## Verdict

`M60_ABORTED_EVALUATOR_INTEGRITY_FAILURE`

No provider/model calls were made. The official Base-Lite assets are present
and their frozen hashes match the retained provenance, but the provider-free
final preflight does not establish a complete interpretable evaluator path for
all 180 SELECT tasks. M60 therefore stops before request construction for live
acquisition and produces no external benchmark score.

## Repository and contract state

- HEAD before and after the audit: `920a1d2235e9a7912ca96fa58690fc4afa4296f9`.
- Candidate C remains the production contract:
  `3fb439e68cc158db6da5ea4bea31085d00776eb9573e5d743522922f74fb5587`.
- Retained canonical builder source hash: `ff695c5a4f9b26ffe9d88f30ee9c4a917c90e670e72b70a7b3739a9ed41b8a23`.
- Benchmark semantics, runtime semantics, and the single-call architecture
  were not changed.

## Official benchmark provenance

The local official public checkout is the pinned dataset commit
`507d7d98f477b9d6a990628ea021bd2501cfd6e2` with dataset SHA-256 `f2b2cd4402450f3bde1961e8f92f24100cc2ac6e908ef9a80b996d265c3a7884`. The protected local
GT/knowledge artifact has SHA-256 `eb3ddfff4371a5f7aeacefefbfc5ff039fbf5fec57ff69ae64c1b0378a3a94b2` and 270 rows. The official
evaluator source is pinned at repository commit `e15cd221267e06fabfaf6a3d4a69308280ce9a7c`. The
official PostgreSQL image digest is `sha256:1ae45d7aa5d64dd8eb82e4058f56b4b9625d5035b9b9dc0d2afa0295d9d3053c`.

The mechanically classified population is 270 tasks: 180 `Query`/SELECT and
90 `Management`. No SELECT task was manually excluded. Management tasks were
not sent to a model because the product is intentionally read-only.

## Provider-free integrity results

The existing protected preflight was rerun against the official local assets
without provider access. It confirmed a 270/270 public/protected merge, 180
SELECT tasks, 90 management tasks, 18 readable database catalogs, and 18/18
read-only probes. Runtime/evaluator separation also passed: gold SQL, test
cases, reference results, and gold labels were not included in the provider
payload constructor.

The final evaluator gate remains blocked:

| Check | Result |
| --- | ---: |
| SELECT reference rows | 180 |
| Unchanged M1 accepted | 175 |
| M1 policy rejection | 1 (`mental_5`, query-too-expensive) |
| M1 planning errors | 4 (`news_8`, `news_9`, `solar_6`, `solar_7`, multi-statement) |
| Reference execution success | 175/175 accepted |
| Official reference-evaluable | 175 |
| Official reference pass | 170 |
| Official reference fail | 5 (`insider_1`, `vaccine_10`, `vaccine_2`, `vaccine_7`, `virtual_2`) |
| Reference not evaluable | 5 |

Because five official SELECT references do not reach a complete accepted and
evaluable path under the unchanged M1/evaluator boundary, a full 180-task
external success rate would not be an interpretable official evaluation. This
is an evaluator/runtime compatibility blocker, not permission to weaken the
read-only policy, cost gate, parser, or execution boundary.

## Information boundary and adapter

The existing adapter is provider-free and deterministic. It maps official
schema metadata to a complete normalized PostgreSQL schema rendering, keeps
declared foreign keys explicit, and preserves official external knowledge as
untrusted annotations. The protected merge is separate from runtime payload
construction. No gold SQL, test cases, expected rows, reference results, or
evaluation labels enter the provider payload.

The adapter is not promoted to a live M60 request path because the final
official evaluator gate failed. No LiveSQLBench-specific prompt, semantics,
context enrichment, or generated SQL path was added.

## External score status

No SELECT provider requests were built or sent. Therefore:

- SELECT provider calls: `0`.
- Management model calls: `0`.
- Retries: `0`.
- External SELECT score: **not produced**.
- Official full Base-Lite/leaderboard score: **not produced**.

The internal stable Decision-SQL evidence remains 83/90 Governed and 59/62
Answerable Runtime TSA. It is not compared numerically with a nonexistent
external score, and the two constructs would not be identical even if both
scores were available.

## Scope and next action

Management SQL remains `OUT_OF_PRODUCT_SCOPE_READ_ONLY_SYSTEM`; it was not
treated as SELECT failure and received zero model calls. A separate,
explicitly reviewed infrastructure/evaluator compatibility milestone is
required before M60 can begin live SELECT acquisition. This M60 audit does not
change Candidate C, benchmark semantics, runtime safety, or the README.
