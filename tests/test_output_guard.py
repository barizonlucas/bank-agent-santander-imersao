from decimal import Decimal

from app.agent.output_guard import (
    extrair_valores, resposta_segura, saldos_validos, valores_permitidos, verificar,
)
from app.bank.account import Account


def permitidos(conta, resultados=(), msg=""):
    return valores_permitidos(conta, list(resultados), msg)


def test_extrai_formatos():
    vals = [v for _, v in extrair_valores("Saldo R$ 1.250,00; depositei 250 reais e R$50. Limite R$ 1.000.000,00")]
    assert vals == [Decimal("1250.00"), Decimal("250"), Decimal("50"), Decimal("1000000.00")]


def test_texto_sem_valores_aprovado():
    assert verificar("Desculpe, só posso ajudar com operações da sua conta.", permitidos(Account())) == []


def test_valor_do_resultado_da_ferramenta_aprovado():
    c = Account()
    r = c.depositar("250")
    texto = "Depósito de R$ 250,00 realizado. Novo saldo: R$ 1.250,00."
    assert verificar(texto, permitidos(c, [r]), saldos_validos(c, [r])) == []


def test_valor_inventado_reprovado():
    c = Account()
    assert verificar("Seu saldo é R$ 9.999,00.", permitidos(c)) == ["R$ 9.999,00"]


def test_saldo_desatualizado_reprovado():
    c = Account()
    c.depositar("250")  # saldo real: 1.250,00 — mas 1.000,00 é um valor "conhecido" (saldo inicial)
    texto = "Seu saldo atual é de R$ 1.000,00."
    assert verificar(texto, permitidos(c), saldos_validos(c, [])) != []
    assert verificar("Seu saldo atual é de R$ 1.250,00.", permitidos(c), saldos_validos(c, [])) == []


def test_varias_operacoes_no_mesmo_turno():
    c = Account()
    r1 = c.depositar("250")
    r2 = c.depositar("50")
    texto = "Depósito feito, novo saldo: R$ 1.250,00. Outro depósito, novo saldo: R$ 1.300,00."
    assert verificar(texto, permitidos(c, [r1, r2]), saldos_validos(c, [r1, r2])) == []


def test_valor_do_extrato_e_do_usuario_aprovados():
    c = Account()
    c.depositar("100")
    texto = "Você depositou R$ 100,00 antes e pediu R$ 5.000,00, que excede o saldo."
    assert verificar(texto, permitidos(c, msg="Transfere 5000")) == []


def test_resposta_segura_e_deterministica():
    c = Account()
    r = c.depositar("10")
    assert resposta_segura(c, [("depositar", r)]) == "Depósito de R$ 10,00 realizado. Novo saldo: R$ 1.010,00."
    assert "R$ 1.010,00" in resposta_segura(c, [])
