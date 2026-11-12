from decimal import Decimal

import pytest

from app.bank import models
from app.bank.account import Account
from app.bank.errors import (
    ConfirmationNotAuthorizedError,
    InsufficientBalanceError,
    InvalidAmountError,
    InvalidRecipientError,
    NoPendingOperationError,
)


def complete_transfer(account: Account, amount, recipient="conta da Ana"):
    """Simulates the real flow: prepare in one user turn, confirm in the next."""
    account.start_user_turn()
    summary = account.prepare_transfer(amount, recipient)
    account.start_user_turn()
    return account.confirm_transfer(summary["operation_id"])


def test_initial_balance():
    account = Account()
    assert account.balance == Decimal("1000.00")
    assert account.get_balance()["balance"] == "R$ 1.000,00"


def test_deposit():
    account = Account()
    result = account.deposit("250")
    assert result["new_balance"] == "R$ 1.250,00" and result["previous_balance"] == "R$ 1.000,00"
    assert account.balance == Decimal("1250.00")


@pytest.mark.parametrize("amount", ["-50", "0", "abc", "10.999"])
def test_invalid_deposit_does_not_change_balance(amount):
    account = Account()
    with pytest.raises(InvalidAmountError):
        account.deposit(amount)
    assert account.balance == Decimal("1000.00") and account.statement == []


def test_transfer_takes_two_steps():
    account = Account()
    account.start_user_turn()
    summary = account.prepare_transfer("100", "conta da Ana")
    assert summary["status"] == "AWAITING_USER_CONFIRMATION"
    assert account.balance == Decimal("1000.00"), "preparing must NOT debit"
    account.start_user_turn()
    receipt = account.confirm_transfer(summary["operation_id"])
    assert receipt["new_balance"] == "R$ 900,00" and receipt["recipient"] == "conta da Ana"
    assert account.pending is None


def test_confirmation_in_the_same_turn_is_blocked():
    account = Account()
    account.start_user_turn()
    summary = account.prepare_transfer("100", "Ana")
    with pytest.raises(ConfirmationNotAuthorizedError):
        account.confirm_transfer(summary["operation_id"])
    assert account.balance == Decimal("1000.00")


def test_transfer_above_balance_is_rejected():
    account = Account()
    with pytest.raises(InsufficientBalanceError):
        account.prepare_transfer("5000", "Ana")
    assert account.balance == Decimal("1000.00") and account.pending is None


def test_transferring_the_whole_balance_zeroes_it_without_going_negative():
    account = Account()
    complete_transfer(account, "1000")
    assert account.balance == Decimal("0.00")
    with pytest.raises(InsufficientBalanceError):
        account.prepare_transfer("0.01", "Ana")


def test_a_new_pending_transfer_replaces_the_previous_one():
    account = Account()
    account.start_user_turn()
    first = account.prepare_transfer("800", "Ana")
    second = account.prepare_transfer("900", "Bruno")
    account.start_user_turn()
    with pytest.raises(NoPendingOperationError):
        account.confirm_transfer(first["operation_id"])
    account.confirm_transfer(second["operation_id"])
    assert account.balance == Decimal("100.00")


def test_confirming_without_pending_or_with_a_wrong_id():
    account = Account()
    account.start_user_turn()
    with pytest.raises(NoPendingOperationError):
        account.confirm_transfer("TRF-XXXXXX")
    account.prepare_transfer("10", "Ana")
    account.start_user_turn()
    with pytest.raises(NoPendingOperationError):
        account.confirm_transfer("TRF-INVENTED")


def test_cancel():
    account = Account()
    account.start_user_turn()
    summary = account.prepare_transfer("10", "Ana")
    account.cancel_transfer(summary["operation_id"])
    assert account.pending is None and account.balance == Decimal("1000.00")


@pytest.mark.parametrize("recipient", ["", "   ", None, "minha conta", "para mim", "x" * 61, "<script>"])
def test_invalid_recipient(recipient):
    account = Account()
    with pytest.raises(InvalidRecipientError):
        account.prepare_transfer("10", recipient)


def test_decimal_precision():
    account = Account()
    account.deposit("0.10")
    account.deposit("0.20")
    assert account.balance == Decimal("1000.30")
    assert account.get_balance()["balance"] == "R$ 1.000,30"


def test_operation_sequence_keeps_the_statement_invariant():
    account = Account()
    account.deposit("250")  # 1250.00
    complete_transfer(account, "100")  # 1150.00
    account.deposit("49,99")  # 1199.99
    complete_transfer(account, "1199.98")  # 0.01
    account.deposit("1.000,00")  # 1000.01
    assert account.balance == Decimal("1000.01")
    deposits = sum(t.amount for t in account.statement if t.type == models.DEPOSIT)
    transfers = sum(t.amount for t in account.statement if t.type == models.TRANSFER)
    assert Decimal("1000.00") + deposits - transfers == account.balance
    assert len(account.statement) == 5
