# M34.1 Human Benchmark Audit Summary

This report audits the original Decision-SQL Bench v0.1 pilot without model calls, provider calls, prompt optimization, or source-case edits.

## Checkpoint and scope

- Benchmark commit: `10e014f` (`feat: add original governed benchmark pilot`)
- Audit parent: `10e014f`
- Historical preservation checkpoint: `37c86bf`
- Working tree before audit: clean, `main...origin/main`
- Cases audited: all 30, exactly once
- Provider calls: 0
- Model evaluation: not run
- Pilot source files modified: no

## Human recommendation outcome

| Status | Count |
|---|---:|
| ACCEPT | 8 |
| REVISE | 21 |
| REJECT | 1 |
| Total | 30 |

Machine validation reported 30/30. Human review recommends only 8 cases for current use, 21 repairs, and 1 replacement. `HUMAN_ACCEPTED` was not set.

## Critical defects, ranked

Counts are affected cases and can overlap.

| Defect | Affected cases |
|---|---:|
| Result contract makes unrequested row order semantic | 19 |
| Semantic target not uniquely justified by question/context | 5 |
| Hidden required knowledge | 5 total; 4 ANSWERABLE |
| Reference semantic error despite A/B agreement | 2 |
| Invalid semantic mutant | 2 |
| Unauthorized reference join | 1 |
| Metric/output precision underspecified | 1 |
| Non-discriminating or incomplete counterfactual axis | 4 |
| Artificial authority block | 2 |

The largest issue is not SQL execution: 19 answerable cases force an order that their questions never request. Other material defects include `fleet_02` using an unlisted direct depot join, `fleet_04` hiding maintenance columns, `support_02` leaving subscription selection undefined, `support_05` silently choosing a matching-only baseline, and `support_06` adding a hidden date and subscription predicate.

## All rejected cases

### support_08

Question: “Join ticket requester email to incident code and list incident severities.”

Rejected because email-to-incident-code is not a credible analyst linkage and does not resemble a realistic data-governance decision. It was intended to test fail-closed behavior for an unauthorized physical-looking join. Replace it with two plausible external identifiers that happen to align physically but lack an authorized relationship.

## All revise cases

| Case | Exact required repair |
|---|---|
| commerce_01 | Remove hidden customer_id ordering or state it in the question. |
| commerce_02 | Remove hidden order_id ordering unless ranked output is intended. |
| commerce_03 | Remove hidden region ordering or state it. |
| commerce_04 | Remove hidden ordering; add captured-price/current-price and rounding fixtures; naturalize wording. |
| commerce_05 | Remove hidden ordering; simplify repeated product/order wording. |
| commerce_06 | Define or remove two-decimal rounding; remove unrelated metric dependency; remove hidden ordering. |
| commerce_07 | State tie-break and ranked output order, or make them non-semantic. |
| fleet_01 | Remove hidden event ordering or state it; consider naturalizing “documented.” |
| fleet_02 | Expose fuel attributes; repair/add depot authority; disambiguate depot meaning; add a distinguishing fixture; remove hidden order. |
| fleet_03 | Remove hidden route ordering; naturalize wording. |
| fleet_04 | Expose maintenance attributes; remove hidden vehicle ordering. |
| fleet_05 | Remove hidden driver ordering or state it. |
| fleet_06 | Resolve vehicle population and event tie policy; replace invalid mutant; separate display order from latest-row selection. |
| fleet_08 | Expose weather route_code and correct the denied relationship endpoint. |
| fleet_10 | Expose maintenance time metadata and make both ambiguity interpretations visible, or define one. |
| support_01 | Remove hidden account ordering or state it. |
| support_02 | Define subscription selection/validity; expose starts_on; align references; add multi-plan fixture; remove hidden order. |
| support_03 | Remove hidden ordering; remove false relationship tag; naturalize wording. |
| support_04 | Remove hidden ticket ordering or state it. |
| support_05 | Define whether zero-ticket accounts enter the baseline; replace weak mutant; remove false tags and hidden ordering. |
| support_06 | Resolve hidden date and subscription predicates; expose incident fields; repair target/references/mutant; remove hidden ordering. |

## Accepted cases

- `commerce_08`: explicit, plausible external-code authority trap.
- `commerce_09`: genuine recent-signup versus recent-activity ambiguity.
- `commerce_10`: realistic read-only policy block.
- `fleet_07`: fully stated top-three ranking and tie behavior.
- `fleet_09`: realistic legacy device-code authority trap.
- `support_07`: realistic requester/contact authority trap.
- `support_09`: genuine active-status versus expiry ambiguity; nullable expiry remains a minor limitation.
- `support_10`: realistic read-only policy block.

## Coverage audit

| Mechanism | Declared | Meaningfully verified | Weak/questionable |
|---|---:|---:|---:|
| schema_linking | 2 | 2 | 0 |
| relationship | 21 | 18 | 3 |
| multi_hop | 5 | 5 | 0 |
| population | 10 | 9 | 1 |
| filter_scope | 1 | 1 | 0 |
| aggregation | 14 | 14 | 0 |
| grain | 1 | 1 | 0 |
| calculation | 6 | 6 | 0 |
| temporal | 8 | 7 | 1 |
| json | 3 | 3 | 0 |
| window | 3 | 2 | 1 |
| nested | 4 | 4 | 0 |
| correlated | 2 | 0 | 2 |
| set_operation | 2 | 1 | 1 |
| ordering | 3 | 2 | 1 |
| null_semantics | 5 | 5 | 0 |
| precision | 5 | 4 | 1 |

The false/weak declared coverage is concentrated in `support_05` (`relationship`, `correlated`), duplicated `window` tagging in `fleet_07`, and `set_operation` on `commerce_05`, where distinct counting is not a set operation in the usual sense.

## Reference quality

Reference diversity across the 20 answerable cases:

| Diversity | Count |
|---|---:|
| HIGH | 12 |
| MEDIUM | 2 |
| LOW | 6 |

The two references often use structurally different CTE/subquery forms, but execution agreement did not protect against shared authoring mistakes. Reference defects were found in `fleet_02` (unlisted direct depot join), `support_02` (different subscription-selection behavior in A and B), and `support_06` (hidden date and subscription constraints).

## Counterfactual quality

Case-level quality for answerable cases:

| Quality | Cases |
|---|---:|
| STRONG | 8 |
| ADEQUATE | 10 |
| WEAK | 2 |

Weak suites: `fleet_02` does not distinguish purchase depot from vehicle home depot; `support_06` tests a hidden date rule rather than a stated requirement. `commerce_04` and `support_02` also have material coverage gaps even though their overall suites are adequate.

## Mutant quality

All 60 mutants were manually classified independently of kill status.

| Quality | Mutants |
|---|---:|
| STRONG | 26 |
| USEFUL | 31 |
| WEAK | 1 |
| INVALID | 2 |

Weak mutant: `support_05/m25_global_avg` uses average `ticket_id`, not a plausible competing average-count population. Invalid mutants: `fleet_06/m16_no_tiebreak` because the question does not state an event-id tie-break, and `support_06/m26_no_date` because the question does not state an incident date filter. Therefore 60/60 killed overstates mutant quality.

## Authority and ambiguity quality

One answerable case (`fleet_02`) uses an unauthorized/unlisted reference join. Four answerable cases rely on hidden required knowledge: `fleet_02`, `fleet_04`, `support_02`, and `support_06`. No internal target leakage was found in the model-visible case records or rendered authority context.

| Case type | Case | Assessment |
|---|---|---|
| AMBIGUOUS | commerce_09 | Genuinely ambiguous; ACCEPT |
| AMBIGUOUS | fleet_10 | Weakly grounded because maintenance fields are hidden; REVISE |
| AMBIGUOUS | support_09 | Genuinely ambiguous, with nullable-expiry limitation; ACCEPT |

Authority-blocked quality is MIXED: `commerce_08`, `fleet_09`, and `support_07` are strong; `fleet_08` is internally inconsistent; `support_08` is artificial and rejected.

## Domain realism

| Domain | Question realism | Schema realism | Data realism | Business-rule realism |
|---|---|---|---|---|
| commerce_ops | MEDIUM | MEDIUM | LOW | MEDIUM |
| fleet_ops | MEDIUM | MEDIUM | LOW | MEDIUM |
| support_ops | MEDIUM | MEDIUM | LOW | MEDIUM |

All three schemas resemble plausible operational domains and have useful relational/JSON/temporal structure. Data is visibly synthetic: sequential names and identifiers, modulo-driven categories/statuses, nearly uniform distributions, and few NULLs in the fields exercised by answerable cases. The strongest domain cases are commerce population/metric cases, fleet route efficiency/latest reading, and support SLA/share cases. The weakest are the cases named in the repair queue.

## Circularity and benchmark value

Authoring circularity across all 30 cases:

| Circularity | Count |
|---|---:|
| LOW | 13 |
| MEDIUM | 13 |
| HIGH | 4 |

HIGH cases: `fleet_02`, `support_02`, `support_05`, `support_06`. They mirror a single author-written query convention or carry tags/constraints not independently supported by the question.

Benchmark value across all 30 cases:

| Value | Count |
|---|---:|
| HIGH_VALUE | 23 |
| MEDIUM_VALUE | 6 |
| LOW_VALUE | 1 |

The low-value case is `support_08`, which should be replaced before scaling. `commerce_01`/`commerce_02` are useful smoke tests but form a simple-filter redundancy cluster; `fleet_03`/`fleet_07` reuse the same route-efficiency mechanism.

## Machine validation versus human quality

Machine validation is green because references agree with themselves, fixtures execute, and mutants are killed. Human audit finds that self-consistency is not semantic correctness: shared hidden assumptions survive A/B agreement, and a technically strict result contract can score a reasonable unordered answer as wrong. The pilot contains real governance tests, but the answerable portion currently mixes governed reasoning with reconstruction of author conventions.

## Scale and repair gate

### Is the current 30-case pilot ready to become the design template for a 240-case benchmark?

NO.

The repair gate is M34.2: repair all REVISE cases, replace `support_08`, rerun machine validation, rerun targeted human audit, and require 30/30 machine-validated plus 30/30 human-accepted before using this pilot as a scaling template.

## Final verdict

### Did machine validation overestimate pilot quality?

YES

### Are there ANSWERABLE cases requiring hidden knowledge?

YES

### Are there semantic-target assumptions not uniquely justified by question/context?

YES

### Are any reference implementations semantically wrong despite A/B execution agreement?

YES

### Are all counterfactual suites meaningfully discriminating?

NO

### Are all 60 mutants meaningful semantic negatives?

NO

### Are the AUTHORITY_BLOCKED cases realistic governance tests?

MIXED

### Are the AMBIGUOUS cases genuinely ambiguous?

MIXED

### Is the pilot sufficiently realistic?

MIXED

### Is the current pilot ready for model evaluation?

NO

### Is the current pilot ready to scale to ~240 cases?

NO
