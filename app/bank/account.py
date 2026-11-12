"""Fictitious bank account: ALL business rules live here.

The language model never changes the balance directly: through tools it only
asks this class to run an operation. The class validates everything and is the
single source of truth for balance and statement.

Transfers are two-step, and the rule is enforced in code:
  1. `prepare_transfer` validates and creates a pending operation (no debit);
  2. `confirm_transfer` is only accepted in a LATER user turn, i.e. the user
     must have sent a new message after seeing the summary. This stops the model
     from "self-confirming" inside the same reply.
"""

from __future__ import annotations

import threading
import uuid
from decimal import Decimal
from typing import Any

from app import messages
from app.bank import models
from app.bank.errors import (
    ConfirmationNotAuthorizedError,
    InsufficientBalanceError,
    NoPendingOperationError,
)
from app.bank.models import PendingTransfer, Transaction
from app.bank.money import format_brl, parse_amount
from app.bank.recipient import validate_recipient
from app.config import INITIAL_BALANCE

ToolResult = dict[str, Any]


class Account:
    def __init__(self, initial_balance: Decimal = INITIAL_BALANCE) -> None:
        self._initial_balance = parse_amount(initial_balance)
        self._balance = self._initial_balance
        self._statement: list[Transaction] = []
        self._pending: PendingTransfer | None = None
        self._turn = 0
        self._lock = threading.RLock()

    # ---- read ----
    @property
    def balance(self) -> Decimal:
        return self._balance

    @property
    def pending(self) -> PendingTransfer | None:
        return self._pending

    @property
    def statement(self) -> list[Transaction]:
        return list(self._statement)

    def start_user_turn(self) -> int:
        """Called by the agent whenever a NEW user message arrives."""
        with self._lock:
            self._turn += 1
            return self._turn

    def get_balance(self) -> ToolResult:
        with self._lock:
            return {"ok": True, "account": messages.ACCOUNT_LABEL, "balance": format_brl(self._balance)}

    # ---- deposit ----
    def deposit(self, amount: object) -> ToolResult:
        with self._lock:
            value = parse_amount(amount)
            previous = self._balance
            self._balance = previous + value
            self._statement.append(Transaction(models.DEPOSIT, value, self._balance))
            self._check_invariants()
            return {
                "ok": True,
                "operation": models.DEPOSIT,
                "deposited_amount": format_brl(value),
                "previous_balance": format_brl(previous),
                "new_balance": format_brl(self._balance),
            }

    # ---- transfer, step 1 ----
    def prepare_transfer(self, amount: object, recipient: object) -> ToolResult:
        with self._lock:
            value = parse_amount(amount)
            name = validate_recipient(recipient)
            self._require_funds(value)
            self._pending = PendingTransfer(f"TRF-{uuid.uuid4().hex[:6].upper()}", value, name, self._turn)
            return self._transfer_summary(self._pending)

    def _require_funds(self, value: Decimal) -> None:
        if value > self._balance:
            raise InsufficientBalanceError(messages.insufficient_balance(format_brl(value), format_brl(self._balance)))

    def _transfer_summary(self, pending: PendingTransfer) -> ToolResult:
        return {
            "ok": True,
            "status": "AWAITING_USER_CONFIRMATION",
            "operation_id": pending.operation_id,
            "origin": messages.ACCOUNT_LABEL,
            "recipient": pending.recipient,
            "amount": format_brl(pending.amount),
            "current_balance": format_brl(self._balance),
            "balance_after_transfer": format_brl(self._balance - pending.amount),
            # Read by the model: it must not confirm on its own (see class docstring).
            "instruction": "Show this summary and ask the user to reply 'confirmo' or 'cancelar'. "
            "Do NOT confirm on your own.",
        }

    # ---- transfer, step 2 ----
    def confirm_transfer(self, operation_id: object) -> ToolResult:
        with self._lock:
            pending = self._require_pending(operation_id)
            self._require_new_user_turn(pending)
            self._require_funds_at_confirmation(pending)
            previous = self._balance
            self._apply_transfer(pending)
            return self._transfer_receipt(pending, previous)

    def _require_new_user_turn(self, pending: PendingTransfer) -> None:
        if self._turn <= pending.created_in_turn:
            raise ConfirmationNotAuthorizedError(messages.CONFIRMATION_REQUIRES_NEW_MESSAGE)

    def _require_funds_at_confirmation(self, pending: PendingTransfer) -> None:
        """Re-validate: the balance may have changed since the summary was shown."""
        if pending.amount <= self._balance:
            return
        self._pending = None
        raise InsufficientBalanceError(
            messages.insufficient_balance_at_confirmation(format_brl(self._balance), format_brl(pending.amount))
        )

    def _apply_transfer(self, pending: PendingTransfer) -> None:
        self._balance -= pending.amount
        self._statement.append(Transaction(models.TRANSFER, pending.amount, self._balance, pending.recipient))
        self._pending = None
        self._check_invariants()

    def _transfer_receipt(self, pending: PendingTransfer, previous: Decimal) -> ToolResult:
        return {
            "ok": True,
            "operation": models.TRANSFER,
            "status": "COMPLETED",
            "operation_id": pending.operation_id,
            "origin": messages.ACCOUNT_LABEL,
            "recipient": pending.recipient,
            "transferred_amount": format_brl(pending.amount),
            "previous_balance": format_brl(previous),
            "new_balance": format_brl(self._balance),
        }

    def cancel_transfer(self, operation_id: object) -> ToolResult:
        with self._lock:
            pending = self._require_pending(operation_id)
            self._pending = None
            return {
                "ok": True,
                "status": "CANCELLED",
                "operation_id": pending.operation_id,
                "balance": format_brl(self._balance),
            }

    def _require_pending(self, operation_id: object) -> PendingTransfer:
        pending = self._pending
        if pending is None:
            raise NoPendingOperationError(messages.NO_PENDING_TRANSFER)
        if not isinstance(operation_id, str) or operation_id.strip().upper() != pending.operation_id:
            raise NoPendingOperationError(messages.wrong_operation_id(pending.operation_id))
        return pending

    # ---- integrity ----
    def _check_invariants(self) -> None:
        """Defense in depth: the balance must ALWAYS match the statement."""
        deposits = self._sum_of(models.DEPOSIT)
        transfers = self._sum_of(models.TRANSFER)
        expected = self._initial_balance + deposits - transfers
        # if/raise instead of assert: asserts vanish when Python runs with -O.
        if self._balance != expected:
            raise RuntimeError(f"balance inconsistency: balance={self._balance}, expected={expected}")
        if self._balance < 0:
            raise RuntimeError(f"negative balance detected: {self._balance}")

    def _sum_of(self, transaction_type: str) -> Decimal:
        return sum((t.amount for t in self._statement if t.type == transaction_type), Decimal("0"))

    # ---- persistence ----
    def to_dict(self) -> dict[str, Any]:
        """Serialize the state (Decimals as text, never floats)."""
        with self._lock:
            return {
                "initial_balance": str(self._initial_balance),
                "balance": str(self._balance),
                "turn": self._turn,
                "statement": [t.to_storage() for t in self._statement],
                "pending": self._pending.to_storage() if self._pending else None,
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Account:
        """Rebuild the account and VALIDATE its consistency: a tampered or corrupt
        file (balance not matching the statement, negative...) is rejected."""
        account = cls(Decimal(data["initial_balance"]))
        account._balance = Decimal(data["balance"])
        account._turn = int(data.get("turn", 0))
        account._statement = [Transaction.from_storage(t) for t in data.get("statement", [])]
        account._pending = cls._restore_pending(data.get("pending"))
        account._check_invariants()
        return account

    @staticmethod
    def _restore_pending(data: dict[str, Any] | None) -> PendingTransfer | None:
        if not data:
            return None
        return PendingTransfer(
            data["operation_id"],
            parse_amount(data["amount"]),
            validate_recipient(data["recipient"]),
            int(data["created_in_turn"]),
        )

    def snapshot(self) -> dict[str, Any]:
        """State sent to the frontend; always comes from here, never from model text."""
        with self._lock:
            return {
                "account": messages.ACCOUNT_LABEL,
                "balance": format_brl(self._balance),
                "statement": [t.to_public() for t in reversed(self._statement)],
                "pending": self._pending.to_public() if self._pending else None,
            }
