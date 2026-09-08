# Decision-SQL Bench v0.1

Decision-SQL Bench is an original, synthetic, governed Text-to-SQL benchmark. It measures both answering and safe non-answering. Semantic targets are evaluator-only annotations; reference SQL is a verified implementation witness, not the definition of truth.

The pilot contains three isolated PostgreSQL schema packs (`commerce_ops`, `fleet_ops`, `support_ops`) and exactly 30 audited cases: 20 `ANSWERABLE`, 5 `AUTHORITY_BLOCKED`, 3 `AMBIGUOUS`, and 2 `POLICY_BLOCKED`.

Build or rebuild deterministic artifacts with:

```bash
python -m benchmark.cli build
```

With PostgreSQL reachable through `M34_DATABASE_URL` (default: the local Decision-SQL PostgreSQL service), run the gates with:

```bash
python -m benchmark.cli validate-references --split pilot
python -m benchmark.cli mutation-test --split pilot
python -m benchmark.cli validate --split pilot
python -m benchmark.cli report --split pilot
```

The evaluator accepts generic submissions and keeps model-visible case files separate from evaluator-only ground truth. No provider or model is called by this milestone.
