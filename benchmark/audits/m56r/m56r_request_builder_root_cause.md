# M56R request-builder root cause

## Canonical path

Production benchmark requests are built by `benchmark.m51b_runner._requests`, which delegates to `_provider_request`, serializes context with `serialize_governed_context_v1`, and passes `instructions` and `user_text` to `OpenAICompatibleProvider.complete_json_schema`.

## Aborted path

The aborted M56 runner read `benchmark/prompts/governed_context_v1.md` directly and rebuilt the request text in `benchmark/m56_runner.py`. It did not obtain the prompt through the frozen M43 request ledger/canonical builder.

## First divergence

The first provider-visible divergence was `messages[0].content` (the system prompt). The canonical ledger prompt is SHA-256 `119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb` and `4146` bytes; the directly read prompt file is SHA-256 `9b9a860022978cf3dd53d4d5762a3dd9df785e489ca7d8575143507e1c0138f0` and `5487` bytes. The file contains an additional parent/child measure section that is absent from the retained M43 prompt.

No model context, response schema, or provider transport was needed to explain the first divergence; the system message differed before the request reached the provider.

## Why existing tests missed it

Historical tests separately checked prompt-file content and frozen ledger hashes, but no test compared the complete provider-visible production payload with the experiment CONTROL payload. M56 therefore passed its own internal prompt hash while bypassing the canonical request source.

## Repair strategy

`_provider_request` is now the single request construction helper used by production `_requests` and M56R. CONTROL calls `_requests`; treatment arms call the same helper with an explicit contract-only prompt override. M56R fingerprints the complete provider-visible payload, including model, ordered messages, response schema, and temperature. Timeout, request IDs, trace IDs, timestamps, and local paths are transport/local metadata and are excluded because they are not in the provider body.
