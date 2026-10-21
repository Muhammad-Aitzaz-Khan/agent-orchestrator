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

