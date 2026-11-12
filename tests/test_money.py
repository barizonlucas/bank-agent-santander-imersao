from decimal import Decimal

import pytest

from app.bank.money import ValorInvalidoError, format_brl, parse_valor


@pytest.mark.parametrize(
    "entrada, esperado",
    [
        (250, "250.00"), ("250", "250.00"), ("250.5", "250.50"), ("250,50", "250.50"),
        ("1.234,56", "1234.56"), ("1,234.56", "1234.56"), ("R$ 100", "100.00"), ("100 reais", "100.00"),
        ("0.01", "0.01"), (0.1, "0.10"), (Decimal("99.99"), "99.99"), ("+10", "10.00"),
        ("1000000", "1000000.00"),
    ],
)
def test_valores_validos(entrada, esperado):
    assert parse_valor(entrada) == Decimal(esperado)


@pytest.mark.parametrize(
    "entrada",
    [
        -50, "-50", 0, "0", "0,00", "abc", "", "   ", None, True, False, float("nan"), float("inf"),
        "NaN", "Infinity", "10.999", 10.999, "1e3", "12,34,56", "cem", "100; DROP TABLE", "1000000.01",
        [], {},
    ],
)
def test_valores_invalidos(entrada):
    with pytest.raises(ValorInvalidoError):
        parse_valor(entrada)


def test_format_brl():
    assert format_brl(Decimal("1234.5")) == "R$ 1.234,50"
    assert format_brl(Decimal("0")) == "R$ 0,00"
    assert format_brl(Decimal("1000000")) == "R$ 1.000.000,00"


@pytest.mark.parametrize("entrada, esperado", [("1.000.000", "1000000.00"), ("1,000,000", "1000000.00"), ("1.000.000,00", "1000000.00")])
def test_milhar_sem_ambiguidade(entrada, esperado):
    assert parse_valor(entrada) == Decimal(esperado)


@pytest.mark.parametrize("entrada", ["10.999", "1.500", "1,234"])
def test_formatos_ambiguos_sao_recusados(entrada):
    with pytest.raises(ValorInvalidoError):
        parse_valor(entrada)
