"""Exact money parsing and formatting with Decimal.

Design decision: floats never touch money. The model sends the amount as *text*
and this module is the only place that converts it. Any doubtful input is
REJECTED rather than "fixed" or rounded.
"""

import re
from decimal import Decimal, InvalidOperation

from app import messages
from app.bank.errors import InvalidAmountError
from app.config import MAX_AMOUNT_PER_OPERATION

CENT = Decimal("0.01")

# Accepted formats (after stripping "R$" and spaces). A thousands separator is
# only recognised when it cannot be confused with the decimal separator:
#   1.234,56 | 1.234.567  -> Brazilian thousands (decimal comma, or 2+ groups)
#   1,234.56 | 1,234,567  -> US thousands (decimal point, or 2+ groups)
#   1234.56  | 1234,56    -> no thousands separator
# "10.999" or "1,234" read as decimals with 3 places and are REJECTED, so the
# agent asks the user to clarify instead of guessing.
_BR_THOUSANDS = re.compile(r"^\d{1,3}(\.\d{3})+,\d+$|^\d{1,3}(\.\d{3}){2,}$")
_US_THOUSANDS = re.compile(r"^\d{1,3}(,\d{3})+\.\d+$|^\d{1,3}(,\d{3}){2,}$")
_PLAIN_DOT = re.compile(r"^\d+(\.\d+)?$")
_PLAIN_COMMA = re.compile(r"^\d+(,\d+)?$")
# Matches Portuguese user input: "R$ 100" prefix and "100 reais" suffix.
_CURRENCY_PREFIX = re.compile(r"(?i)^r\$\s*")
_CURRENCY_SUFFIX = re.compile(r"(?i)\s*(reais|real)$")


def _strip_currency_and_sign(text: str) -> str:
    stripped = _CURRENCY_PREFIX.sub("", text.strip())
    stripped = _CURRENCY_SUFFIX.sub("", stripped).replace(" ", "")
    if stripped.startswith("-"):
        raise InvalidAmountError(messages.AMOUNT_NEGATIVE)
    return stripped.removeprefix("+")


def _normalize_text(text: str) -> str:
    """Turn a user-written amount into a plain `1234.56` string or raise."""
    cleaned = _strip_currency_and_sign(text)
    if _BR_THOUSANDS.match(cleaned):
        return cleaned.replace(".", "").replace(",", ".")
    if _US_THOUSANDS.match(cleaned):
        return cleaned.replace(",", "")
    if _PLAIN_DOT.match(cleaned):
        return cleaned
    if _PLAIN_COMMA.match(cleaned):
        return cleaned.replace(",", ".")
    raise InvalidAmountError(messages.amount_unparseable(text))


def _to_decimal(raw: object) -> Decimal:
    """Convert the accepted input types to Decimal (no range validation yet)."""
    if isinstance(raw, bool) or raw is None:
        raise InvalidAmountError(messages.AMOUNT_NOT_NUMERIC)
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, float):
        return Decimal(repr(raw))  # repr() gives the shortest exact decimal (0.1 -> "0.1")
    if isinstance(raw, str) and raw.strip():
        return Decimal(_normalize_text(raw))
    raise InvalidAmountError(messages.AMOUNT_NOT_NUMERIC)


def _validate_range_and_precision(value: Decimal) -> None:
    if not value.is_finite():
        raise InvalidAmountError(messages.AMOUNT_NOT_FINITE)
    if value <= 0:
        raise InvalidAmountError(messages.AMOUNT_NOT_POSITIVE)
    # More than 2 written decimals ("1.500", "10.999") is rejected even when the
    # extra digits are zeros: it is ambiguous (1,50 or 1.500?) so we ask instead.
    if value.as_tuple().exponent < -2 or value != value.quantize(CENT):
        raise InvalidAmountError(messages.AMOUNT_TOO_MANY_DECIMALS)
    if value > MAX_AMOUNT_PER_OPERATION:
        raise InvalidAmountError(messages.amount_over_limit(format_brl(MAX_AMOUNT_PER_OPERATION)))


def parse_amount(raw: object) -> Decimal:
    """Parse `raw` into a positive Decimal with 2 places, or raise InvalidAmountError.

    Accepts int, Decimal, finite float, or numeric text ("1.234,56", "R$ 100").
    Rejects bools, zero, negatives, more than 2 decimals and amounts above the limit.
    """
    try:
        value = _to_decimal(raw)
    except InvalidOperation as exc:
        raise InvalidAmountError(messages.amount_unparseable(raw)) from exc
    _validate_range_and_precision(value)
    return value.quantize(CENT)


def format_brl(amount: Decimal) -> str:
    """Format in the Brazilian standard: R$ 1.234,56."""
    quantized = amount.quantize(CENT)
    sign = "-" if quantized < 0 else ""
    integer, cents = f"{abs(quantized):.2f}".split(".")
    return f"{sign}R$ {int(integer):,}".replace(",", ".") + f",{cents}"
