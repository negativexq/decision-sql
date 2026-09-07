# M24.1 — M1 Scope Hardening + Evidence Accounting Repair

## Why M24.1 exists

M24 expanded the general analytical SQL surface without provider calls. Two bounded issues remained: unqualified columns with multiple visible matches were resolved by selecting the first relation, and LiveSQLBench replay deltas were stored under a historical-regression field even though the BIRD/Defog corpus was not replayed.

## Ambiguous column semantics

M1 now fails closed for an unqualified column when more than one visible relation exposes that name. A single visible match still resolves normally, qualified references remain valid, and parent-scope correlated references are preserved. The existing `UNKNOWN_COLUMN` policy code is retained, with an explicit ambiguity message. Relation aliases used as row/composite values, such as `ROW_TO_JSON(alias)`, remain distinct from unqualified column references.

## What did not change

The function surface, provider, prompts, model, cost gate, read-only executor, SELECT-only boundary, and multi-statement rejection are unchanged. M1 remains the sole SQL safety authority. No benchmark-specific runtime rule was added.

## M24 → M24.1 replay

The frozen 180-case LiveSQLBench gold replay remains provider-free. M24 reported 166 accepted, 10 rejected, 4 planning/multi-statement, and 166 evaluator passes. M24.1 replays the same population after ambiguity hardening and records the exact result in the companion safe artifact. Aggregate status deltas are separated from historical regression evidence.

## Historical regression limitation

Historical BIRD/Defog provider-free SQL replay was **NOT RUN** because the required historical database roots were unavailable. The 774-case historical corpus remains an explicit limitation; LiveSQLBench recovery counts are not historical BIRD/Defog results.

## Pilot readiness

The protected LiveSQLBench merge, unchanged M1, read-only execution, and evaluator remain provider-free and auditable. The frozen 18-case pilot is preserved and its compatibility is recomputed, but no model pilot is started automatically. The infrastructure surface is ready for review before that pilot.
