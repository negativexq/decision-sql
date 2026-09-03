# M3.5 — Semantic Contract Hardening

M3.5 adds lifecycle and provenance governance around the accepted M3 semantic
catalog. It does not add metrics, change metric formulas, or alter M3.4 route
selection.

## Contract identity

The typed Python catalog remains the source of truth. Every entity,
relationship, dimension, measure, and metric receives a deterministic stable
ID such as `metric:completed_revenue`, independent of its display label.
Meaning-bearing objects carry a positive semantic version and a SHA-256
definition hash. Descriptions and display labels are intentionally excluded
from definition hashes.

The current local contract is:

| Field | Value |
| --- | --- |
| Catalog ID | `decisionsql-demo-semantic-catalog` |
| Catalog version | `1` |
| Compiler contract version | `1` |
| Semantic object count | `48` |
| Semantic contract hash | `0463a10ecd3dbb414d11559f8559c604748f2b131a078d66ec79c4993d70eb3c` |

The checked-in snapshot is
[`app/semantics/semantic_contract.json`](../app/semantics/semantic_contract.json).
It contains identity, version, definition hash, lifecycle, and display
metadata, but no physical SQL implementation fields.

The M3 artifact hashes remain separate historical evidence:

- benchmark: `bae37fa9c4850954dbae1a58b6c26c5b9cb1855b7ab6b6acd562845ee4bbb0b8`
- catalog artifact: `1ef47fe5d85f9e847740fb5c53932cbb1e17253e34f2b73c4e707125ff38720a`
- public glossary: `af114d4c52dfed39826e9a49a9159cb2221178999a0b5738ebfb6eaba620cf07`

The M3.5 contract hash is not a replacement for any of those hashes.

## Lifecycle

Objects use the bounded lifecycle states `ACTIVE`, `DEPRECATED`, and
`RETIRED`.

- `ACTIVE` objects are exposed to provider grounding and can compile.
- `DEPRECATED` objects are not exposed to normal provider grounding but may be
  compiled by an explicit internal request for compatibility. A replacement
  reference is optional and is never applied automatically.
- `RETIRED` objects are hidden from grounding and rejected for new compilation.
  Their snapshot identity remains available for historical provenance.

Replacement references must resolve to an existing object, cannot be self
references, and cannot form cycles.

## Version rules

A semantic version must increase when meaning-bearing content changes,
including measures, aggregations, filters, valid dimensions, relationship or
grain dependencies, scale, null policy, and physical mappings that change the
business result. A description or display-label-only change does not require a
semantic version bump.

Catalog version must increase for additions, removals, semantic changes, and
lifecycle changes visible to production. A catalog version may change without
contract content changing, for operational release bookkeeping.

`diff_snapshots(old, new)` emits bounded `ADDED`, `REMOVED`,
`SEMANTIC_CHANGED`, `LIFECYCLE_CHANGED`, and `DISPLAY_ONLY_CHANGED` records.
`validate_transition(old, new)` rejects semantic drift without an object
version bump and catalog drift without a catalog-version bump.

## Snapshot commands

Regenerate the deterministic snapshot without a provider or database write:

```bash
python -m app.semantics.contract snapshot
```

Check the accepted snapshot in CI:

```bash
python -m app.semantics.contract check
```

The intended change workflow is to update semantic source deliberately, bump
the affected object version and catalog version, regenerate the snapshot, and
review the deterministic contract diff before committing both source and
snapshot.

## Execution provenance

Successful governed routes carry a bounded `SemanticExecutionProvenance` with:

- catalog ID and catalog version;
- full semantic contract hash;
- compiler contract version;
- metric stable ID, semantic version, definition hash, and lifecycle;
- requested dimension stable IDs.

M3.4 spans add the catalog ID, catalog version, first 16 characters of the
contract hash, metric ID/version, and lifecycle status. Full hashes remain in
structured provenance; they are not metric labels. Raw questions, physical
SQL, result rows, prompts, and secrets are not added to telemetry.

OFF remains the default and produces no semantic provenance or grounding call.
SHADOW and ON behavior remains unchanged; shadow results remain diagnostic and
the direct result remains authoritative in shadow mode.

## Invariants and limitations

M1 remains the sole SQL planning and execution authority. The semantic catalog
cannot contain arbitrary SQL, and the compiler API remains
`compile_metric(request)`.

M3.5 does not provide a remote registry, hot reload, user editing UI,
automatic semantic migrations, historical data snapshots, arbitrary runtime
metrics, temporal/value grounding, or full model/container supply-chain
provenance. External benchmarks and provider evaluations are not rerun.
