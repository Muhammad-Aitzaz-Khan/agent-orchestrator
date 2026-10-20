import pytest

from calc_parser import evaluate


def test_simple():
    assert evaluate("1 + 2") == 3


def test_precedence():
    assert evaluate("2 * 3 + 4") == 10
    assert evaluate("2 * (3 + 4)") == 14


def test_multi_digit_numbers():
    assert evaluate("12 + 30") == 42


def test_subtraction_is_left_to_right():
    assert evaluate("10 - 3 - 2") == 5


def test_division():
    assert evaluate("8 / 4 / 2") == 1.0


def test_incomplete_expression():
    with pytest.raises(ValueError):
        evaluate("2 +")
