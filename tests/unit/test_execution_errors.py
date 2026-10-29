from app.execution.reader import error_class


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
