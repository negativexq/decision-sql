# M5R.1R.1 — R1 Structured-Output Failure Forensics

Status: forensic/provider-boundary diagnostic only.

## Conclusion

The historical R1 DEV run remains at 98% protocol compliance: 100 calls, 98
valid DTOs, and two `MalformedProviderResponse` records. The failed case IDs
are `m5r1-group-025-abstain` and `m5r1-group-043-answer`.

The historical artifact is not sufficient to identify the exact failure stage.
It contains only the case ID, status, and exception type for each failure; it
does not contain the request body, response envelope, provider request ID,
finish state, or validation error details.

A bounded forensic reproduction was therefore run, without benchmark scoring:
the two failed inputs and three successful controls were called once, then the
two historical failures were called once more. All seven calls used the
frozen R1 request path, response-schema hash, model, reasoning setting, and
context renderer. All seven returned HTTP 200, `finish_reason=stop`,
structured content, and a valid R1 DTO. The historical failures did not
reproduce on either repetition.

This supports an intermittent/provider-or-unresolved-edge characterization,
but does not prove what the historical response contained. It is not enough
to assert a provider strict-schema violation or to justify a semantic-quality
rerun. The primary classification is therefore:

`R1_INTERMITTENT_PROVIDER_PROTOCOL_FAILURE` — strongly supported as an
intermittent characterization, with the exact historical payload root cause
unresolved.

`R1_FINAL_FROZEN_RERUN_JUSTIFIED`: **no**. A future rerun would require a
separately approved protocol decision that establishes reliable handling of
the historical failure class without retries or permissive coercion.

## Frozen identities and request comparison

The benchmark was not loaded for scoring or changed:

| Item | Value |
| --- | --- |
| Corpus | `066d036596e54a9effe319f4cf58761a0da5b6572f20ac82cbaec7b8ae9ec767` |
| DEV | `301e001e1207b646d3469aab7607bdbff4db501b1c09df1fb329cecfbd6089c9` |
| HOLDOUT | `087cca9818f9654c72bc0abd42c153c41152fba8ca82bec498d5944046a318ad` |
| R1 DTO | `m5r1-r1-decision-v1` |
| Provider response schema | `6ebc494d7897182fd2744e79441aa2e12fa12125fae49bba9b7b7d85f3e0e800` |
| R1 semantic prompt | `db21af3e6065c9610ab4a0a635bb283aea81f8567283f5b3fa7f8f79578cd063` |
| Model | `gpt-5.6-luna` |
| Reasoning | `none` |
| Temperature | omitted/provider default |
| Transport | `httpx 0.28.1`; no OpenAI SDK |

Every forensic request was built by the same request helper and recorded the
expected schema hash and `strict=true`. The two historical failures and all
three controls therefore have the same request contract in the reproducible
path. The runner was serial; there was no concurrent request collection or
shared mutable request object.

The provider schema and local DTO had the same fields, enums, and
`additionalProperties=false`. The audit also found a local strictness drift:
the provider schema required `resolved_semantic_ids`, while the local DTO had
previously supplied a default. The DTO was tightened to require that already
contracted field, and deterministic parser tests now reject missing fields,
unknown fields, and invalid enum values. This is a semantic-neutral boundary
correction, but the absent historical payload means it cannot be attributed as
the cause of the two recorded failures.

The resulting local DTO schema hash is:

`d2d5d0b4570dccf6c7105830b9484ba367b834ab83534f756fe2c21d26a1f0f2`

The provider wire schema hash remains the frozen
`6ebc494d7897182fd2744e79441aa2e12fa12125fae49bba9b7b7d85f3e0e800`.

## Envelope and parser findings

The historical records do not preserve sanitized envelopes, so the historical
HTTP status, response status, refusal state, incomplete state, finish reason,
raw shape, and exact local validation error are unavailable.

The seven forensic responses had the same bounded shape pattern: an object
with `choices`, `created`, `id`, `model`, `object`, `service_tier`,
`system_fingerprint`, and `usage`; one choice; a message containing
`annotations`, `content`, `refusal`, and `role`; string content; no refusal;
and `finish_reason=stop`. No response contained a refusal, incomplete state,
JSON decoding error, extra field, missing field, or invalid enum in the
reproduction. No response-extraction failure was observed.

The current forensic artifact stores only bounded shape summaries and token /
latency metadata. It does not persist raw content, questions, credentials,
authorization headers, or full response bodies.

The seven calls used 848–851 input tokens and 34–56 output tokens. No obvious
length or token anomaly was observed; the sample is too small for a stronger
correlation claim.

The provider adapter performs one application HTTP POST per invocation. No
application retry, parser retry, coercion, enum mapping, fallback action
parsing, or JSON repair was added. Lower-level connection retry behavior is
not independently instrumented by the existing adapter.

## Protocol outcome model

The reproduction distinguishes typed success from malformed content at the
local boundary and records refusal/incomplete indicators when present. A
future protocol revision should explicitly distinguish transport outcomes such
as `SUCCESS_TYPED`, `REFUSAL`, `INCOMPLETE`, `TRANSPORT_ERROR`,
`SCHEMA_VIOLATION`, and `LOCAL_PARSE_ERROR` from the semantic actions
`ANSWER`, `CLARIFY`, and `ABSTAIN`. This milestone does not change the frozen
99% gate or retroactively rescore the historical run.

## Invariants

- No R1 DEV benchmark rerun and no HOLDOUT consumption.
- No R2 or R0 work.
- No SQL generation, repair, embeddings, reranker, or judge calls.
- M1, M3, M4, and the M5 verifier were not changed.
- Benchmark content, splits, and frozen hashes remain unchanged.
- The original M5R.1R result remains `M5R1R_PROTOCOL_UNRELIABLE`.
- No production selective-answering runtime was added.

## Artifacts and decision

The ignored forensic run is under
`evaluation/results/m5r1r1/20260903T233619Z-forensics/`. It records seven
provider calls, request/schema identity checks, bounded response-shape
summaries, local/provider schema comparison, and the no-rerun decision.

The semantic-neutral DTO strictness correction is covered by focused tests.
It does not establish that the historical failures are fixed, because their
raw provider payloads were not persisted. Accordingly, the correct next step
is not another quality run in this milestone.
