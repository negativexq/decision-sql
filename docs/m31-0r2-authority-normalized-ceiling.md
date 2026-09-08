# M31.0R2 — Authority-Normalized Deterministic Ceiling

M31.0R2 corrected the historical oracle denominator before model evaluation.
Gold SQL can express a join that the server is not authorized to resolve; that
is not legitimate Decision-SQL representability.

`mental_1` is therefore classified as `METADATA_BLOCKED`. The current
server-owned metadata contains `clinicians.facconnect -> facilities.fackey`,
but not `encounters.facid -> facilities.fackey`. No relationship was added
from gold SQL or from the benchmark case.

The remaining genuine lowering gap was `vaccine_1`. The offline logical
fixture now uses a bounded `outer_attribute` reference. The resolver validates
scope depth and visibility, resolves the reference against an enclosing
logical state, and lowers it through the existing canonical scalar-subquery
compiler. Correlated predicates remain in the scalar query scope; they are not
placed in server CTEs that would illegally depend on an outer row.

The normalized population is 15 legitimate cases and 3 metadata-blocked
cases. The deterministic result is:

```text
logical fixtures       15/15
resolver               15/15
canonical match        15/15
compile                15/15
semantic validator     15/15
M1                     15/15
execution              15/15
official correct       14
evaluator limitation    1
engine wrong            0
```

This gate passed without a provider call. The 18-case Luna run is the next
frozen phase and must use the original 18-case diagnostic population, while
keeping the three metadata blockers separate in downstream metrics.
