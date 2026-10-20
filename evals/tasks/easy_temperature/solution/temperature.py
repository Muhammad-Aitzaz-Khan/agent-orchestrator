"""Temperature conversions."""


def celsius_to_fahrenheit(celsius):
    return celsius * 9 / 5 + 32


def fahrenheit_to_celsius(fahrenheit):
    return (fahrenheit - 32) * 5 / 9


def is_freezing(celsius):
    """True when water freezes: at or below 0 degrees Celsius."""
    return celsius <= 0
