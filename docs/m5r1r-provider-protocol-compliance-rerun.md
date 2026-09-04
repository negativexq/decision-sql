# M5R.1R — Frozen Provider-Protocol Compliance Re-run

## Decision

The protocol correction succeeded for the R2 smoke and R2 DEV responses, but
the full experiment did not produce an eligible arm. R1 protocol compliance
was 98/100, below the precommitted 99% minimum. R2 was protocol-compliant at
100/100, but its frozen semantic results failed the original DEV gates. The
HOLDOUT was therefore not consumed.

Final classification: `M5R1R_PROTOCOL_UNRELIABLE`.

This is a protocol-correction result, not evidence to tune the benchmark,
prompts, model, R0 logic, or R2 semantic validator.

## Frozen inputs

The M5R.1 benchmark was not changed:

| Item | Value |
| --- | --- |
| corpus | `decisionsql-selective-answering` / `m5r1-selective-answering-v1` |
| corpus hash | `066d036596e54a9effe319f4cf58761a0da5b6572f20ac82cbaec7b8ae9ec767` |
| DEV hash | `301e001e1207b646d3469aab7607bdbff4db501b1c09df1fb329cecfbd6089c9` |
| HOLDOUT hash | `087cca9818f9654c72bc0abd42c153c41152fba8ca82bec498d5944046a318ad` |
| R0 | unchanged; historical DEV gate failure retained |
| model | `gpt-5.6-luna` |
| reasoning | `none` |
| temperature | omitted/provider default |

The original invalid M5R.1 calls remain historical: one R1 and one R2. They
are not mixed into rerun quality metrics.

## Protocol-only correction

The previous boundary sent `response_format: {"type": "json_object"}` and
then permissively parsed the resulting JSON. The correction sends native
strict JSON Schema response formats through the existing `httpx`-based
OpenAI-compatible adapter:

```text
response_format.type = json_schema
response_format.json_schema.strict = true
additionalProperties = false
```

R1 schema hash:
`6ebc494d7897182fd2744e79441aa2e12fa12125fae49bba9b7b7d85f3e0e800`.

R2 schema hash:
`d93fc828be70cf7564ac0e0e39ee74b0b12fddbc44cdc61f8f63104460f9b019`.

The semantic prompt hashes did not change:

- R1 before/after:
  `db21af3e6065c9610ab4a0a635bb283aea81f8567283f5b3fa7f8f79578cd063`
- R2 before/after:
  `39ad1d9b9c6afe47016a65ca12328a84c915dda5f4980761dbd41d50ac3f805a`

Change classification: `PROTOCOL_ONLY`. No examples, business rules,
thresholds, semantic guidance, or server context were added. The old
`action`/singular `interpretation`/`canonical` response is still rejected;
there is no semantic coercion.

The repository has no OpenAI SDK dependency. The relevant transport dependency
is `httpx 0.28.1`; dependency versions were not changed.

## Smoke and DEV accounting

Protocol smoke ran exactly once per arm:

- R1: 1 call, strict DTO parse passed, enum passed, no SQL.
- R2: 1 call, strict DTO parse passed, validator consumed the DTO, no action
  field, no SQL.

The smoke gate passed. DEV then ran exactly 100 R1 and 100 R2 calls, without
retries. R1 had 98 valid DTOs and 2 DTO failures; R2 had 100 valid DTOs and no
failures. Both arms had zero transport failures. R1’s 98% compliance failed
the required 99% protocol gate, so R1 semantic quality was not scored.

R2 was protocol-valid, but its action result was 35/100. It had 9.09% unsafe
answer rate, 50% ANSWER precision, 100% CLARIFY precision, 32.18% ABSTAIN
precision, 82.35% unnecessary intervention, and 12% answer coverage. Its
governed-default false intervention and M4-complexity false intervention were
both 100%. It did not pass the semantic DEV gate.

R2 interpretation diagnostics were: mean proposed interpretations 0.86,
14 valid interpretations, 72 invalid interpretations, 0 canonical dedups,
87 zero-valid cases, 12 one-valid cases, and 1 two-plus-valid case. Exact
benchmark-key interpretation recall was 0%; interpretation precision was
16.28%. These are diagnostics only and do not justify semantic tuning in this
milestone.

R0 remains the frozen historical result: 73/100 accuracy, 37.88% unsafe
answer rate, 56.14% ANSWER precision, 90.48% CLARIFY precision, 100% ABSTAIN
precision, 5.88% unnecessary intervention, and 57% coverage.

## HOLDOUT and invariants

HOLDOUT was not consumed because no provider arm passed both protocol and DEV
semantic gates. There were zero SQL-generation calls, zero repair calls, zero
LLM-judge calls, zero embedding/reranker calls, and zero M1 calls.

M1, M3, M4, and the M5 verifier were unchanged. Defog, BIRD, PRACTIQ, M3,
M4, M5.0, and M5.0.1 were not rerun. No production runtime or public action
state was added. The default architecture remains unchanged.

The rerun artifacts are in the ignored local directory
`evaluation/results/m5r1r/20260903T231846Z-dev/`; the historical invalid run
was not overwritten.

## Checks

The protocol tests, focused M5R.1 tests, full pytest suite, Ruff, mypy, and
`git diff --check` passed. Full pytest result: 286 passed, 3 intentionally
skipped integration tests. No stale `decisionsql-api-run-*` containers remain.

No commit or push was performed.
