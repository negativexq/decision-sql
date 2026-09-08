# M34.2 Temporal and Filter Repair Report

The repaired targets expose every temporal/filter fact needed by their references and remove hidden predicates.

| Case | Repair | Distinguishing evidence |
|---|---|---|
| commerce_06 | Two-decimal average is stated; unrelated net metric removed | wording and target provenance agree |
| fleet_02 | Fuel is grouped by authorized vehicle home depot | fuel rows whose purchase depot differs no longer create an unauthorized join |
| fleet_04 | Maintenance ID/time attributes are visible | lower-inclusive/upper-exclusive window is executable from context |
| support_02 | Most-recent subscription policy and `starts_on` are visible | later stricter plan fixture changes the result |
| support_06 | Hidden date and subscription predicates removed | old high-severity incidents are governed by severity alone |

Machine result: context 20/20, reference A/B agreement 62/62 fixture comparisons, and no temporal/filter repair failures.
