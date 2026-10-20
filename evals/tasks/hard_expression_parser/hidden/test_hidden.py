import pytest

from calc_parser import evaluate


def test_unary_minus():
    assert evaluate("-3") == -3
    assert evaluate("2 * -(1 + 1)") == -4
    assert evaluate("1 - -1") == 2


def test_long_chain():
    assert evaluate("100 - 20 - 30 + 5 - 5") == 50


def test_invalid_character():
    with pytest.raises(ValueError):
        evaluate("4 $ 2")


def test_unclosed_parenthesis():
    with pytest.raises(ValueError):
        evaluate("(1 + 2")
