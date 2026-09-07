"""Small, reusable generation guidance derived from the failure audits.

This is prompt guidance only. It never authorizes SQL, changes the catalog, or
weakens M1.
"""

from __future__ import annotations

QUERY_QUALITY_PACK = """QUERY QUALITY PACK (generation guidance; the original question wins):
- Project only requested outputs and their required derived expressions. Never use SELECT * and
  never add convenience, debug, identifier, or context columns.
- Preserve the requested row grain. Do not add joins, aggregation, DISTINCT, status/date filters,
  or business exclusions unless the question or supplied knowledge explicitly requires them.
- For top-N requests, ORDER BY the requested measure in the requested direction and apply the
  requested LIMIT. Do not invent a ranking metric or tie-breaker.
- Use a supplied knowledge definition for a named metric, but never invent a physical column,
  join, value, or formula when the definition is not explicit.
- Before returning SQL, verify every selected expression answers one requested output and every
  filter is grounded in the question or the supplied knowledge.
"""


def render_query_quality_pack() -> str:
    """Return the stable prompt fragment used by direct and blueprint generation."""

    return QUERY_QUALITY_PACK
