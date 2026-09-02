# Demo commerce schema

The schema is implemented by Alembic revision `0001_commerce_schema` and mirrored as typed SQLAlchemy models in `app/db/models.py`.

The relationships support customer, region, representative, product, order, payment, and refund analysis. The fixture is synthetic and reproducible; run `python -m demo.seed` with `ADMIN_DATABASE_URL` configured to replace it.
