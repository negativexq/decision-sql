# DecisionSQL

DecisionSQL is a production-oriented foundation for a governed Text-to-SQL / Decision Intelligence service. It will convert natural-language business questions into safe, read-only PostgreSQL queries while preserving authorization, provenance, and measurable failure attribution.

## North-star question

How do we prove that the query used the correct business data, respected authorization boundaries, could not execute dangerous or expensive SQL, produced an answer supported by its result, and failed at an identifiable pipeline stage when it could not answer?

## Core authority boundary

The LLM will propose SQL. Deterministic application code will decide what may execute. The model will not own identity, tenant scope, authorization, database credentials, or execution authority. The future execution path will add AST validation, policy evaluation, an `EXPLAIN` cost gate, read-only transaction settings, PostgreSQL controls, bounded results, and result validation.

## Implemented scope: M0

- Python 3.12 project with FastAPI, Pydantic Settings, SQLAlchemy 2.x, Alembic, `sqlglot`, pytest, Ruff, mypy, and OpenTelemetry API.
- Docker Compose PostgreSQL 16 environment.
- Eight-table synthetic commerce schema with deterministic seed data.
- Separate admin and restricted reader database connections.
- `GET /health` with a reader-backed `SELECT 1` connectivity check.
- Typed `UserContext`, `QueryRequest`, `QueryStatus`, `QueryResult`, and `FailureStage` models.
- Provider-neutral LLM interface; it is intentionally unconfigured and does not call a model.
- Architecture, security, and evaluation plans.

There is no Text-to-SQL or query execution endpoint in M0.

## Local startup

Requirements: Docker with Compose support.

```bash
cp .env.example .env
docker compose up --build
```

The database becomes healthy, then the `migrate` and `seed` one-shot services run before the API starts. Check the API:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"ok","database":"ok"}
```

To include the optional Jaeger-compatible local service:

```bash
docker compose --profile observability up --build
```

Open Jaeger at `http://localhost:16686` after a future exporter/instrumentation stage is enabled.

## Development commands

With Python 3.12 and the development dependencies installed:

```bash
python -m pytest
ruff check .
mypy app demo
```

For a local database, migrations and seeding can be run explicitly with `ADMIN_DATABASE_URL` set:

```bash
alembic upgrade head
python -m demo.seed
```

See [docs/architecture.md](docs/architecture.md), [docs/security.md](docs/security.md), and [docs/evaluation.md](docs/evaluation.md) for boundaries and intentionally deferred work.
