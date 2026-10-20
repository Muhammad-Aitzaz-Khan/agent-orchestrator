from datetime import date

from report import monthly_total


def test_refunds_reduce_total():
    sales = [(date(2026, 5, 1), 200.0), (date(2026, 5, 9), -45.25)]
    assert monthly_total(sales, 2026, 5) == 154.75


def test_empty_month():
    assert monthly_total([(date(2026, 1, 1), 10.0)], 2026, 2) == 0
