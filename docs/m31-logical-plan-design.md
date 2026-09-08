# M31 — Small Compositional Logical Plan

M31 introduces a smaller model-facing language while preserving the canonical
`SemanticQueryPlan`/`SemanticQueryIR` engine as the server authority.

## Boundary

```text
Question + bounded semantic catalog
              ↓
          Luna
              ↓
    LogicalQueryPlanV1
              ↓
    deterministic resolver
              ↓
     SemanticQueryPlan
              ↓
       SemanticQueryIR
              ↓
        compiler → validator → M1 → PostgreSQL
```

The model chooses semantic meaning and logical dataflow. The server resolves
mapping IDs, relationship paths, physical sources and keys, population safety,
grain, output bookkeeping, scope, and CTE/derived-relation mechanics. The
logical contract contains no SQL, physical join key, alias, CTE name, scope ID,
or arbitrary expression escape hatch.

## LogicalQueryPlanV1

The contract is a bounded ordered sequence with backward-only integer step
references. The nine operator families are:

| Operator | Model-owned meaning | Server-derived consequences |
| --- | --- | --- |
| `SCAN` | semantic anchor/entity | source binding and base population |
| `RELATE` | match/preserve/semi/anti relation to an entity or prior result | authorized path, joins, keys, fanout checks |
| `FILTER` | typed boolean predicate | canonical predicate expressions |
| `PROJECT` | requested returned values in list order | positions, roles, aliases, wrappers |
| `AGGREGATE` | grouping and supported aggregate functions | canonical grain and aggregate AST |
| `COMPUTE` | typed mathematical result variants | calculation normalization |
| `WINDOW` | function, partitions, logical ordering | window AST and scope plumbing |
| `SORT` | logical target and direction | canonical order expression |
| `TOP` | bounded limit/offset | final query nesting required by SQL |

The expression family is tagged and bounded: attributes, prior results,
literals, intervals, binary arithmetic/comparison, boolean predicates, typed
function calls, CASE, and scalar prior results. There is no string expression
field.

The 16-case oracle reconstruction requires 3–29 logical steps; the schema cap
is 32. The observed median is 9. This is a bounded real-data limit, not an
unbounded plan field.

## Relationship and population policy

`RELATE` expresses population semantics rather than SQL join type. The
resolver uses the server-owned relationship graph and fails closed for missing
or ambiguous paths. Population contracts, relationship IDs, physical keys,
fanout policy, and base entity IDs are server-owned.

The oracle slice exposed an important limitation: several canonical reference
queries use explicit joins or nested result keys that are not represented by
the current relationship metadata. M31 does not add benchmark-specific
mappings or fuzzy key inference to hide that limitation.

## Oracle gate

`evaluation/fixtures/m31_oracle_logical_ceiling.json` records the deterministic
reconstruction. Logical fixtures were produced offline from the existing
oracle plans and are never imported by runtime/provider code. M31.0R generic
lowering work raised resolver/compile/semantic-validator acceptance from
12/12/10 to 15/15/15 of the historical 16-case denominator. The remaining
mental case requires a relationship absent from the server metadata, and the
vaccine case needs a correlated scalar outer-reference type that the current
logical reference family does not carry.

This is an explicit M31.0R NO-GO. M31.1 provider acquisition, development
iterations, untouched confirmation, and scale-out are not run because doing so
would confound unresolved deterministic lowering/representation failures with
model behavior.

## Provider contract prepared but not exercised

The provider path uses the proven strict native JSON Schema response format:

```text
response_format = json_schema
strict = true
```

The projected schema has 9 top-level operator families, 21,593 compact JSON
bytes, 34 definitions, 29 provider `anyOf` branches, no `oneOf`, and maximum
schema depth 9. The full canonical provider schema baseline was 39,454 bytes
and 63 `anyOf` branches. The reduction is material, but the oracle gate is the
primary criterion.

## Non-negotiable invariants

The canonical semantic engine remains internal authority. M1 remains mandatory.
There is no agent, selector, retry, repair, pass@K, model switch, SQL fixer,
gold SQL leakage, or runtime use of offline oracle fixtures.
