# M31.0R — Logical Resolver Ceiling Closure

M31.0R was run offline with provider calls held at zero. The resolver was
treated as a compiler boundary; no model-facing physical keys, CTE names, SQL
fragments, gold SQL, or case-specific runtime rules were added.

## Initial failures and general fixes

| Case/mechanism | Initial failure | General fix | Result |
| --- | --- | --- | --- |
| cybermarket_1 | relationship validation treated a CTE source as disconnected | canonical validation and compilation now seed relationship lineage from visible CTE base entities; CTE-source joins use server-derived typed keys when required | compile and semantic validation pass |
| insider_1 | `trader.tradereg` was lost after a projected logical result | resolver tracks visible result lineage and server-hidden relationship attributes through nested scopes | compile and semantic validation pass |
| gaming_1 / robot_1 | prior-result output scope and result relation candidate ambiguity | result exports are carried as server-only scoped outputs; candidate matching uses visible result attributes only | compile and semantic validation pass |
| news_1 / vaccine_1 | scalar-result CTE definitions were absent from the enclosing scope | deterministic scalar CTE dependencies are hoisted into the enclosing server scope | both compile and pass semantic validation; vaccine remains execution-non-equivalent because correlated outer scope is not represented by the current logical reference type |
| mental_1 | facilities join uses `encounters.facid = facilities.fackey`, absent from RelationshipGraph | no unsafe inference was added | fail closed |

The resolver now has explicit server-only state for hidden attributes and
carried prior-result outputs. Hidden fields are exported only inside generated
relations, are never final outputs, and are removed by the final logical
projection.

## Relationship metadata audit

The mental schema meaning catalog describes `encounters.facid` as referencing
the facility ID but explicitly says it is not an enforced foreign key. The
current server mapping contains `clinicians.facconnect -> facilities.fackey`
but no `encounters.facid -> facilities.fackey` relationship. The canonical
oracle parser represents the reference SQL join as a typed ad-hoc join; that is
evaluator/oracle input, not server-owned relationship metadata. No mapping was
added from the reference SQL.

This makes the prior 16-case representability classification inconsistent with
the M31 responsibility boundary: mental_1 is canonical-oracle representable,
but not LogicalQueryPlanV1-resolvable under the available server metadata.

## Gate result

For the 16 previously classified representable cases:

```text
logical fixtures                 16/16
resolver success                 15/16
authoritative canonical match    14/16
compile                          15/16
SemanticConsistencyValidator     15/16
M1                               not run as a complete 16/16 ceiling was not restored
execution                        not run as a complete 16/16 ceiling was not restored
```

The repaired non-mental cases were independently compiled, accepted by M1,
and executed as a diagnostic. They produced 13 official correct results, one
evaluator limitation, and one remaining semantic mismatch (vaccine_1). The
vaccine mismatch is a logical-scope representation gap: the existing logical
reference type cannot distinguish an inner scalar result reference from a
correlated outer-row reference after server lowering.

Therefore M31.0R is `NO-GO` for provider evaluation. No Luna benchmark call,
smoke call, confirmation split, or scale-out was consumed.
