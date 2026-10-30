from app.execution.reader import error_class, unique_columns


class _DriverError(Exception):
    def __init__(self, sqlstate: str | None) -> None:
        super().__init__("value 'x7' is secret")
        self.sqlstate = sqlstate


class _Wrapped(Exception):
    def __init__(self, orig: Exception) -> None:
        super().__init__("wrapper")
        self.orig = orig


def test_only_fixable_sqlstates_get_a_name() -> None:
    assert error_class(_Wrapped(_DriverError("22P02"))) == "INVALID_TEXT_CONVERSION"
    assert error_class(_DriverError("22012")) == "DIVISION_BY_ZERO"
    assert error_class(_Wrapped(_DriverError("42501"))) is None  # permission denied stays unnamed
    assert error_class(_Wrapped(_DriverError(None))) is None
    assert error_class(ValueError("no state")) is None


def test_repeated_result_columns_get_distinct_names() -> None:
    assert unique_columns(["status", "sum", "sum"]) == ["status", "sum", "sum_2"]
    # A later column already called sum_2 keeps its name; the repeat moves on.
    assert unique_columns(["sum", "sum", "sum_2"]) == ["sum", "sum_3", "sum_2"]
    assert unique_columns(["a", "b"]) == ["a", "b"]
