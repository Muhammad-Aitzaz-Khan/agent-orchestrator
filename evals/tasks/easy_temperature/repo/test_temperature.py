import pytest

from temperature import celsius_to_fahrenheit, fahrenheit_to_celsius, is_freezing


def test_boiling_to_fahrenheit():
    assert celsius_to_fahrenheit(100) == 212


def test_freezing_to_fahrenheit():
    assert celsius_to_fahrenheit(0) == 32


def test_boiling_to_celsius():
    assert fahrenheit_to_celsius(212) == 100


def test_body_temperature_to_celsius():
    assert fahrenheit_to_celsius(98.6) == pytest.approx(37)


def test_is_freezing():
    assert is_freezing(-5)
    assert is_freezing(0)
    assert not is_freezing(4)
