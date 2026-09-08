# M38 authoring proposal for later scale

M38 freezes a 6-database / 90-case development benchmark. It is not a final hidden test set.

## Future mechanism coverage

The next expansion should cover projection, filtering, aggregation, grouping, authorized and multi-hop relationships, population, conditional measures, calculations, ratios, temporal boundaries, latest-row tie breaks, JSON, windows, nested and correlated queries, set operations, ordering, limits, NULL semantics, and precision. At least half of new answerable cases should combine three or more mechanisms, with explicit controls retained.

## Database isolation

Keep database-level DEV and CONFIRMATION splits. A future FINAL split must use entirely unseen databases; never randomly distribute cases from one database across final partitions.

## Authoring rule

New cases may reuse mechanism families but must not be paraphrases or cosmetic domain reskins of pilot questions.
