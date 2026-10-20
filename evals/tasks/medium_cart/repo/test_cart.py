import pytest

from cart import apply_discount, cart_total, most_expensive


def test_discount():
    assert apply_discount(200, 25) == 150


def test_discount_zero():
    assert apply_discount(80, 0) == 80


def test_discount_invalid():
    with pytest.raises(ValueError):
        apply_discount(100, 150)


def test_cart_total():
    assert cart_total([(2.5, 4), (10, 1)]) == 20.0


def test_cart_total_empty():
    assert cart_total([]) == 0


def test_most_expensive():
    assert most_expensive([(3, 1), (9, 2), (4, 5)]) == 9


def test_most_expensive_empty():
    assert most_expensive([]) is None
