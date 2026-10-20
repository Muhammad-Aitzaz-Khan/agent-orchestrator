"""Sales reporting."""


def monthly_total(sales, year, month):
    """Total sales for one calendar month, rounded to 2 decimals.

    sales is a list of (datetime.date, amount) pairs. Refunds are negative amounts
    and must be included in the total.
    """
    total = 0
    for day, amount in sales:
        if day.month == month and amount > 0:
            total += amount
    return round(total, 2)


def best_month(sales, year):
    """Return the month number (1-12) with the highest total in that year, or None if there
    were no sales that year."""
    totals = {}
    for day, amount in sales:
        if day.year == year:
            totals[day.month] = totals.get(day.month, 0) + amount
    return max(totals, key=totals.get) if totals else None
