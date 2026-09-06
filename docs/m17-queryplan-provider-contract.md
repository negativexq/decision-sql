# M17 — QueryPlan provider contract repair

M16 remains a valid negative end-to-end experiment: 25/90 intervention cases
were correct, and 63/90 did not produce an accepted canonical QueryPlan V1.
The compact M16 evidence does not preserve enough response data to recover the
63-case mechanical distribution, so that distribution is not retrospectively
reclassified. The 25/27 accepted-plan figure remains conditional diagnostic
evidence only.

M17 repairs the provider delivery boundary prospectively. `QueryPlanWireV2` is
a strict, versioned wire DTO whose JSON Schema is derived from the typed model.
The adapter performs only explicit wire-to-canonical conversion into the
unchanged `QueryPlanV1`; catalog validation, `QueryPlanV1Compiler`, M1, and P0
typed-result comparison remain authoritative and unchanged. Provider SQL,
expressions, join conditions, and unknown fields are rejected.

The configured OpenAI-compatible endpoint was verified for JSON-object mode;
provider-native JSON-Schema enforcement was not verified, so the complete
DTO-derived schema and enum/value contract are included in the prompt. The
contract uses one neutral format-only example and no semantic few-shot example.
No repair, judge, retrieval, or memory call is used.

## Fresh preflight

The fresh 18-case preflight used three cases for each M16 shape. With
GPT-5.6 Luna (`gpt-5.6-luna`) at temperature 0.0, all 18 responses were
received, parsed, wire-validated, canonicalized, catalog-accepted, compiled,
admitted by M1, executed, and compared. Control and intervention were each
correct on 17/18. Contract acquisition was 18/18, so the preflight gate passed.

## Fresh primary result

The fresh 90-case primary used the frozen QueryPlan V1 shape distribution and
balanced arm order. Contract acquisition was 90/90. Control scored 79/90
(87.778%) and native QueryPlan scored 86/90 (95.556%), a +7.778 percentage
point delta. Paired states were A/B/C/D = 75/4/11/0; C−B = 7 and the exact
two-sided paired p-value was 0.11846923828125.

The provider contract reliability gate passed, but the precommitted capability
gain gate required at least +10 percentage points and therefore did not pass.
Confirmation was not authorized. The resulting classification is
`M17_GENERAL_QUERYPLAN_V1_CAPABILITY_NOT_PROVEN_AFTER_CONTRACT_REPAIR`.

M17 changes the provider output contract while preserving canonical QueryPlan
V1 semantics and compiler ownership. It does not establish that the broader
semantic planning task is better than direct SQL generation. M12R’s bounded M3
result remains separate evidence, not a substitute for this result.

## Research transfer and anti-loop rule

- M1 remains immutable execution authority.
- P0 typed execution-result semantics remain correctness authority; weak
  structural similarity is not used as correctness.
- D2 is validation infrastructure only; historical D3/P4 residual mining is
  closed.
- Memory and retrieval remain OFF because P3 did not establish decision-grade
  causal benefit.
- M13/M14 routing and runtime behavior are unchanged.

M16 did not establish that QueryPlan semantic planning was incapable; it
established that the exact end-to-end intervention failed. Because failure
stages were collapsed, M17 first repairs observability before attribution.
M17 changes the provider delivery contract, not the canonical semantics.

This is a bounded contract revalidation, not a new architecture review. Do not
tune the consumed questions or add a repair variant in this attempt. If the
contract is reliable but the exact V1 capability is not proven, close this
exact V1 line rather than expanding it immediately.
