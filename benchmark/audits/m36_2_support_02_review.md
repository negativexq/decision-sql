# M36.2 support_02 independent review

Outcome: `REFERENCES_WRONG`

The review began with the question and visible authority, not with Luna's SQL. The
visible `support_sla` rule now states that the first `agent_response` considered is
one after `opened_at`, and that a response later than `opened_at + plan SLA` is a
breach. The question independently names the first response after the ticket was
opened and the most recently started subscription. The visible `event_ticket`,
`subscription_account`, `subscription_plan`, and `starts_on` authority is complete.

The seeded database contains agent-response rows before their ticket's `opened_at`
(for example ticket 2 has an event on 2026-01-02 before opening on 2026-01-03).
Therefore an unrestricted `MIN(event_at)` is not the first valid response under the
visible rule. Both frozen references used unrestricted `MIN(event_at)`, so their
agreement was a shared reference defect, not independent confirmation of the rule.

The repaired A/B references select the minimum response after `opened_at`, retain
the strict SLA boundary, and retain most-recent subscription selection. Existing
fixtures already distinguish exact-boundary, just-after, unanswered, and stricter
later-plan behavior; no fixture SQL was changed. Existing SLA mutants were updated
to remain plausible counterfactuals under the repaired rule.

This is not a repair to agree with Luna. The visible rule and data semantics were
resolved first; the references were then made consistent with that independent
resolution.
