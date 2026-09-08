# M34.2 Ordering Repair Report

Ordering is semantic only when the question explicitly requests it or visible business/result authority defines it. The post-repair pilot has 3 ordered answerable cases and 17 unordered answerable cases. Unordered cases retain duplicate multiplicity while ignoring incidental SQL row order.

| Case | Ordering source | Contract |
|---|---|---|
| commerce_07 | QUESTION_EXPLICIT: descending net value, customer-ID tie-break | ordered |
| fleet_06 | QUESTION_EXPLICIT: highest event ID for timestamp ties, vehicle-ID display order | ordered |
| fleet_07 | QUESTION_EXPLICIT: descending efficiency, route-ID tie-break, top three | ordered |
| Other 17 answerable cases | NOT_APPLICABLE | unordered |

The validator rejects an ordered target without explicit visible provenance and rejects `row_order=true` when no target ordering exists.
