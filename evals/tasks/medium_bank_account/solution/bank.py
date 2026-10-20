"""A simple bank account."""


class InsufficientFunds(Exception):
    """Raised when an account does not have enough money."""


class Account:
    def __init__(self, owner, balance=0, history=None):
        self.owner = owner
        self.balance = balance
        self.history = list(history) if history else []

    def deposit(self, amount):
        """Add money. amount must be positive, otherwise ValueError."""
        if amount <= 0:
            raise ValueError("amount must be positive")
        self.balance += amount
        self.history.append(("deposit", amount))

    def withdraw(self, amount):
        """Remove money. amount must be positive, otherwise ValueError.

        Raises InsufficientFunds if the balance is too low. Overdrafts are not allowed.
        """
        if amount <= 0:
            raise ValueError("amount must be positive")
        if amount > self.balance:
            raise InsufficientFunds(f"{self.owner} cannot withdraw {amount}")
        self.balance -= amount
        self.history.append(("withdraw", amount))

    def transfer(self, other, amount):
        """Move money from this account to other. The same rules as withdraw apply,
        and if the transfer fails neither account changes."""
        self.withdraw(amount)
        other.deposit(amount)
