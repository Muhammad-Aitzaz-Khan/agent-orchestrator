"""A small calculator module with a few bugs for the agent to fix."""


def add(a, b):
    return a + b


def divide(a, b):
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b


def average(numbers):
    if not numbers:
        return 0.0
    return sum(numbers) / len(numbers)


def is_even(n):
    return n % 2 == 0
