"""What a pinned query relied on, so a later refresh can tell when it changed.

The checks decide whether a saved query may still run; they cannot tell whether it
still means what it meant.  A column renamed, retyped, redescribed, hidden, or newly
marked sensitive can leave the SQL valid and the chart misleading, so a card keeps a
fingerprint of each table it reads and the reader is told when one differs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from app.catalog.models import SchemaCatalog, TableMetadata

REMOVED = "removed"


def _fingerprint(table: TableMetadata) -> str:
    # Stored values and value hints change with the data, not the meaning; leave them out.
    shape = {
        "description": table.description,
        "queryable": table.queryable,
        "columns": sorted(
            [
                column.name.lower(),
                column.type,
                column.description,
                column.sensitivity.value,
                column.queryable,
                column.primary_key,
            ]
            for column in table.columns
        ),
        "relationships": sorted(
            [item.column.lower(), item.referenced_table.lower(), item.referenced_column.lower()]
            for item in table.relationships
        ),
    }
    encoded = json.dumps(shape, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def table_basis(catalog: SchemaCatalog, tables: Iterable[str]) -> dict[str, str]:
    basis: dict[str, str] = {}
    for name in tables:
        table = catalog.get_table(name)
        basis[name.lower()] = _fingerprint(table) if table is not None else REMOVED
    return basis


def changed_tables(basis: dict[str, str], catalog: SchemaCatalog) -> list[str]:
    """Tables whose description differs from the pinned one; none for an unpinned basis."""
    current = table_basis(catalog, basis)
    return sorted(name for name, value in basis.items() if current[name] != value)
