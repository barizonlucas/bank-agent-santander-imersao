from decimal import Decimal

import pytest

from app.bank.errors import InvalidAmountError
from app.bank.money import format_brl, parse_amount


@pytest.mark.parametrize(
    "raw, expected",
    [
        (250, "250.00"), ("250", "250.00"), ("250.5", "250.50"), ("250,50", "250.50"),
        ("1.234,56", "1234.56"), ("1,234.56", "1234.56"), ("R$ 100", "100.00"), ("100 reais", "100.00"),
        ("0.01", "0.01"), (0.1, "0.10"), (Decimal("99.99"), "99.99"), ("+10", "10.00"),
        ("1000000", "1000000.00"),
    ],
)
def test_valid_amounts(raw, expected):
    assert parse_amount(raw) == Decimal(expected)


@pytest.mark.parametrize(
    "raw",
    [
        -50, "-50", 0, "0", "0,00", "abc", "", "   ", None, True, False, float("nan"), float("inf"),
        "NaN", "Infinity", "10.999", 10.999, "1e3", "12,34,56", "cem", "100; DROP TABLE", "1000000.01",
        [], {},
    ],
)
def test_invalid_amounts(raw):
    with pytest.raises(InvalidAmountError):
        parse_amount(raw)


def test_invalid_amount_error_is_also_a_value_error_with_a_stable_code():
    with pytest.raises(ValueError) as caught:
        parse_amount("abc")
    assert caught.value.code == "INVALID_AMOUNT"


def test_format_brl():
    assert format_brl(Decimal("1234.5")) == "R$ 1.234,50"
    assert format_brl(Decimal("0")) == "R$ 0,00"
    assert format_brl(Decimal("1000000")) == "R$ 1.000.000,00"
    assert format_brl(Decimal("-5")) == "-R$ 5,00"


@pytest.mark.parametrize(
    "raw, expected",
    [("1.000.000", "1000000.00"), ("1,000,000", "1000000.00"), ("1.000.000,00", "1000000.00")],
)
def test_unambiguous_thousands_separators(raw, expected):
    assert parse_amount(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["10.999", "1.500", "1,234"])
def test_ambiguous_formats_are_rejected(raw):
    with pytest.raises(InvalidAmountError):
        parse_amount(raw)
