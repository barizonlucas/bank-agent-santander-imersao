from decimal import Decimal

import pytest

from app.bank.account import (
    Account,
    ConfirmacaoNaoAutorizadaError,
    DestinoInvalidoError,
    SaldoInsuficienteError,
    SemOperacaoPendenteError,
)
from app.bank.money import ValorInvalidoError


def transferir(conta: Account, valor, destino="conta da Ana"):
    """Simula o fluxo real: prepara num turno, confirma no turno seguinte."""
    conta.novo_turno_usuario()
    p = conta.preparar_transferencia(valor, destino)
    conta.novo_turno_usuario()
    return conta.confirmar_transferencia(p["operacao_id"])


def test_saldo_inicial():
    c = Account()
    assert c.saldo == Decimal("1000.00")
    assert c.consultar_saldo()["saldo"] == "R$ 1.000,00"


def test_deposito():
    c = Account()
    r = c.depositar("250")
    assert r["novo_saldo"] == "R$ 1.250,00" and r["saldo_anterior"] == "R$ 1.000,00"
    assert c.saldo == Decimal("1250.00")


@pytest.mark.parametrize("valor", ["-50", "0", "abc", "10.999"])
def test_deposito_invalido_nao_altera_saldo(valor):
    c = Account()
    with pytest.raises(ValorInvalidoError):
        c.depositar(valor)
    assert c.saldo == Decimal("1000.00") and c.extrato == []


def test_transferencia_em_duas_etapas():
    c = Account()
    c.novo_turno_usuario()
    p = c.preparar_transferencia("100", "conta da Ana")
    assert p["status"] == "AGUARDANDO_CONFIRMACAO_DO_USUARIO"
    assert c.saldo == Decimal("1000.00"), "preparar NÃO pode debitar"
    c.novo_turno_usuario()
    r = c.confirmar_transferencia(p["operacao_id"])
    assert r["novo_saldo"] == "R$ 900,00" and r["destinatario"] == "conta da Ana"
    assert c.pendente is None


def test_confirmacao_no_mesmo_turno_e_bloqueada():
    c = Account()
    c.novo_turno_usuario()
    p = c.preparar_transferencia("100", "Ana")
    with pytest.raises(ConfirmacaoNaoAutorizadaError):
        c.confirmar_transferencia(p["operacao_id"])
    assert c.saldo == Decimal("1000.00")


def test_transferencia_maior_que_saldo():
    c = Account()
    with pytest.raises(SaldoInsuficienteError):
        c.preparar_transferencia("5000", "Ana")
    assert c.saldo == Decimal("1000.00") and c.pendente is None


def test_transferir_saldo_total_zera_sem_negativar():
    c = Account()
    transferir(c, "1000")
    assert c.saldo == Decimal("0.00")
    with pytest.raises(SaldoInsuficienteError):
        c.preparar_transferencia("0.01", "Ana")


def test_revalida_saldo_na_confirmacao():
    c = Account()
    c.novo_turno_usuario()
    p1 = c.preparar_transferencia("800", "Ana")
    # Outra transferência substitui a pendência; a primeira deixa de existir
    p2 = c.preparar_transferencia("900", "Bruno")
    c.novo_turno_usuario()
    with pytest.raises(SemOperacaoPendenteError):
        c.confirmar_transferencia(p1["operacao_id"])
    c.confirmar_transferencia(p2["operacao_id"])
    assert c.saldo == Decimal("100.00")


def test_confirmar_sem_pendencia_ou_id_errado():
    c = Account()
    c.novo_turno_usuario()
    with pytest.raises(SemOperacaoPendenteError):
        c.confirmar_transferencia("TRF-XXXXXX")
    c.preparar_transferencia("10", "Ana")
    c.novo_turno_usuario()
    with pytest.raises(SemOperacaoPendenteError):
        c.confirmar_transferencia("TRF-INVENTADO")


def test_cancelar():
    c = Account()
    c.novo_turno_usuario()
    p = c.preparar_transferencia("10", "Ana")
    c.cancelar_transferencia(p["operacao_id"])
    assert c.pendente is None and c.saldo == Decimal("1000.00")


@pytest.mark.parametrize("destino", ["", "   ", None, "minha conta", "para mim", "x" * 61, "<script>"])
def test_destinatario_invalido(destino):
    c = Account()
    with pytest.raises(DestinoInvalidoError):
        c.preparar_transferencia("10", destino)


def test_precisao_decimal():
    c = Account()
    c.depositar("0.10")
    c.depositar("0.20")
    assert c.saldo == Decimal("1000.30")
    assert c.consultar_saldo()["saldo"] == "R$ 1.000,30"


def test_sequencia_de_operacoes_e_invariante():
    c = Account()
    c.depositar("250")          # 1250.00
    transferir(c, "100")        # 1150.00
    c.depositar("49,99")        # 1199.99
    transferir(c, "1199.98")    # 0.01
    c.depositar("1.000,00")     # 1000.01
    assert c.saldo == Decimal("1000.01")
    dep = sum(t.valor for t in c.extrato if t.tipo == "DEPOSITO")
    trf = sum(t.valor for t in c.extrato if t.tipo == "TRANSFERENCIA")
    assert Decimal("1000.00") + dep - trf == c.saldo
    assert len(c.extrato) == 5
