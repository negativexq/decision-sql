# M47B — Prospective Grain Normalization Confirmation

## Historical preservation
M39–M47A artifacts were hashed before the run and were not modified.

## M47B design
Fresh provider calls: 90; model calls: 90; RAW and NORMALIZED share the same parsed submissions.

## Evaluation truth
`0.2.2-dev` / `3bb0c505c1c154d8f8f14ab7e903e190ca4d0ed3488f740f68f45674585aec0e`; prompt `119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb`.

## Frozen generation contract
Raw responses: `9bea788dce7e9199d154b6eecd2980af85e7ae94c41e90be8a7dbc1d0bdbe2e3`; parsed submissions: `efa7a016b39a913e070dfee0591994facb9ffbe43363527d4ba2504c5821ad66`.

## Frozen normalizer contract
M47A source hash `55ff3b32a698c8b8a151984dc8b9070531cfde926fb02148f6fc59f6c97b5800`; structured model context active: `NO`.

## Pre-live reference no-op
120/120 references unchanged; 184/184 fixture comparisons; 190/190 mutants killed.

## Fresh provider execution
90/90 genuine responses; retries: 0.

## Fresh RAW baseline
Governed `71/90`; Answerable `46/60`; conditional SQL `46/52`.

## Prospective normalization results
Raw fanout diagnostics: `1`; normalized: `0`; rewrites: `1`; abstentions: `0`.

## RAW vs NORMALIZED
Governed: `71/90 → 72/90`; Answerable TSA: `46/60 → 47/60`.

## Grain-sensitive family
The frozen M46A applicability family is reported in `grain_family_analysis.json`; no case IDs are used by the normalizer.

## Safe-SQL non-interference
Safe SQL changed: `0`; SUM(DISTINCT) repairs: `0`; unauthorized relationships: `0`.

## Determinism
Offline RAW/NORMALIZED replay was run twice with identical artifact hashes.

## Verdict
`PROSPECTIVE_GRAIN_NORMALIZATION_SUPPORTED` — one fresh supported fanout SQL was normalized correctly, with zero safe-SQL interference and zero normalization regressions.
