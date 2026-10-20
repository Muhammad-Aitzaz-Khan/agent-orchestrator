import pytest

from bank import Account, InsufficientFunds


def test_accounts_have_separate_history():
    a = Account("ali")
    b = Account("sara")
    a.deposit(10)
    assert b.history == []


def test_failed_transfer_changes_nothing():
    a = Account("ali", balance=20)
    b = Account("sara", balance=5)
    with pytest.raises(InsufficientFunds):
        a.transfer(b, 50)
    assert a.balance == 20
    assert b.balance == 5


def test_history_records_transfer():
    a = Account("ali", balance=100)
    b = Account("sara")
    a.transfer(b, 25)
    assert a.history[-1] == ("withdraw", 25)
    assert b.history[-1] == ("deposit", 25)
