"""Shopping cart helpers."""


def apply_discount(price, percent):
    """Return the price after a percent discount. percent must be between 0 and 100."""
    if percent < 0 or percent > 100:
        raise ValueError("percent must be between 0 and 100")
    return price - price * percent


def cart_total(items):
    """items is a list of (price, quantity) pairs. Returns the total, rounded to 2 decimals."""
    total = 0
    for price, quantity in items:
        total += price
    return round(total, 2)


def most_expensive(items):
    """Return the highest unit price in the cart, or None if the cart is empty."""
    return max(price for price, _ in items)
