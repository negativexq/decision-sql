# M36.2 — Projection policy repair and benchmark contract re-freeze

## Outcome

The repaired benchmark is `0.1.2-pilot`. The public policy is
`CLARIFY_STRICT_PROJECTION`: exact final projection remains strict, but every
ANSWERABLE question now names its final fields and order. Provider calls: **0**.

Old benchmark content hash: `f30f5bc061920c59f7a7b58e85d8fcb911ab7dbd83fb1505a2412c7043dba9a8`  
New benchmark content hash: `d9e42b6b0b417a8b0f605867ab3c67cc15e885123a0db87d56a444725dcda5ef`

## Gates

| Gate | Result |
|---|---:|
| ANSWERABLE projection contracts visibly justified | 20/20 |
| Hidden exact-projection requirements | 0 |
| Unresolved projection ambiguities | 0 |
| Context sufficiency | 20/20 |
| Authority completeness | 20/20 |
| Reference A/B cases | 40/40 |
| Fixture agreement comparisons | 62/62 |
| Mutants killed | 68/68 |
| Invalid mutants | 0 |
| Post-repair adversarial audit | 30/30 CLEAN |
| Provider calls | 0 |

Public model-facing strict-projection rule: **True**.  
Case-specific evaluator projection data in requests: **False**.

## Semantic and projection scope

All 20 answerable targets now carry evaluator-only exact projection metadata with
field role and visible provenance. The metadata is not serialized into requests.
The seven M35R1 projection cases remain historical evidence and were not rescored;
under the repaired questions and public policy, their former extra-column outputs
would be clearly non-compliant retrospectively. This does not alter the official
M35R1 score.

## support_02

Independent outcome: **REFERENCES_WRONG**. The visible SLA rule and repaired
question define the first response after `opened_at`; seeded data contains
pre-opening response events. Both prior references used unrestricted `MIN` and
therefore shared the same semantic omission. A and B were repaired to filter
responses after opening. The fixtures were sufficient and unchanged. This was
not a repair to agree with Luna.

## Historical boundary

M35R1 remains the v0.1.1-pilot baseline at 12/20 ANSWERABLE and 22/30 governed
success. Its artifacts and score were not rewritten. M36.2 is an offline
benchmark-contract repair, not a model evaluation.
