# M34.2 Mutant Repair Report

The M34.1 weak/invalid active mutants were repaired without weakening the kill gate.

| Former mutant | M34.2 action |
|---|---|
| fleet_06/m16_no_tiebreak | Replaced by `m16_returns_all_events`, a valid wrong-population query. |
| support_06/m26_no_date | Replaced by `m26_medium_incident`, a valid wrong-severity query. |
| support_05/m25_global_avg | Replaced by `m25_all_accounts_baseline`, a plausible zero-ticket baseline interpretation. |

Active execution result: 61 authored, 61 executed, 61 killed, 0 survived, 0 invalid; kill rate 100%.
