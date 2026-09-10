from unittest.mock import Mock

from sqlglot import exp

from app.catalog.models import ColumnMetadata, SchemaCatalog, TableMetadata
from app.sql.authority import ExecutionAuthority, external_relations, validate_authority
from app.sql.models import CandidateSource, SqlCandidate, SqlPlanFailure, SqlSafetyStatus
from app.sql.parser import SQLParser
from app.sql.service import SqlSafetyService


def parsed(sql: str) -> exp.Expression:
    return SQLParser().parse(sql).expression


def catalog(*table_names: str) -> SchemaCatalog:
    return SchemaCatalog(
        tables=tuple(
            TableMetadata(
                name=name,
                description=f"Synthetic {name} table.",
                columns=(
                    ColumnMetadata(name="id", type="INTEGER", description=f"{name} id."),
                    ColumnMetadata(
                        name="subscriber_id",
                        type="INTEGER",
                        description=f"{name} subscriber id.",
                    ),
                ),
            )
            for name in table_names
        )
    )


def authority(*table_names: str) -> ExecutionAuthority:
    return ExecutionAuthority.from_catalog(catalog(*table_names))


def test_authorized_single_relation_passes() -> None:
    rejection = validate_authority(
        parsed("SELECT id FROM allowed_table"), authority("allowed_table")
    )
    assert rejection is None


def test_unauthorized_single_relation_is_rejected_before_database_connection() -> None:
    engine = Mock()
    service = SqlSafetyService(
        engine,
        catalog=catalog("allowed_table", "forbidden_table"),
    )

    result = service.plan(
        SqlCandidate(
            sql="SELECT id FROM forbidden_table",
            execution_authority=authority("allowed_table"),
        )
    )

    assert isinstance(result, SqlPlanFailure)
    assert result.status is SqlSafetyStatus.AUTHORITY_REJECTION
    assert result.failure_stage is not None
    assert result.failure_stage.value == "AUTHORITY_REJECTION"
    assert result.authority_rejection is not None
    assert result.authority_rejection.unauthorized_relations == ("public.forbidden_table",)
    engine.connect.assert_not_called()


def test_model_candidate_without_authority_is_rejected_before_database_connection() -> None:
    engine = Mock()
    service = SqlSafetyService(engine, catalog=catalog("allowed_table"))

    result = service.plan(
        SqlCandidate(sql="SELECT id FROM allowed_table", source=CandidateSource.LLM)
    )

    assert isinstance(result, SqlPlanFailure)
    assert result.status is SqlSafetyStatus.AUTHORITY_REJECTION
    assert result.authority_rejection is not None
    assert result.authority_rejection.code.value == "MISSING_REQUEST_AUTHORITY"
    engine.connect.assert_not_called()


def test_mixed_authorized_and_unauthorized_join_fails_closed() -> None:
    rejection = validate_authority(
        parsed("SELECT a.id FROM allowed_table AS a JOIN forbidden_table AS b ON b.id = a.id"),
        authority("allowed_table"),
    )
    assert rejection is not None
    assert rejection.unauthorized_relations == ("public.forbidden_table",)


def test_unauthorized_nested_subquery_fails_closed() -> None:
    rejection = validate_authority(
        parsed("SELECT id FROM allowed_table WHERE id IN (SELECT id FROM forbidden_table)"),
        authority("allowed_table"),
    )
    assert rejection is not None
    assert rejection.unauthorized_relations == ("public.forbidden_table",)


def test_unauthorized_cte_source_fails_closed() -> None:
    rejection = validate_authority(
        parsed("WITH hidden AS (SELECT id FROM forbidden_table) SELECT id FROM hidden"),
        authority("allowed_table"),
    )
    assert rejection is not None
    assert rejection.unauthorized_relations == ("public.forbidden_table",)


def test_cte_alias_is_not_an_external_dependency() -> None:
    rejection = validate_authority(
        parsed("WITH visible AS (SELECT id FROM allowed_table) SELECT id FROM visible"),
        authority("allowed_table"),
    )
    assert rejection is None
    assert external_relations(
        parsed("WITH visible AS (SELECT id FROM allowed_table) SELECT id FROM visible")
    ) == ("public.allowed_table",)


def test_multiple_authorized_relations_pass() -> None:
    rejection = validate_authority(
        parsed("SELECT a.id FROM allowed_table AS a JOIN second_allowed AS b ON b.id = a.id"),
        authority("allowed_table", "second_allowed"),
    )
    assert rejection is None


def test_schema_qualification_is_part_of_authority_identity() -> None:
    expression = parsed("SELECT id FROM public.allowed_table")
    assert external_relations(expression) == ("public.allowed_table",)
    assert validate_authority(
        expression, ExecutionAuthority(allowed_relations=("other.allowed_table",))
    )


def test_quoted_identifier_uses_structural_identifier_resolution() -> None:
    expression = parsed('SELECT id FROM "allowed_table"')
    assert external_relations(expression) == ("public.allowed_table",)
    assert validate_authority(expression, authority("allowed_table")) is None


def test_telecom_15_mechanism_is_blocked_without_case_specific_runtime_logic() -> None:
    engine = Mock()
    service = SqlSafetyService(
        engine,
        catalog=catalog("subscribers", "external_directory"),
    )
    result = service.plan(
        SqlCandidate(
            sql="SELECT subscriber_id FROM external_directory",
            correlation_id="telecom_15",
            execution_authority=authority("subscribers"),
        )
    )

    assert isinstance(result, SqlPlanFailure)
    assert result.status is SqlSafetyStatus.AUTHORITY_REJECTION
    assert result.authority_rejection is not None
    assert result.authority_rejection.unauthorized_relations == ("public.external_directory",)
    engine.connect.assert_not_called()
