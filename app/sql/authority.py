"""Request-scoped SQL relation authority and structural dependency checks."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from app.catalog.models import SchemaCatalog, SchemaContext


def canonical_relation(schema: str | None, table: str) -> str:
    """Return the lowercase relation identity used by the runtime authority contract."""
    normalized_table = table.strip().lower()
    normalized_schema = (schema or "public").strip().lower()
    if not normalized_table or not normalized_schema:
        raise ValueError("Relation schema and table must be non-empty")
    return f"{normalized_schema}.{normalized_table}"


class ExecutionAuthority(BaseModel):
    """Immutable relation-level authority for one request or server route.

    The catalog may contain technically queryable tables that are not in this
    request's authority envelope.  This contract intentionally contains no
    benchmark truth, SQL, or evaluator fields.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed_relations: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("allowed_relations")
    @classmethod
    def normalize_relations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: set[str] = set()
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Allowed relations must be non-empty strings")
            parts = tuple(part.strip() for part in value.split("."))
            if len(parts) == 1:
                normalized.add(canonical_relation(None, parts[0]))
            elif len(parts) == 2:
                normalized.add(canonical_relation(parts[0], parts[1]))
            else:
                raise ValueError("Allowed relations must be table or schema.table identifiers")
        return tuple(sorted(normalized))

    @classmethod
    def from_catalog(cls, catalog: SchemaCatalog) -> ExecutionAuthority:
        return cls.from_table_names(table.name for table in catalog.tables if table.queryable)

    @classmethod
    def from_table_names(cls, table_names: Iterable[str]) -> ExecutionAuthority:
        return cls(
            allowed_relations=tuple(canonical_relation("public", name) for name in table_names)
        )

    @classmethod
    def from_context(cls, context: SchemaContext) -> ExecutionAuthority:
        """Build authority from the same server-owned tables exposed to the model."""
        return cls(
            allowed_relations=tuple(
                canonical_relation("public", table.name) for table in context.tables
            )
        )


class AuthorityCode(StrEnum):
    """Stable authority failure code without coupling to global SQL policy codes."""

    UNAUTHORIZED_RELATION = "UNAUTHORIZED_RELATION"


class AuthorityRejection(BaseModel):
    """Bounded deterministic diagnostic for a request authority violation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: AuthorityCode = AuthorityCode.UNAUTHORIZED_RELATION
    referenced_relations: tuple[str, ...] = ()
    unauthorized_relations: tuple[str, ...] = ()
    message: str


def external_relations(expression: exp.Expression) -> tuple[str, ...]:
    """Extract physical relation dependencies, excluding CTE and derived aliases.

    SQLGlot scopes distinguish physical ``Table`` sources from CTE/derived
    ``Scope`` sources.  This avoids treating a CTE name as a database relation.
    """
    physical_nodes: dict[int, exp.Table] = {}
    for scope in traverse_scope(expression):
        for source in scope.sources.values():
            if isinstance(source, exp.Table):
                physical_nodes[id(source)] = source
    return tuple(sorted({_table_identity(table) for table in physical_nodes.values()}))


def validate_authority(
    expression: exp.Expression, authority: ExecutionAuthority
) -> AuthorityRejection | None:
    referenced = external_relations(expression)
    allowed = set(authority.allowed_relations)
    unauthorized = tuple(sorted(set(referenced) - allowed))
    if not unauthorized:
        return None
    return AuthorityRejection(
        referenced_relations=referenced,
        unauthorized_relations=unauthorized,
        message=(
            "SQL references relations outside the request execution authority: "
            + ", ".join(unauthorized)
        ),
    )


def _table_identity(table: exp.Table) -> str:
    if table.catalog:
        return ".".join(part.lower() for part in (table.catalog, table.db or "public", table.name))
    return canonical_relation(table.db, table.name)
