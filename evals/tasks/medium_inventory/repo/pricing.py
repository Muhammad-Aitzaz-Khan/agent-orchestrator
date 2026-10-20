"""Pricing rules shared by the shop."""

TAX_RATE = 0.10


def with_tax(amount):
    """Return amount plus 10% sales tax, rounded to 2 decimals."""
    return round(amount * TAX_RATE, 2)


def bulk_discount(quantity):
    """Return the discount fraction for an order size.

    50 or more units: 10%. 10 or more units: 5%. Otherwise no discount.
    """
    if quantity > 50:
        return 0.10
    if quantity >= 10:
        return 0.05
    return 0.0
