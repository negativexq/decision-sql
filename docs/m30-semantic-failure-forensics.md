# M30 — Semantic Decision Failure Forensics

This report is a provider-free replay of the frozen M29R.1 responses.
It does not modify prompts, plans, the semantic engine, or M1.

Provider calls during M30: **0**.

## Primary first-divergence counts

| Cause | Cases |
|---|---:|
| `METADATA_LIMITATION` | 2 |
| `CALCULATION_ERROR` | 3 |
| `RELATIONSHIP_ERROR` | 5 |
| `POPULATION_ERROR` | 6 |

## Case ledger

| Case | Status | First divergence | Primary cause | Metadata |
|---|---|---|---|---|
| `alien_1` | SEMANTIC_VALID_AND_CORRECT | `none` | `none` | False |
| `archeology_1` | METADATA_BLOCKED | `none` | `METADATA_LIMITATION` | True |
| `credit_1` | CANONICAL_MODEL_INVALID | `none` | `CALCULATION_ERROR` | False |
| `cross_db_1` | CANONICAL_MODEL_INVALID | `none` | `RELATIONSHIP_ERROR` | False |
| `crypto_1` | SEMANTIC_VALID_BUT_WRONG | `POPULATION_ERROR` | `POPULATION_ERROR` | False |
| `cybermarket_1` | SEMANTIC_VALID_BUT_WRONG | `POPULATION_ERROR` | `POPULATION_ERROR` | False |
| `disaster_1` | SEMANTIC_VALID_BUT_WRONG | `POPULATION_ERROR` | `POPULATION_ERROR` | False |
| `fake_1` | METADATA_BLOCKED | `none` | `METADATA_LIMITATION` | True |
| `gaming_1` | CANONICAL_MODEL_INVALID | `none` | `RELATIONSHIP_ERROR` | False |
| `insider_1` | CANONICAL_MODEL_INVALID | `none` | `RELATIONSHIP_ERROR` | False |
| `mental_1` | CANONICAL_MODEL_INVALID | `none` | `POPULATION_ERROR` | False |
| `museum_1` | CANONICAL_MODEL_INVALID | `none` | `POPULATION_ERROR` | False |
| `news_1` | CANONICAL_MODEL_INVALID | `none` | `CALCULATION_ERROR` | False |
| `polar_1` | SEMANTIC_VALID_BUT_WRONG | `POPULATION_ERROR` | `POPULATION_ERROR` | False |
| `robot_1` | CANONICAL_MODEL_INVALID | `none` | `RELATIONSHIP_ERROR` | False |
| `solar_1` | CANONICAL_MODEL_INVALID | `none` | `RELATIONSHIP_ERROR` | False |
| `vaccine_1` | DOWNSTREAM_ENGINE_FAILURE | `none` | `none` | False |
| `virtual_1` | CANONICAL_MODEL_INVALID | `none` | `CALCULATION_ERROR` | False |

## Boundary

Population, grain, relationship, calculation, and nested-scope failures are
semantic-plan evidence. They are not provider transport or projection failures.
The two relationship metadata limitations remain separate from model errors.
