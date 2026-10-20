import pytest

from bank import Account, InsufficientFunds


def test_deposit():
    acc = Account("ali")
    acc.deposit(50)
    assert acc.balance == 50


def test_withdraw():
    acc = Account("ali", balance=100)
    acc.withdraw(30)
    assert acc.balance == 70


def test_withdraw_too_much():
    acc = Account("ali", balance=50)
    with pytest.raises(InsufficientFunds):
        acc.withdraw(80)


def test_negative_deposit():
    with pytest.raises(ValueError):
        Account("ali").deposit(-5)


def test_transfer():
    a = Account("ali", balance=100)
    b = Account("sara", balance=10)
    a.transfer(b, 40)
    assert a.balance == 60
    assert b.balance == 50
