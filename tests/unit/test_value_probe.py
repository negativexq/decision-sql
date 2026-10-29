from app.catalog.models import ColumnMetadata, SchemaCatalog, Sensitivity, TableMetadata
from app.sql.models import UnmatchedValue
from app.sql.parser import SQLParser
from app.sql.values import ValueProbe, _key


def _catalog() -> SchemaCatalog:
    return SchemaCatalog(
        tables=(
            TableMetadata(
                name="powers",
                description="Powers.",
                columns=(
                    ColumnMetadata(name="id", type="integer", description="Key."),
                    ColumnMetadata(name="power_name", type="varchar(100)", description="Name."),
                    ColumnMetadata(
                        name="tier",
                        type="text",
                        description="Tier.",
                        value_domain=("Low", "High"),
                    ),
                    ColumnMetadata(
                        name="holder",
                        type="text",
                        description="Holder.",
                        sensitivity=Sensitivity.CONFIDENTIAL,
                    ),
                    ColumnMetadata(name="code", type="integer", description="Code."),
                ),
            ),
        )
    )


class FakeLookup:
    def __init__(self, stored: dict[tuple[str, str], tuple[str, ...]]) -> None:
        self.values = stored
        self.spelling_calls: list[str] = []

    def stored(self, target: tuple[str, str], value: str) -> bool:
        return value in self.values.get(target, ())

    def spellings(self, target: tuple[str, str], key: str) -> tuple[str, ...]:
        self.spelling_calls.append(key)
        return tuple(value for value in self.values.get(target, ()) if _key(value) == key)[:2]


def _probe(sql: str, lookup: FakeLookup):
    return ValueProbe(_catalog()).probe(SQLParser().parse(sql).expression, lookup)


def test_a_literal_stored_in_one_other_spelling_is_rewritten() -> None:
    lookup = FakeLookup({("powers", "power_name"): ("Stealth", "Flight")})
    result = _probe("SELECT COUNT(*) FROM powers p WHERE p.power_name = 'stealth'", lookup)

    assert "'Stealth'" in result.expression.sql(dialect="postgres")
    assert result.changes == (
        "The filter on powers.power_name compared it with 'stealth'; "
        "the column stores that value as 'Stealth'.",
    )
    assert result.unmatched == () and result.checked == 1


def test_a_literal_with_no_stored_spelling_or_several_is_reported_not_guessed() -> None:
    lookup = FakeLookup({("powers", "power_name"): ("Super Speed", "super-speed", "Flight")})
    result = _probe(
        "SELECT id FROM powers WHERE power_name IN ('super speed', 'Telepathy', 'Flight')", lookup
    )

    assert result.changes == ()
    assert result.unmatched == (
        UnmatchedValue(table="powers", column="power_name", literal="super speed", shared=False),
        UnmatchedValue(table="powers", column="power_name", literal="Telepathy", shared=False),
    )


def test_a_punctuation_only_literal_is_never_matched_by_its_empty_form() -> None:
    lookup = FakeLookup({("powers", "power_name"): ("+", "negative")})
    result = _probe("SELECT id FROM powers WHERE power_name = '-'", lookup)

    assert lookup.spelling_calls == []
    assert [item.literal for item in result.unmatched] == ["-"]


def test_known_confidential_and_non_text_columns_are_not_probed() -> None:
    probe = ValueProbe(_catalog())
    for sql in (
        "SELECT id FROM powers WHERE tier = 'low'",
        "SELECT id FROM powers WHERE holder = 'someone'",
        "SELECT id FROM powers WHERE code = '7'",
    ):
        assert probe.targets(SQLParser().parse(sql).expression) == [], sql


def test_a_code_of_digits_matches_its_zero_padded_spelling() -> None:
    assert _key("01611430131177") == _key("1611430131177") == "1611430131177"
    assert _key("000") == "0" and _key("Item 03") == "item03"
    lookup = FakeLookup({("powers", "power_name"): ("01611430131177",)})
    result = _probe("SELECT id FROM powers WHERE power_name = '1611430131177'", lookup)
    assert "'01611430131177'" in result.expression.sql(dialect="postgres")
