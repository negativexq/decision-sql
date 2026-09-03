from dataclasses import dataclass

from app.semantics.models import Cardinality, MetricCatalog, RelationshipDefinition


class RelationshipPathError(ValueError):
    pass


@dataclass(frozen=True)
class PathStep:
    relationship: RelationshipDefinition
    source_entity: str
    target_entity: str
    forward: bool


class RelationshipGraph:
    """Deterministic graph over server-owned semantic relationships."""

    def __init__(self, catalog: MetricCatalog) -> None:
        self.catalog = catalog

    def shortest_safe_path(self, source: str, target: str) -> tuple[PathStep, ...]:
        if source == target:
            return ()
        paths: list[tuple[PathStep, ...]] = []
        self._walk(source, target, (), {source}, paths)
        if not paths:
            raise RelationshipPathError(f"no safe relationship path: {source} -> {target}")
        shortest_length = min(len(path) for path in paths)
        shortest = [path for path in paths if len(path) == shortest_length]
        if len(shortest) != 1:
            raise RelationshipPathError(f"ambiguous relationship path: {source} -> {target}")
        return shortest[0]

    def _walk(
        self,
        current: str,
        target: str,
        path: tuple[PathStep, ...],
        visited: set[str],
        results: list[tuple[PathStep, ...]],
    ) -> None:
        if current == target:
            results.append(path)
            return
        if len(path) >= len(self.catalog.entities):
            return
        for relationship in self.catalog.relationships:
            next_entity: str | None = None
            forward = False
            if relationship.from_entity == current:
                next_entity = relationship.to_entity
                forward = True
            elif relationship.to_entity == current:
                # Reverse traversal expands a many-side row and is not safe for
                # an aggregate subplan in M3 V1.
                if relationship.cardinality is not Cardinality.ONE_TO_ONE:
                    continue
                next_entity = relationship.from_entity
            if next_entity is None or next_entity in visited:
                continue
            self._walk(
                next_entity,
                target,
                path
                + (
                    PathStep(
                        relationship=relationship,
                        source_entity=current,
                        target_entity=next_entity,
                        forward=forward,
                    ),
                ),
                visited | {next_entity},
                results,
            )
