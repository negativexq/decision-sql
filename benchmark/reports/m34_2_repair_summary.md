# M34.2 Repair Summary

The governed pilot is machine-ready for human review at version `0.1.1-pilot`. Human acceptance remains pending: `0/30` accepted.

| Gate | Result |
|---|---:|
| Cases / distribution | 30: 20 answerable, 5 authority-blocked, 3 ambiguous, 2 policy-blocked |
| Context / authority | 20/20 / 20/20 |
| Ordered / unordered answerables | 3 / 17 |
| Reference A/B agreement | 40/40 cases; 62/62 fixture comparisons |
| Counterfactual fixtures | 42; minimum 2 per answerable |
| Mutants | 61 executed, 61 killed, 0 survived, 0 invalid |
| Governance | blocked 5/5, ambiguous 3/3, policy 2/2 |
| Post-repair audit | 30 CLEAN, 0 REVIEW_REQUIRED, 0 CRITICAL_DEFECT |
| Provider calls | 0 |

The repaired system now records semantic provenance for population, filters, ordering, limits, temporal semantics, and rounding. Incidental SQL row order is ignored unless the question explicitly requests it. Historical M34/M34.1 audit evidence remains present and unchanged; full repository frozen-source failures remain documented as historical and are not part of the pilot machine gate.

Content hash: `f30f5bc061920c59f7a7b58e85d8fcb911ab7dbd83fb1505a2412c7043dba9a8`.
