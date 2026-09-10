# M56 — Single-Call Contract Improvement — Aborted

## Integrity finding

M56 was aborted after acquisition because the preregistered runner did not use the retained canonical provider-visible prompt. The runner read `benchmark/prompts/governed_context_v1.md` (SHA-256 `9b9a860022978cf3dd53d4d5762a3dd9df785e489ca7d8575143507e1c0138f0`), while `benchmark.m46b_contract.m43_prompt()` reconstructs the retained request-builder prompt from the M43 request ledger (SHA-256 `119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb`). The bytes differ.

This is a request-builder/provenance defect. The 120 selection responses and 90 full-run responses are preserved as raw evidence, but neither selection results nor full-run metrics are scientifically valid M56 evidence for the retained production contract. They must not be used as an official score or as a model/contract improvement claim.

## Accounting

- Provider/model attempts made: `210`
- Selection attempts: `120`
- Full-run attempts: `90`
- Retries: `0`
- Further calls after detection: `0`
- Benchmark semantics changed: `NO`
- Runtime semantics changed: `NO`
- Historical M54 artifacts changed: `NO`

## Preserved history

M54 remains the official benchmark baseline. Its metrics remain 82/90 expansion Governed Task Success, 57/62 expansion Answerable Runtime TSA, and 160/180 public Governed Task Success with 108/122 public Answerable Runtime TSA. The invalid M56 response ledgers are retained but are not admitted to official scoring.

## Required recovery

The next recovery milestone must reconstruct `BASE_PROMPT` through `benchmark.m46b_contract.m43_prompt()` before candidate construction, verify hash `119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb`, and preregister a new experiment. No further model call was made after the mismatch was identified.

## Verdict

`M56_ABORTED_PRELIVE_REQUEST_BUILDER_DRIFT`
