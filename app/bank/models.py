"""Plain data objects of the account: statement entries and the pending transfer."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Self

from app.bank.money import format_brl, parse_amount

DEPOSIT = "DEPOSIT"
TRANSFER = "TRANSFER"
TRANSACTION_TYPES = (DEPOSIT, TRANSFER)


def _now_label() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")


@dataclass(frozen=True)
class Transaction:
    type: str  # DEPOSIT | TRANSFER
    amount: Decimal
    balance_after: Decimal
    recipient: str | None = None
    timestamp: str = field(default_factory=_now_label)

    def to_public(self) -> dict[str, Any]:
        """Shape sent to the frontend (money already formatted)."""
        return {
            "type": self.type,
            "amount": format_brl(self.amount),
            "balance_after": format_brl(self.balance_after),
            "recipient": self.recipient,
            "timestamp": self.timestamp,
        }

    def to_storage(self) -> dict[str, Any]:
        """Shape written to disk (Decimals as text, never floats)."""
        return {
            "type": self.type,
            "amount": str(self.amount),
            "balance_after": str(self.balance_after),
            "recipient": self.recipient,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_storage(cls, data: dict[str, Any]) -> Self:
        if data["type"] not in TRANSACTION_TYPES:
            raise ValueError(f"invalid transaction type in stored data: {data['type']!r}")
        return cls(
            data["type"],
            parse_amount(data["amount"]),
            Decimal(data["balance_after"]),
            data.get("recipient"),
            data["timestamp"],
        )


@dataclass(frozen=True)
class PendingTransfer:
    operation_id: str
    amount: Decimal
    recipient: str
    created_in_turn: int

    def to_public(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "amount": format_brl(self.amount),
            "recipient": self.recipient,
        }

    def to_storage(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "amount": str(self.amount),
            "recipient": self.recipient,
            "created_in_turn": self.created_in_turn,
        }
