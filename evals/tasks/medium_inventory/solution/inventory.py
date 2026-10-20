"""Stock tracking for a small shop."""
from pricing import bulk_discount, with_tax


class Inventory:
    def __init__(self):
        self.items = {}

    def add(self, name, price, quantity):
        """Add stock. If the item already exists, increase its quantity and use the new price."""
        if name in self.items:
            self.items[name]["quantity"] += quantity
            self.items[name]["price"] = price
        else:
            self.items[name] = {"price": price, "quantity": quantity}

    def remove(self, name, quantity):
        """Remove units. KeyError if the item is unknown, ValueError if there is not enough stock."""
        item = self.items[name]
        if quantity > item["quantity"]:
            raise ValueError(f"only {item['quantity']} {name} left")
        item["quantity"] -= quantity

    def stock(self, name):
        return self.items[name]["quantity"]

    def order_cost(self, name, quantity):
        """Cost of buying quantity units: price times quantity, minus the bulk discount, plus tax."""
        price = self.items[name]["price"]
        subtotal = price * quantity * (1 - bulk_discount(quantity))
        return with_tax(subtotal)
