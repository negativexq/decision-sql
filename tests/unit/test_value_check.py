from app.catalog.models import ColumnMetadata, SchemaCatalog, TableMetadata
from app.config import Settings
from app.decision.service import _repair_feedback
from app.generation.decision_contract import PRODUCTION_DECISION_CASE_ID, ProductionDecision
from app.sql.models import ExplainEstimate, QueryPlan, UnmatchedValue
from app.sql.parser import SQLParser
from app.sql.values import ValueChecker


def _catalog(shared: bool = False) -> SchemaCatalog:
    segments = ("Discount", "Other", "Premium", "Value for money")
    return SchemaCatalog(
        tables=(
            TableMetadata(
                name="stations",
                description="Gas stations.",
                columns=(
                    ColumnMetadata(name="id", type="integer", description="Key."),
                    ColumnMetadata(
                        name="segment",
                        type="text",
                        description="Segment.",
                        value_domain=segments,
                        known_values=segments if shared else (),
                    ),
                    ColumnMetadata(
                        name="status",
                        type="text",
                        description="Status.",
                        value_domain=("open", "OPEN"),
                    ),
                ),
            ),
            TableMetadata(
                name="regions",
                description="Regions.",
                columns=(
                    ColumnMetadata(name="id", type="integer", description="Key."),
                    ColumnMetadata(name="segment", type="text", description="Region segment."),
                ),
            ),
        )
    )


def _check(sql: str, shared: bool = False):
    return ValueChecker(_catalog(shared)).check(SQLParser().parse(sql).expression)


def test_a_literal_differing_only_in_case_or_punctuation_takes_the_stored_spelling() -> None:
    result = _check(
        "SELECT COUNT(*) FROM stations s WHERE s.segment = 'premium' "
        "OR segment IN ('value-for-money', 'Other')"
    )
    sql = result.expression.sql(dialect="postgres")
    assert "'Premium'" in sql and "'Value for money'" in sql and "'premium'" not in sql
    assert len(result.changes) == 2 and result.unmatched == ()
    assert result.checked == 3


def test_a_literal_matching_nothing_is_reported_and_left_alone() -> None:
    result = _check("SELECT id FROM stations WHERE 'Luxury' = segment")
    assert result.changes == ()
    assert result.unmatched == (
        UnmatchedValue(table="stations", column="segment", literal="Luxury", shared=False),
    )
    assert (
        _check("SELECT id FROM stations WHERE segment = 'Luxury'", shared=True).unmatched[0].shared
    )


def test_a_literal_close_to_several_stored_values_is_not_guessed() -> None:
    result = _check("SELECT id FROM stations WHERE status = 'Open'")
    assert result.changes == () and [item.literal for item in result.unmatched] == ["Open"]


def test_columns_the_check_cannot_place_are_skipped() -> None:
    # An unqualified column two joined tables share, a CTE that reuses a table name,
    # and a column with no known values are not checked.
    for sql in (
        "SELECT s.id FROM stations s JOIN regions r ON r.id = s.id WHERE segment = 'premium'",
        "WITH stations AS (SELECT 'x' AS segment) SELECT * FROM stations WHERE segment = 'x'",
        "SELECT r.id FROM regions r WHERE r.segment = 'premium'",
    ):
        result = _check(sql)
        assert result.checked == 0 and result.changes == () and result.unmatched == ()


def _plan(*unmatched: UnmatchedValue) -> QueryPlan:
    return QueryPlan(
        plan_id="00000000-0000-0000-0000-000000000001",
        normalized_sql="SELECT 1",
        statement_type="Select",
        estimate=ExplainEstimate(total_cost=1, plan_rows=1, top_level_node_type="Result"),
        unmatched_values=unmatched,
    )


def test_only_blind_guesses_go_back_to_the_model() -> None:
    previous = ProductionDecision(
        case_id=PRODUCTION_DECISION_CASE_ID, decision="ANSWER", sql="SELECT 1"
    )
    settings = Settings(_env_file=None, reader_role="reader")
    guessed = UnmatchedValue(table="stations", column="segment", literal="Luxury", shared=False)
    feedback = _repair_feedback(_plan(guessed), previous, settings)
    assert feedback is not None and feedback.code == "VALUE_NOT_STORED"
    assert "stations.segment = 'Luxury'" in feedback.message
    # The feedback never names the values the column does store.
    assert "Premium" not in feedback.message
    seen = guessed.model_copy(update={"shared": True})
    assert _repair_feedback(_plan(seen), previous, settings) is None
    assert _repair_feedback(_plan(), previous, settings) is None
