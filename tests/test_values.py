import pytest

from core.values import MissingValueError, to_bool, to_float, to_int


def test_zero_is_not_replaced_by_default():
    assert to_float(0, default=0.85) == 0.0
    assert to_int("0", default=5) == 0


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_missing_uses_default_or_raises(missing):
    assert to_float(missing, default=1.5) == 1.5
    with pytest.raises(MissingValueError):
        to_int(missing)


def test_parses_strings():
    assert to_float("2,5") == 2.5
    assert to_int(" 7 ") == 7
    assert to_int(2.6) == 3


def test_garbage_is_an_error_not_zero():
    with pytest.raises(ValueError):
        to_float("abc", default=0)


@pytest.mark.parametrize("value, expected", [
    (True, True), (False, False), (1, True), (0, False),
    ("да", True), ("false", False), ("0", False), ("нет", False), ("", False),
])
def test_to_bool(value, expected):
    assert to_bool(value) is expected
