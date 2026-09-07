from dataclasses import dataclass

from app.semantics.models import Cardinality, MetricCatalog, RelationshipDefinition
from app.semantics.semantic_mapping import SemanticMappingSnapshot


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


@dataclass(frozen=True)
class SemanticPathStep:
    """A path step resolved from the canonical server-owned mapping."""

    relationship_id: str
    source_entity: str
    target_entity: str
    forward: bool
    cardinality: str


class SemanticRelationshipGraph:
    """Relationship graph for the canonical semantic mapping.

    The original metric graph remains source-compatible; this graph applies
    the same deterministic shortest-unique-path rule to identity mappings.
    """

    def __init__(self, mapping: SemanticMappingSnapshot) -> None:
        self.mapping = mapping

    def shortest_safe_path(
        self, source: str, target: str, *, allow_fanout: bool = False
    ) -> tuple[SemanticPathStep, ...]:
        if source == target:
            return ()
        paths: list[tuple[SemanticPathStep, ...]] = []
        self._walk(source, target, (), {source}, paths, allow_fanout)
        if not paths:
            raise RelationshipPathError(f"no safe relationship path: {source} -> {target}")
        shortest_length = min(len(path) for path in paths)
        shortest = [path for path in paths if len(path) == shortest_length]
        if len(shortest) != 1:
            raise RelationshipPathError(f"ambiguous relationship path: {source} -> {target}")
        return shortest[0]

    def resolve_path(
        self, source: str, target: str, *, allow_fanout: bool = False
    ) -> tuple[SemanticPathStep, ...]:
        """Resolve a unique server-owned path, preserving typed failure text."""
        return self.shortest_safe_path(source, target, allow_fanout=allow_fanout)

    def _walk(
        self,
        current: str,
        target: str,
        path: tuple[SemanticPathStep, ...],
        visited: set[str],
        results: list[tuple[SemanticPathStep, ...]],
        allow_fanout: bool,
    ) -> None:
        if current == target:
            results.append(path)
            return
        if len(path) >= len(self.mapping.entities):
            return
        for relationship in self.mapping.relationships_for(current):
            if relationship.from_entity_id == current:
                next_entity = relationship.to_entity_id
                forward = True
            elif relationship.to_entity_id == current:
                if relationship.cardinality != "ONE_TO_ONE" and not allow_fanout:
                    continue
                next_entity = relationship.from_entity_id
                forward = False
            else:  # pragma: no cover - relationships_for already filters this
                continue
            if next_entity in visited:
                continue
            step = SemanticPathStep(
                relationship_id=relationship.relationship_id,
                source_entity=current,
                target_entity=next_entity,
                forward=forward,
                cardinality=relationship.cardinality,
            )
            self._walk(
                next_entity,
                target,
                path + (step,),
                visited | {next_entity},
                results,
                allow_fanout,
            )
