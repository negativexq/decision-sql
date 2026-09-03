# M4 — Verified Query Memory

M4 adds a provider-facing experiment for the residual direct Text-to-SQL path.
It keeps a small, server-owned corpus of independently verified SQL examples
separate from the governed semantic catalog. Retrieved SQL is prompt context
only; newly generated SQL still enters the existing M1 planning, policy, cost,
and read-only execution path.

## Frozen provider-free inputs

- Corpus: `decisionsql-demo-verified-query-memory`, version `1`
- Memory examples: `50`
- DEV questions: `48`
- HOLDOUT questions: `32`
- Corpus SHA-256: `f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae`
- Benchmark SHA-256: `c7387a3e86d144348d94da949c01c027af0e384381340a858d7083a4c5887970`
- DEV SHA-256: `34ff68f3b0fe632645d5e1013c2498fd4ae0c97a1e8287c308fe3c82b134c2b0`
- HOLDOUT SHA-256: `910bb52de4d58e2963c695748e15064ef8d73b1c83c8dd6b74147c35c5e176fd`
- Retriever: `m4-retriever-v1`, lexical + schema overlap, `K=3`

The corpus uses deterministic lexical, schema-overlap, and bounded heuristic
signals. It does not use embeddings, a vector database, an LLM reranker, or
previous benchmark holdout answers. Exact question, SQL, normalized SQL, and
question-ID leakage checks are provider-free.

## Provider-free gate

All 50 memory SQL statements and all 80 DEV/HOLDOUT reference statements parse,
pass M1, and execute successfully against the demo PostgreSQL database. The
retrieval Phase A gate selected the lexical + schema configuration with 37/48
(77.08%) useful-precedent recall on DEV, above the 70% minimum.

The provider-free gate passed before inference. The selected retriever reached
37/48 (77.08%) useful-precedent recall on DEV. The provider configuration was
`gpt-5.6-luna`, with reasoning disabled and temperature omitted; no smoke call,
retry, repair call, reranker, or embedding call was used.

## Frozen evaluation result

| Evaluation | Direct SQL | Verified memory | Delta |
| --- | ---: | ---: | ---: |
| DEV | 15/48 (31.25%) | 29/48 (60.42%) | +29.17 pp |
| HOLDOUT | 10/32 (31.25%) | 17/32 (53.13%) | +21.88 pp |

Pairwise DEV results were 15 BOTH_CORRECT, 14 MEMORY_ONLY, 0 BASELINE_ONLY,
and 19 BOTH_INCORRECT. HOLDOUT results were 10 BOTH_CORRECT, 7 MEMORY_ONLY,
0 BASELINE_ONLY, and 15 BOTH_INCORRECT. Provider failures and detected
`EXAMPLE_OVERTRANSFER` failures were zero in both splits.

The memory arm used 55,908 input and 5,492 output tokens on DEV, versus 45,890
and 6,275 for direct SQL. On HOLDOUT it used 37,154 input and 3,697 output
tokens, versus 30,577 and 4,619. Provider latency averaged 2,249 ms for memory
versus 2,459 ms direct on DEV, and 2,326 ms versus 2,662 ms on HOLDOUT.

M4 classification: **VERIFIED_QUERY_MEMORY_ACCEPTED**. HOLDOUT was consumed
once, only after the DEV gate passed. This acceptance is limited to the frozen
internal direct-path experiment and does not enable the feature by default.

## Scope and safety

M4 does not add SQL repair, execution-feedback loops, embeddings, temporal or
value grounding, metric catalog capabilities, or governed routing. The memory
feature is not enabled by default. It does not solve arbitrary semantic
memory creation, automatic production learning, or multi-agent generation.
