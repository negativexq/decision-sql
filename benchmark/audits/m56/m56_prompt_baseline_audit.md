# M56 — Current Prompt Baseline Audit

Prompt hash: `9b9a860022978cf3dd53d4d5762a3dd9df785e489ca7d8575143507e1c0138f0`

This audit was generated before any M56 candidate response. The production prompt was not edited in this phase.

## Existing contract findings

| Causal group | Existing instruction | Assessment | Risk |
| --- | --- | --- | --- |
| `ANSWERABILITY_CALIBRATION` | Clarify only when materially different interpretations remain unresolved. | `PRESENT_BUT_TOO_OPEN_TO_OVERDETECTION` | No required pre-clarification unresolved-variable check; complex explicit contracts can be treated as ambiguous. |
| `GOVERNANCE_DECISION_HIERARCHY` | Use authorized relationships and block absent relationships. | `PRESENT_BUT_NOT_EXPLICITLY_ORDERED` | The prompt does not explicitly separate clear-but-unauthorized intent from unresolved meaning, nor explicitly prohibit business synonym inference from field names. |
| `GOVERNED_SQL_TRANSLATION` | Parent/child additive alignment plus use visible business/temporal rules. | `PARTIAL` | The fanout rule is scoped to parent-plus-child additive combinations and does not directly state existence/semi-join preservation; duration and required predicate translation are not stated as a final SQL checklist. |

## Ordering and overlap

- The clarification rule and answerability definition are compatible, but lack a required unresolved-variable test.
- The parent/child rule explicitly excludes existence tests; it does not contradict safe SQL, but leaves parent-measure filtering by a non-unique child under-specified.

## Evidence boundary

The baseline conclusions use only the current provider-visible prompt and M55's frozen forensic findings. No candidate or new model response was inspected.
