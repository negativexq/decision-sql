# M26 — Query-Aware Grounding + Database Value Linking

## Objective

M26 measured the existing one-call DIRECT Luna path after adding a generic,
deterministic grounding layer. The intervention was limited to server-owned
context construction: table retrieval, column retrieval, bounded FK closure,
query-aware semantic KB retrieval, and bounded database value evidence.

No planner, repair, judge, router, embedding call, or second model call was
introduced.

## Why full semantic context is insufficient

M25.2 exposed the complete live structural schema, all column meanings, and the
complete database KB. That was semantically complete but large: its median
model input was 13,212 estimated tokens. M26 tests whether deterministic
selection can reduce irrelevant ontology content while retaining structural
recall and adding a small amount of actual-value evidence.

## Architecture

`app/grounding/query_aware.py` accepts the question, live catalog, authorized
semantic annotations, and an optional bounded read-only SQLAlchemy engine. It
returns a typed `GroundingContext`; it has no SQL-generation or evaluation
authority.

Table ranking uses deterministic lexical overlap over table names, aliases,
descriptions, columns, and column meanings. Column ranking is independent and
can add an owning table when its signal is at least as strong as the weakest
table-first candidate. Candidates are accepted in relevance order only when
their minimal FK closure fits the ten-table bound. Paths are shortest, depth
bounded to three edges, and deterministically tie-broken.

All queryable columns of selected tables are retained for structural recall.
Only their meanings are rendered. KB records are ranked over name,
description, and definition, with the top twelve records retained; duplicate
records are not collapsed.

## Database value linking

At most sixteen catalog-approved candidate columns, twelve extracted phrases,
and eight values per column are used. Lookups are parameterized and read-only,
with exact/case-insensitive matching represented before bounded substring
matches. No arbitrary rows are exposed and no unapproved identifier is
constructed.

## Security boundary

M1 remains the sole SQL safety authority. Grounding does not alter the
SELECT-only boundary, table/column authorization, function policy, cost gate,
single-statement rule, or read-only executor. Protected external-knowledge IDs
were not used as retrieval input; they were available only for offline recall
diagnostics.

## Frozen pilot methodology

The exact frozen 18-case order from M25.2 was used. Production prompt, provider,
model (`gpt-5.6-luna`), settings, M1, evaluator, and DIRECT route were frozen.
Each case received at most one provider request, with no retries or additional
LLM calls. The full 180-case model benchmark was not run.

Provider-free grounding constructed all 180 SELECT contexts and all 18 pilot
contexts. Pilot table coverage was 18/18; aggregate table recall was 100% on
the pilot. Across all SELECT cases, aggregate table recall was 95.86% and
column recall where structurally extractable was 96.39%. Value evidence was
available for 168/180 cases.

## Results

M25.2, the full-semantic-context arm, scored 6/18. M26 scored 4/18 official
raw, with 18 provider calls, 18 provider successes, 16 M1-accepted proposals,
and 16 successful executions. The paired transitions were 3 old-correct/new-
correct, 3 old-correct/new-wrong, 1 old-wrong/new-correct, and 11
old-wrong/new-wrong. The exact two-sided McNemar p-value was 0.625.

This is a valid negative-transfer pilot result, not a full-benchmark score.
It does not prove that grounding is generally harmful; it shows that this
specific frozen retriever/value-linking intervention did not improve this
18-case paired sample.

## Context efficiency

M26 pre-generation context had median estimated size 9,246 tokens, P95 12,586,
and maximum 18,629. Authoritative provider usage was 175,067 input tokens in
total, median 9,363.5, P95 13,183, and maximum 13,319.

## Claim boundary

M26 measures one fresh 18-case paired arm. It says nothing about the full
LiveSQLBench model accuracy, and it does not authorize an automatic 180-case
run. Remaining failures should be inspected before adding another query-
requirements or reasoning intervention.
