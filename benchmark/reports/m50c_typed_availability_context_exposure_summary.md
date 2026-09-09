# M50C — Typed Availability Primitive Context Exposure

## Scope

Live attempts: 180 (90 CONTROL, 90 TREATMENT); retries: 0. Provider/model calls after response freeze: 0.

## Contract

M50B snapshot corpus: `759d5056cf8edf8477bf1cae2d7028ab9e3da63c37d20bf6f7dfb0d7319dfb4f`.
Treatment exposes factual primitive inventories only; answerability and uniqueness remain uncomputed.

## Replay status

The zero-call replay exposed a post-response contract defect: M50C reused `m48b._prepare_state` without the frozen M48B.1 reader-role grant restoration after schema reset. The resulting runtime dispositions are invalid for scoring, so the paired runtime/evaluation outputs are not M50C evidence.

Responses are preserved in `m50c_control_responses.jsonl` and `m50c_treatment_responses.jsonl`; no harness patch or rerun was made.

## Final intervention verdict

`M50C_ABORTED_POST_RESPONSE_CONTRACT_DEFECT`.
