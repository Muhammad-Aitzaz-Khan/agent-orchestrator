import pytest

from calculator import add, average, divide, is_even


def test_add():
    assert add(2, 3) == 5


def test_add_negative():
    assert add(-1, -1) == -2


def test_divide():
    assert divide(7, 2) == 3.5


def test_divide_by_zero():
    with pytest.raises(ValueError):
        divide(1, 0)


def test_average():
    assert average([2, 4, 6]) == 4.0


def test_average_empty():
    assert average([]) == 0.0


def test_is_even():
    assert is_even(4)
    assert not is_even(7)
