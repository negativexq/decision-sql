"""Deterministic, evaluation-only semantic verification signals."""

from __future__ import annotations

import json
from collections.abc import Iterable
from hashlib import sha256

from sqlglot import exp

from app.catalog.models import SchemaCatalog
from app.db.models import Base
from app.memory.models import VerifiedQueryExample
from app.models.domain import TextToSqlRequest
from app.semantics.catalog import build_m3_catalog
from app.verification.models import (
    SemanticVerificationReport,
    VerificationRecommendation,
    VerificationSeverity,
    VerificationSignal,
    VerificationSignalCode,
)
from app.verification.question_signals import (
    explicit_entities,
    explicit_status_filter,
    has_aggregation_cue,
    has_average_cue,
    has_distinct_entity_cue,
    has_explicit_ordering_cue,
    has_threshold_cue,
    has_total_cue,
    normalized_question,
    requested_grouping_terms,
    requested_top_n,
)
from app.verification.sql_signals import SqlFacts, extract_sql_facts

VERIFIER_VERSION = "semantic-verifier-v1"
_RULES = (
    (VerificationSignalCode.REQUESTED_GROUPING_MISSING, VerificationSeverity.HARD),
    (VerificationSignalCode.TOP_N_STRUCTURE_MISSING, VerificationSeverity.HARD),
    (VerificationSignalCode.AGGREGATE_THRESHOLD_FILTER_MISSING, VerificationSeverity.HARD),
    (VerificationSignalCode.POSSIBLE_AGGREGATION_MISMATCH, VerificationSeverity.SOFT),
    (VerificationSignalCode.POSSIBLE_ENTITY_COUNT_FANOUT, VerificationSeverity.HARD),
    (VerificationSignalCode.POSSIBLE_AGGREGATE_FANOUT, VerificationSeverity.SOFT),
    (VerificationSignalCode.REQUESTED_DIMENSION_NOT_PROJECTED, VerificationSeverity.HARD),
    (VerificationSignalCode.REQUESTED_MEASURE_NOT_PROJECTED, VerificationSeverity.SOFT),
    (VerificationSignalCode.TOP_N_WITHOUT_ORDER, VerificationSeverity.HARD),
    (VerificationSignalCode.TOP_N_LIMIT_MISSING, VerificationSeverity.HARD),
    (VerificationSignalCode.GOVERNED_METRIC_SHOULD_USE_SEMANTIC_PATH, VerificationSeverity.HARD),
    (VerificationSignalCode.RETIRED_OR_INVALID_SEMANTIC_OBJECT, VerificationSeverity.HARD),
    (VerificationSignalCode.POSSIBLE_EXAMPLE_OVERTRANSFER, VerificationSeverity.SOFT),
    (VerificationSignalCode.EXPLICIT_ENTITY_NOT_GROUNDED, VerificationSeverity.HARD),
    (VerificationSignalCode.OUTPUT_SHAPE_SUSPICIOUS, VerificationSeverity.SOFT),
    (VerificationSignalCode.UNEXPECTED_CARTESIAN_JOIN, VerificationSeverity.HARD),
    (VerificationSignalCode.EXPLICIT_FILTER_MISSING, VerificationSeverity.HARD),
    (VerificationSignalCode.UNREQUESTED_LIMIT, VerificationSeverity.SOFT),
    (VerificationSignalCode.UNREQUESTED_FILTER, VerificationSeverity.SOFT),
    (VerificationSignalCode.DIRECT_PATH_SEMANTIC_CONTRADICTION, VerificationSeverity.SOFT),
)
RULESET_HASH = sha256(
    json.dumps(
        [(code.value, severity.value) for code, severity in _RULES],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()


class DeterministicSemanticVerifier:
    """Produce bounded signals without gold SQL/results or model calls."""

    def __init__(
        self,
        catalog: SchemaCatalog | None = None,
        *,
        governed_metric_names: frozenset[str] | None = None,
    ) -> None:
        self.catalog = catalog or _default_schema_catalog()
        self.governed_metric_names = governed_metric_names or frozenset(
            metric.name for metric in build_m3_catalog().metrics
        )

    def verify(
        self,
        question: str | TextToSqlRequest,
        sql: str,
        *,
        memory_examples: tuple[VerifiedQueryExample, ...] = (),
        generation_path: str = "DIRECT_SQL",
        semantic_object_status: str | None = None,
    ) -> SemanticVerificationReport:
        question_text = question.question if isinstance(question, TextToSqlRequest) else question
        facts = extract_sql_facts(sql)
        signals = tuple(
            signal
            for signal in self._signals(
                question_text,
                facts,
                memory_examples=memory_examples,
                generation_path=generation_path,
                semantic_object_status=semantic_object_status,
            )
            if signal is not None
        )
        recommendation = (
            VerificationRecommendation.SUSPICIOUS
            if any(signal.severity is VerificationSeverity.HARD for signal in signals)
            else VerificationRecommendation.CONTINUE
        )
        return SemanticVerificationReport(
            verifier_version=VERIFIER_VERSION,
            ruleset_hash=RULESET_HASH,
            signals=signals,
            recommendation=recommendation,
        )

    def _signals(
        self,
        question: str,
        facts: SqlFacts,
        *,
        memory_examples: tuple[VerifiedQueryExample, ...],
        generation_path: str,
        semantic_object_status: str | None,
    ) -> Iterable[VerificationSignal | None]:
        top_n = requested_top_n(question)
        groupings = requested_grouping_terms(question)
        has_agg = bool(facts.aggregate_functions) or has_aggregation_cue(question)
        if groupings and has_agg and not facts.has_group_by and not facts.has_having:
            yield _signal(
                VerificationSignalCode.REQUESTED_GROUPING_MISSING,
                VerificationSeverity.HARD,
                "grouping_terms",
                ",".join(groupings),
            )
        if top_n is not None and (not facts.has_order_by or not facts.has_limit):
            yield _signal(
                VerificationSignalCode.TOP_N_STRUCTURE_MISSING,
                VerificationSeverity.HARD,
                "requested_n",
                str(top_n),
            )
        if (
            top_n is not None
            and facts.has_limit
            and facts.limit_value is not None
            and facts.limit_value != top_n
        ):
            yield _signal(
                VerificationSignalCode.TOP_N_STRUCTURE_MISSING,
                VerificationSeverity.HARD,
                "limit_value",
                str(facts.limit_value),
            )
        if top_n is not None and not facts.has_order_by:
            yield _signal(VerificationSignalCode.TOP_N_WITHOUT_ORDER, VerificationSeverity.HARD)
        if top_n is not None and not facts.has_limit:
            yield _signal(VerificationSignalCode.TOP_N_LIMIT_MISSING, VerificationSeverity.HARD)
        if (
            has_threshold_cue(question)
            and facts.has_group_by
            and facts.aggregate_functions
            and not facts.has_having
            and not facts.has_subquery
        ):
            yield _signal(
                VerificationSignalCode.AGGREGATE_THRESHOLD_FILTER_MISSING, VerificationSeverity.HARD
            )
        if (
            has_average_cue(question)
            and facts.aggregate_functions
            and "AVG" not in facts.aggregate_functions
        ):
            yield _signal(
                VerificationSignalCode.POSSIBLE_AGGREGATION_MISMATCH,
                VerificationSeverity.SOFT,
                "expected",
                "AVG",
            )
        if (
            has_total_cue(question)
            and facts.aggregate_functions
            and "SUM" not in facts.aggregate_functions
        ):
            yield _signal(
                VerificationSignalCode.POSSIBLE_AGGREGATION_MISMATCH,
                VerificationSeverity.SOFT,
                "expected",
                "SUM",
            )
        if (
            has_distinct_entity_cue(question)
            and facts.has_join
            and facts.has_non_distinct_count
            and not facts.has_distinct_count
        ):
            yield _signal(
                VerificationSignalCode.POSSIBLE_ENTITY_COUNT_FANOUT, VerificationSeverity.HARD
            )
        if self._aggregate_fanout_risk(facts):
            yield _signal(
                VerificationSignalCode.POSSIBLE_AGGREGATE_FANOUT, VerificationSeverity.HARD
            )
        for grouping in groupings:
            dimension = _dimension_for_term(grouping)
            if dimension and facts.has_group_by and not _dimension_projected(dimension, facts):
                yield _signal(
                    VerificationSignalCode.REQUESTED_DIMENSION_NOT_PROJECTED,
                    VerificationSeverity.HARD,
                    "dimension",
                    dimension,
                )
        if groupings and has_aggregation_cue(question) and not facts.aggregate_functions:
            yield _signal(
                VerificationSignalCode.REQUESTED_MEASURE_NOT_PROJECTED, VerificationSeverity.SOFT
            )
        if generation_path == "DIRECT_SQL" and _mentions_governed_metric(
            question, self.governed_metric_names
        ):
            yield _signal(
                VerificationSignalCode.GOVERNED_METRIC_SHOULD_USE_SEMANTIC_PATH,
                VerificationSeverity.HARD,
            )
        if semantic_object_status in {"RETIRED", "INVALID"}:
            yield _signal(
                VerificationSignalCode.RETIRED_OR_INVALID_SEMANTIC_OBJECT, VerificationSeverity.HARD
            )
        if self._example_overtransfer(question, facts, memory_examples):
            yield _signal(
                VerificationSignalCode.POSSIBLE_EXAMPLE_OVERTRANSFER, VerificationSeverity.SOFT
            )
        if self._explicit_entity_missing(question, facts):
            yield _signal(
                VerificationSignalCode.EXPLICIT_ENTITY_NOT_GROUNDED, VerificationSeverity.HARD
            )
        if _asks_for_multiple_outputs(question) and facts.projection_count < 2:
            yield _signal(
                VerificationSignalCode.OUTPUT_SHAPE_SUSPICIOUS,
                VerificationSeverity.SOFT,
                "projection_count",
                str(facts.projection_count),
            )
        if facts.has_cross_join or facts.has_unqualified_join:
            if not any(
                term in normalized_question(question)
                for term in ("all combinations", "every combination", "cartesian")
            ):
                yield _signal(
                    VerificationSignalCode.UNEXPECTED_CARTESIAN_JOIN, VerificationSeverity.HARD
                )
        status = explicit_status_filter(question)
        if status and not _status_predicate_present(status, facts):
            yield _signal(
                VerificationSignalCode.EXPLICIT_FILTER_MISSING,
                VerificationSeverity.HARD,
                "status",
                status,
            )
        if facts.has_limit and not has_explicit_ordering_cue(question) and top_n is None:
            yield _signal(VerificationSignalCode.UNREQUESTED_LIMIT, VerificationSeverity.SOFT)

    def _aggregate_fanout_risk(self, facts: SqlFacts) -> bool:
        if not facts.has_join or not facts.aggregate_columns:
            return False
        child_to_parent = {
            "customers": "orders",
            "orders": "order_items",
            "payments": "orders",
            "refunds": "orders",
        }
        for key in facts.aggregate_columns:
            table = key.split(".", 1)[0]
            parent = child_to_parent.get(table)
            if parent and parent in facts.tables:
                return True
        return False

    def _explicit_entity_missing(self, question: str, facts: SqlFacts) -> bool:
        tables = facts.tables
        capable_tables = {
            "regions": {"regions", "customers", "sales_representatives"},
            "representatives": {"sales_representatives", "orders"},
            "customers": {"customers", "orders", "order_items", "payments"},
            "products": {"products", "order_items"},
            "orders": {"orders", "order_items", "payments", "refunds"},
            "payments": {"payments"},
            "refunds": {"refunds"},
            "order items": {"order_items"},
        }
        for entity in explicit_entities(question):
            compatible = capable_tables[entity]
            if not tables & compatible:
                return True
        return False

    @staticmethod
    def _example_overtransfer(
        question: str,
        facts: SqlFacts,
        examples: tuple[VerifiedQueryExample, ...],
    ) -> bool:
        if not examples:
            return False
        question_tokens = set(normalized_question(question).replace("?", "").split())
        for example in examples:
            example_facts = extract_sql_facts(example.sql)
            if not example_facts.tables - facts.tables and example_facts.limit_value is not None:
                if (
                    requested_top_n(question) is None
                    and facts.limit_value == example_facts.limit_value
                ):
                    return True
            example_literals = {
                literal.this.lower()
                for literal in example_facts.tree.find_all(exp.Literal)
                if not literal.is_int and isinstance(literal.this, str)
            }
            if any(value not in question_tokens for value in example_literals):
                return False
        return False


def _signal(
    code: VerificationSignalCode,
    severity: VerificationSeverity,
    key: str | None = None,
    value: str | None = None,
) -> VerificationSignal:
    evidence = ((key, value),) if key is not None and value is not None else ()
    return VerificationSignal(code=code, severity=severity, evidence=evidence)


def _default_schema_catalog() -> SchemaCatalog:
    from app.catalog.default import build_default_catalog

    return build_default_catalog(Base.metadata)


def _dimension_for_term(term: str) -> str | None:
    value = term.strip()
    mapping = {
        "region": "region",
        "regions": "region",
        "customer": "customer",
        "customers": "customer",
        "product": "product_category",
        "products": "product_category",
        "product category": "product_category",
        "status": None,
        "order status": "order_status",
        "payment status": "payment_status",
        "currency": "order_currency",
        "order currency": "order_currency",
        "refund reason": "refund_reason",
        "reason": "refund_reason",
        "representative": "sales_representative",
        "sales representative": "sales_representative",
    }
    return mapping.get(value)


def _dimension_projected(dimension: str, facts: SqlFacts) -> bool:
    requirements = {
        "region": ({"regions", "customers", "sales_representatives"}, {"name", "region_id"}),
        "customer": ({"customers"}, {"name", "id", "customer_id"}),
        "product_category": ({"products"}, {"category"}),
        "sales_representative": ({"sales_representatives"}, {"name", "id", "sales_rep_id"}),
        "order_status": ({"orders"}, {"status"}),
        "order_currency": ({"orders"}, {"currency"}),
        "payment_status": ({"payments"}, {"status"}),
        "refund_reason": ({"refunds"}, {"reason"}),
    }
    tables, columns = requirements.get(dimension, (set(), set()))
    return any(
        (len(parts) == 2 and parts[0] in tables and parts[1] in columns)
        or (len(parts) == 1 and parts[0] in columns)
        for key in facts.projected_columns
        for parts in (key.split(".", 1),)
    )


def _status_predicate_present(status: str, facts: SqlFacts) -> bool:
    return any(key.endswith(".status") for key in facts.predicate_columns)


def _asks_for_multiple_outputs(question: str) -> bool:
    value = normalized_question(question)
    return any(
        marker in value
        for marker in (
            " and total ",
            " and count ",
            " and average ",
            "region and",
            "customer and",
        )
    )


def _mentions_governed_metric(question: str, metric_names: frozenset[str]) -> bool:
    value = normalized_question(question)
    return any(metric.replace("_", " ") in value or metric in value for metric in metric_names)
