from datetime import date

from report import best_month, monthly_total

SALES = [
    (date(2025, 3, 5), 100.0),
    (date(2026, 3, 1), 250.0),
    (date(2026, 3, 20), 50.5),
    (date(2026, 4, 2), 80.0),
]


def test_monthly_total():
    assert monthly_total(SALES, 2026, 4) == 80.0


def test_monthly_total_only_counts_that_year():
    assert monthly_total(SALES, 2026, 3) == 300.5


def test_best_month():
    assert best_month(SALES, 2026) == 3


def test_best_month_no_sales():
    assert best_month(SALES, 2020) is None
