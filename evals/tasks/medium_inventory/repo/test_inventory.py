import pytest

from inventory import Inventory


@pytest.fixture
def shop():
    inv = Inventory()
    inv.add("pen", 2.0, 100)
    inv.add("notebook", 1.0, 200)
    return inv


def test_add_existing_item_increases_stock(shop):
    shop.add("pen", 2.5, 20)
    assert shop.stock("pen") == 120


def test_remove(shop):
    shop.remove("pen", 30)
    assert shop.stock("pen") == 70


def test_remove_too_many(shop):
    with pytest.raises(ValueError):
        shop.remove("pen", 500)


def test_small_order_cost(shop):
    assert shop.order_cost("pen", 5) == 11.0


def test_order_with_5_percent_discount(shop):
    assert shop.order_cost("pen", 10) == 20.9


def test_order_with_10_percent_discount(shop):
    assert shop.order_cost("notebook", 50) == 49.5
