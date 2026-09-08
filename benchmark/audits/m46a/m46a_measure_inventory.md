# M46A measure inventory

The inventory is server-owned audit metadata derived from public schema,
attribute semantics, authorized relationship cardinalities, and public metric
contracts. It is not injected into the frozen model-facing context.

| Category | Count |
|---|---:|
| Numeric attributes audited | 153 |
| Identifiers | 126 |
| Dimensions | 11 |
| Measures | 16 |
| Additive | 15 |
| Semi-additive | 0 |
| Non-additive | 1 |
| Derived | 0 |
| Unknown | 0 |
| Required unknown | 0 |

Native-grain and rollup facts are present in the per-database catalog and are
validated by the typed contract before replay.
