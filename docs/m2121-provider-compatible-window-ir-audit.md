# M2.12.1 — Provider-Compatible Structured Window IR Generation Audit

## Result

M2.12.1 did not establish a usable provider-to-compiler Window IR boundary.
It did establish that the historical M2.12 `0/48` Window IR arm cannot be
interpreted as semantic accuracy: its persisted artifact is missing the raw
response and provider error detail needed to locate the failure.

The new diagnostic run made 60 independent Luna calls. All 60 received a
provider response, so there were no observed HTTP/request-level failures. The
small JSON ladder parsed successfully through levels 0–4. The first material
break occurred at the richer flat DTO boundary: only 2/3 level-5 responses
validated as DTOs and 0/3 converted to the strict internal IR. Across the
12-question arms, plain JSON produced 1/12 complete transport successes,
flat DTO 0/12, and DSL 0/12.

This is evidence of a provider-facing representation/conformance bottleneck,
not evidence that the deterministic compiler failed and not evidence that
Luna’s Window semantic accuracy is 0%.

## Historical M2.12 classification

The persisted M2.12 development artifact contains 48 failed Window IR records,
but no raw assistant content, HTTP/provider error detail, response model,
usage, or request ID for those records. Therefore the only defensible
classification is:

| Classification | Count |
|---|---:|
| `UNDETERMINABLE_FROM_PERSISTED_ARTIFACT` | 48 |

`REQUEST_REJECTED_BEFORE_INFERENCE`: not determinable.

`INFERENCE_OCCURRED_BUT_OUTPUT_UNPARSED`: not determinable.

The new audit’s native reproduction is more informative: 3/3 calls had a
response model, token usage, request ID, and valid JSON content, but the JSON
did not validate as the strict internal `WindowQueryIR`. Those three are
classified as `SCHEMA_VALIDATION_FAILED`; they are not request-level
rejections.

## Internal and provider-facing models

The strict compiler-facing model remains unchanged:

```text
WindowQueryIR
  source_relation: string
  pattern: WindowPattern
  physical_outputs: tuple[string, ...]
  computations: tuple[WindowComputation, ...]
```

`WindowComputation` remains a discriminated union of the typed internal
specifications for latest-per-group, top-N-per-group, LAG, LEAD, running and
moving aggregates, ranking, and share-of-total. The internal model contains
no SQL field and remains the compiler authority.

The diagnostic provider DTO is deliberately flatter:

```text
ProviderWindowIRDTO
  pattern: LATEST_PER_GROUP | TOP_N_PER_GROUP | LAG | LEAD |
           RUNNING_AGGREGATE | MOVING_AGGREGATE | RANKING | SHARE_OF_TOTAL
  source_relation: string
  physical_outputs: tuple[string, ...]
  partition_by: tuple[string, ...] = ()
  order_by: tuple[ProviderOrder, ...] = ()
  tie_breakers: tuple[ProviderOrder, ...] = ()
  target: string | null = null
  offset: int | null = null
  n: int | null = null
  tie_policy: EXACT_N | INCLUDE_TIES | null = null
  aggregate: SUM | AVG | MIN | MAX | null = null
  ranking_function: ROW_NUMBER | RANK | DENSE_RANK | null = null
  frame_mode: string | null = null
  frame_start_kind: string | null = null
  frame_start_value: int | null = null
  frame_end_kind: string | null = null
  frame_end_value: int | null = null
  scale: float | null = null
  alias: string | null = null
```

It has `extra="forbid"`, bounded numeric fields, bounded enums, and alias
validation. It contains no table graph, SQL, SQL fragment, authorization
field, or generic expression escape hatch. A deterministic
`ProviderWindowIRAdapter` resolves it into the strict internal model and runs
the existing internal validation.

## Schema complexity inventory

Schema hashes are persisted in the run artifact:
`evaluation/results/m2121/20260903T132500Z/schema_hashes.json`.

| Schema | bytes | `oneOf` | `anyOf` | discriminator | nested union | union array | optional fields | `$defs` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `WindowQueryIR` | 7,774 | yes | yes | yes | yes | yes | 22 | 16 |
| `ProviderWindowIRDTO` | 3,119 | no | yes | no | no | no | 17 | 6 |
| ladder level 6 (`LAG | RANKING`) | 1,870 | yes | no | yes | yes | no | 2 | 2 |

The internal schema’s complexity comes from the discriminated computation
union inside the computations array. The flat DTO removes that union array,
but its many nullable operation-specific fields still create a broad
provider contract.

## Schema ladder

The ladder used three consumed M2.12 development questions per level:

| Level | Added feature | Calls | JSON/syntax parsed | DTO/schema parsed | Full transport |
|---:|---|---:|---:|---:|---:|
| 0 | enum-only pattern | 3 | 3/3 | n/a | n/a |
| 1 | source relation | 3 | 3/3 | n/a | n/a |
| 2 | output array | 3 | 3/3 | n/a | n/a |
| 3 | flat LAG | 3 | 3/3 | n/a | n/a |
| 4 | flat RANKING | 3 | 3/3 | n/a | n/a |
| 5 | flat `ProviderWindowIRDTO` | 3 | 3/3 | 2/3 | 0/3 |
| 6 | discriminated `LAG | RANKING` union | 3 | 3/3 | 3/3 | 1/3 |

The first syntactic success was level 0. The first failure after the
successful simple levels was level 5, where richer optional operation fields
and strict semantic/catalog conversion became necessary. Level 5 had already
failed before level 6, so the level-6 union is not identified as the first
compatibility break.

## Transport arms

| Arm | Calls | Provider accepted | Syntax parsed | Provider DTO validated | Adapter conversion | Internal IR validated | Transport success |
|---|---:|---:|---:|---:|---:|---:|---:|
| Current native strict IR | 3 | 3 | 3 JSON responses | 0 | 0 | 0 | 0 |
| Flat provider DTO | 12 | 12 | 12 | 5 | 0 | 0 | 0 |
| Plain JSON text | 12 | 12 | 12 | 6 | 1 | 1 | 1 |
| Textual Window DSL | 12 | 12 | 0 | 0 | 0 | 0 | 0 |

The native calls reached inference and returned JSON, but the local existing
parser expected `message.content` to encode the strict internal shape and the
content did not validate as that shape. No current native call yielded a
usable internal IR.

The existing Chat Completions adapter used JSON-object mode for these
diagnostic requests and described the candidate schema in the prompt; it did
not submit a provider-specific `json_schema` response format. Consequently,
the audit found no evidence of an HTTP rejection of a provider JSON Schema.
The observed boundary is output conformance to the requested representation,
not a proven pre-inference provider schema rejection.

The flat DTO and plain JSON calls were accepted as JSON transport, but most
responses used incomplete or semantically unresolvable fields. Typical
failures included unqualified columns, output aliases in physical-column
positions, missing target/alias fields, and operation-specific omissions.
Those are adapter/DTO conformance failures, not compiler failures.

The DSL prompt did not reliably produce the bounded grammar: none of the 12
responses parsed. The parser itself is deterministic and rejects unknown keys,
duplicates, malformed lists, invalid enums/integers, and SQL-looking values.

## Textual DSL grammar

The implemented grammar is line-oriented:

```text
WINDOW NEWLINE
(key="="value NEWLINE)+
```

Required keys are `pattern`, `source`, and `outputs`. Supported keys are:
`pattern`, `source`, `outputs`, `partition`, `order`, `tie_breakers`,
`target`, `offset`, `n`, `tie_policy`, `aggregate`, `ranking_function`,
`frame_mode`, `frame_start_kind`, `frame_start_value`, `frame_end_kind`,
`frame_end_value`, `scale`, and `alias`. Lists are comma-separated; ordering
values are `column:DIRECTION`. Keys must be unique and values cannot contain
SQL comment or statement markers. The parser produces the provider DTO; it
does not compile SQL.

## Semantic diagnostic

Only successfully converted representations were eligible for semantic
comparison. The persisted diagnostic contains two transported records. Across
those records:

| Field | Accuracy |
|---|---:|
| pattern | 100% |
| source relation | 100% |
| physical outputs | 50% |
| partition | 100% |
| order | 50% |
| N | 50% |
| function | 50% |
| alias | 0% |

This sample is too small for a semantic quality claim. It does show why
transport success must be established before returning to M2.12 capability
scoring.

## Cost and latency

The run made 60 calls total: 3 native, 21 ladder, 12 flat DTO, 12 plain JSON,
and 12 DSL. All 60 provider calls returned successfully; no retry or repair
was used.

| Arm | Input tokens | Output tokens | Avg latency | p50 | p95 |
|---|---:|---:|---:|---:|---:|
| Current native (3) | 2,997 | 289 | 2,227.0 ms | 2,187.5 ms | 2,187.5 ms |
| Flat DTO (12) | 23,787 | 1,453 | 1,940.2 ms | 1,994.1 ms | 2,079.4 ms |
| Plain JSON (12) | 23,775 | 1,540 | 2,109.3 ms | 2,048.6 ms | 2,251.2 ms |
| DSL (12) | 11,847 | 1,157 | 1,937.6 ms | 1,915.7 ms | 2,295.1 ms |

Ladder calls used an additional 21,749 input and 1,728 output tokens. Usage was
recorded only where the provider exposed it; no unavailable usage was
converted to a zero inference cost.

## Safety and scope confirmations

- No provider call used M2.12 or any other holdout.
- M2.12 holdout SHA `e2cf624233937374e9aeff91cbcae239b6e65151f6ecbb449126b7fc25339fc4` remains untouched.
- M2.7 and M2.11 holdouts remain untouched.
- No SQL fallback, retry, repair, second LLM call, candidate sampling, or
  hidden chain-of-thought request was used.
- Every future compiler-facing IR conversion is required to pass the existing
  internal validation and existing deterministic compiler; provider DTO/DSL
  is never authorization.
- M1, the evaluator, FULL_COMPACT, and the window compiler were not changed by
  this audit.

## Decision

Classification: **PROVIDER_SCHEMA_COMPLEXITY_BOTTLENECK** at the
provider-facing contract/conformance boundary. The evidence is not a request
HTTP rejection, and it is not a semantic Window capability score. The simplest
reliable syntactic boundary was plain JSON text, but only 1/12 diagnostic
records completed DTO-to-internal-IR transport.

Exactly one next experiment is recommended:

**M2.12R — Window IR Capability Rerun using a minimal pattern-specific plain
JSON provider DTO and the unchanged strict adapter/compiler.**

This should be a new, pre-registered experiment—not a change to this audit.
It is needed to determine whether reducing provider-facing operation-field
complexity yields enough valid internal IR to measure semantic Window quality.
M2.12’s frozen holdout should remain unconsumed until that rerun demonstrates
a reliable transport boundary.
