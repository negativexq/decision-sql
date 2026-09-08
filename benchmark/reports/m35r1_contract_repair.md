# M35R1 Provider-Safe Contract Repair

Offline only. No M35R1 provider call was made while freezing this contract.

## Historical M35 defect

- Raw provider body hash: `2b17c78c9966e64ad78657b536cddff5f650e2510708e94a40fd3bcde1d9d769`
- Exact provider body: `{
  "error": {
    "message": "Invalid schema for response_format 'decision_sql_m35_submission': In context=(), 'allOf' is not permitted.",
    "type": "invalid_request_error",
    "param": "response_format",
    "code": null
  }
}`
- Provider outcome: HTTP 400 before model generation.
- Historical M35 remains `M35_ABORTED_CONTRACT_DEFECT`.

## Repair

- Old provider schema contained `allOf` conditionals.
- New provider schema is a flat strict object with primitive types and enums only.
- Cross-field decision invariants remain enforced by the local validator after parsing.
- Benchmark semantics changed: NO.
- Benchmark content changed: NO.
- Model prompt semantics changed: NO.
- Model configuration changed: NO.

## Gates

- Requests: 30/30
- Exact Case ID/question/instructions/context: {'case_id': 30, 'instructions': 30, 'question': 30, 'serialized_context': 30}
- Answerable context sufficiency: 20/20
- Evaluator-only leakage: 0
- Provider schema unsupported keywords: 0
- Provider calls before M35R1: 0

## Frozen hashes

- Benchmark: `f30f5bc061920c59f7a7b58e85d8fcb911ab7dbd83fb1505a2412c7043dba9a8`
- Governance prompt: `9d63097b6cf110e545ab7b5b13ab6f763a0515002e394791b9b4a9264468bba7`
- Provider schema: `a20bcacd0f9221b53ccf0b8ab860981956fa7eef36bcfb507ffd79453c52b240`
- Case order: `6d31587c9726983492e022c1b731cfdc4e5f5e214dd240f289d417358621ed83`
- Serializer: `19b398badcb193c2b3c20f11697155b184113a956b4b79f4e22ff0b20f6b47f2`
- Evaluator: `ccc3175fde205630fe87717480e3e521be2cee8e6b4fb26c359448977d95e019`
- Validator: `930c3a1097059341509183401277d0aa7a7e0505012f61607342131325b9de54`
- Provider adapter: `d8cabbbd146b54a7fdbd1d6b3c20ffeb712d7e01613cff997cb25fe3fb4efed7`
- Provider config: `7d97392c16f194d96d2a63af34e7403f2b38229ecefbc447dffeab5c3aba9ccb`
- Experiment config: `f1b2f3617d7a9047864c4c22198a1c2d1dd09800ed2b37ed54df36a8bf841415`

M35R1 contract freeze: PASS
