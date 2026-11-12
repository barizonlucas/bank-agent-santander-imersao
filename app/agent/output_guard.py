"""OUTPUT guardrail: checks the monetary amounts in the text the AI generated.

Even with the balance computed in code, the model still WRITES the reply and
could "hallucinate" a number (e.g. "your balance is R$ 1.300,00" when it is
R$ 1.250,00). This module extracts every amount in reais from the text and only
approves the reply if EACH amount comes from a trusted source:

  * tool results (this turn and earlier turns of the session);
  * the real account state (balance, initial balance, pending transfer, statement);
  * amounts typed by the user themselves;
  * rule constants (per-operation limit).

If anything does not match, the reply is replaced by a deterministic message
built from the tool results, never from the model text.

The regexes match PORTUGUESE text on purpose: that is the language the agent
writes in ("R$ 1.234,56", "saldo atual").
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from decimal import Decimal, InvalidOperation
from typing import Any

from app import messages
from app.agent import tools
from app.bank.account import Account, ToolResult
from app.bank.money import format_brl
from app.config import INITIAL_BALANCE, MAX_AMOUNT_PER_OPERATION

CENT = Decimal("0.01")
BALANCE_KEYS = ("new_balance", "balance", "current_balance")

# "R$ 1.234,56" | "R$1.000" | "R$ 50" | "1.234,56 reais" | "250 reais"
_AMOUNT_IN_TEXT = re.compile(
    r"R\$\s*-?\s*(\d[\d.,]*\d|\d)|(\d[\d.,]*\d|\d)\s*(?:reais|real)\b", re.IGNORECASE
)
_FREE_NUMBER = re.compile(r"\d[\d.,]*\d|\d")
# "saldo atual: R$ X", "seu saldo é de R$ X", "novo saldo: R$ X", "saldo disponível R$ X"
_BALANCE_CLAIM = re.compile(
    r"(saldo\s+atual|seu\s+saldo|novo\s+saldo|saldo\s+dispon[ií]vel|saldo\s+da\s+(?:sua\s+)?conta)"
    r"[^R\d\n]{0,30}R\$\s*(\d[\d.,]*\d|\d)",
    re.IGNORECASE,
)


# ---- parsing --------------------------------------------------------------------
def parse_br_number(text: str) -> Decimal | None:
    """Parse a number written in the Brazilian standard (how the AI should write it)."""
    try:
        return _parse_cleaned(text.strip().rstrip(".,"))
    except InvalidOperation:
        return None


def _parse_cleaned(text: str) -> Decimal | None:
    if "," in text:
        return None if text.count(",") > 1 else Decimal(text.replace(".", "").replace(",", "."))
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", text):  # 1.000 -> 1000
        return Decimal(text.replace(".", ""))
    return Decimal(text) if re.fullmatch(r"\d+", text) else None


def _user_readings(token: str) -> set[Decimal]:
    """The user may write '50.5' (decimal point) or '1.000' (thousands): accept both."""
    readings = set()
    brazilian = parse_br_number(token)
    if brazilian is not None:
        readings.add(brazilian)
    try:
        readings.add(Decimal(token.replace(",", "")))
    except InvalidOperation:
        pass
    return readings


def extract_amounts(text: str) -> list[tuple[str, Decimal | None]]:
    """Return [(original_snippet, amount)] for each monetary mention; None = unreadable."""
    found = []
    for match in _AMOUNT_IN_TEXT.finditer(text or ""):
        number = match.group(1) or match.group(2)
        found.append((match.group(0).strip(), parse_br_number(number)))
    return found


def _amounts_in(obj: Any) -> set[Decimal]:
    """Collect monetary amounts from any structure (tool results)."""
    if isinstance(obj, dict):
        return set().union(*(_amounts_in(v) for v in obj.values()))
    if isinstance(obj, (list, tuple)):
        return set().union(*(_amounts_in(v) for v in obj))
    if isinstance(obj, str):
        return {amount for _, amount in extract_amounts(obj) if amount is not None}
    return set()


def user_amounts(message: str) -> set[Decimal]:
    amounts: set[Decimal] = set()
    for token in _FREE_NUMBER.findall(message or ""):
        amounts |= _user_readings(token)
    return amounts


def _quantized(amounts: Iterable[Decimal]) -> set[Decimal]:
    return {amount.quantize(CENT) for amount in amounts}


# ---- trusted sources ------------------------------------------------------------
def allowed_amounts(account: Account, tool_results: list[ToolResult], user_message: str) -> set[Decimal]:
    allowed = {account.balance, INITIAL_BALANCE, MAX_AMOUNT_PER_OPERATION}
    allowed |= _pending_amounts(account)
    for transaction in account.statement:
        allowed |= {transaction.amount, transaction.balance_after}
    allowed |= _amounts_in(tool_results)
    allowed |= user_amounts(user_message)
    return _quantized(allowed)


def _pending_amounts(account: Account) -> set[Decimal]:
    if account.pending is None:
        return set()
    return {account.pending.amount, account.balance - account.pending.amount}


def valid_balances(account: Account, tool_results: list[ToolResult]) -> set[Decimal]:
    """Amounts accepted when the text CLAIMS what the balance is: the real balance now,
    or the `new_balance` of an operation in this same turn (multi-operation replies)."""
    valid = {account.balance}
    for result in tool_results:
        valid |= _balances_in(result)
    return _quantized(valid)


def _balances_in(result: ToolResult) -> set[Decimal]:
    if not result.get("ok"):
        return set()
    return set().union(*(_amounts_in(result[key]) for key in BALANCE_KEYS if key in result))


# ---- verification ---------------------------------------------------------------
def find_suspicious(text: str, allowed: set[Decimal], balances: set[Decimal] | None = None) -> list[str]:
    """Return the SUSPICIOUS snippets (empty list = reply approved).

    Two checks:
      1. every amount in reais must be among the trusted amounts;
      2. if the text CLAIMS the current/new balance, it must be the real balance
         (this catches a stale balance, not only an invented amount).
    """
    suspicious = [
        snippet for snippet, amount in extract_amounts(text)
        if amount is None or amount.quantize(CENT) not in allowed
    ]
    if balances is not None:
        suspicious += _wrong_balance_claims(text, balances)
    return list(dict.fromkeys(suspicious))


def _wrong_balance_claims(text: str, balances: set[Decimal]) -> list[str]:
    claims = []
    for match in _BALANCE_CLAIM.finditer(text or ""):
        amount = parse_br_number(match.group(2))
        if amount is None or amount.quantize(CENT) not in balances:
            claims.append(match.group(0).strip())
    return claims


# ---- deterministic fallback reply ----------------------------------------------
_SAFE_LINES: dict[str, Callable[[ToolResult], str]] = {
    tools.DEPOSIT: lambda r: messages.safe_deposit(r["deposited_amount"], r["new_balance"]),
    tools.TRANSFER: lambda r: messages.safe_transfer_pending(r["amount"], r["recipient"], r["balance_after_transfer"]),
    tools.CONFIRM_OPERATION: lambda r: messages.safe_transfer_done(
        r["transferred_amount"], r["recipient"], r["new_balance"]
    ),
    tools.CANCEL_OPERATION: lambda r: messages.SAFE_TRANSFER_CANCELLED,
    tools.GET_BALANCE: lambda r: messages.safe_balance(r["balance"]),
}


def _safe_line(tool_name: str, result: ToolResult) -> str | None:
    if not result.get("ok"):
        return result.get("message", messages.OPERATION_FAILED)
    formatter = _SAFE_LINES.get(tool_name)
    return formatter(result) if formatter else None


def safe_reply(account: Account, executed: list[tuple[str, ToolResult]]) -> str:
    """100% deterministic message, built only from tool results and the account."""
    lines = [line for name, result in executed if (line := _safe_line(name, result))]
    if not lines:
        lines.append(messages.safe_unreliable_reply(format_brl(account.balance)))
    return " ".join(dict.fromkeys(lines))  # drop duplicates, keep order
