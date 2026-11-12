from decimal import Decimal

from app.agent.output_guard import (
    allowed_amounts,
    extract_amounts,
    find_suspicious,
    safe_reply,
    valid_balances,
)
from app.bank.account import Account


def allowed_for(account, results=(), message=""):
    return allowed_amounts(account, list(results), message)


def test_extracts_the_supported_formats():
    found = extract_amounts("Saldo R$ 1.250,00; depositei 250 reais e R$50. Limite R$ 1.000.000,00")
    assert [amount for _, amount in found] == [Decimal("1250.00"), Decimal("250"), Decimal("50"), Decimal("1000000.00")]


def test_text_without_amounts_is_approved():
    assert find_suspicious("Desculpe, só posso ajudar com operações da sua conta.", allowed_for(Account())) == []


def test_amount_from_a_tool_result_is_approved():
    account = Account()
    result = account.deposit("250")
    text = "Depósito de R$ 250,00 realizado. Novo saldo: R$ 1.250,00."
    assert find_suspicious(text, allowed_for(account, [result]), valid_balances(account, [result])) == []


def test_invented_amount_is_rejected():
    assert find_suspicious("Seu saldo é R$ 9.999,00.", allowed_for(Account())) == ["R$ 9.999,00"]


def test_stale_balance_is_rejected():
    account = Account()
    account.deposit("250")  # real balance: 1.250,00, but 1.000,00 is a "known" amount (initial balance)
    allowed, balances = allowed_for(account), valid_balances(account, [])
    assert find_suspicious("Seu saldo atual é de R$ 1.000,00.", allowed, balances) != []
    assert find_suspicious("Seu saldo atual é de R$ 1.250,00.", allowed, balances) == []


def test_several_operations_in_the_same_turn():
    account = Account()
    first, second = account.deposit("250"), account.deposit("50")
    text = "Depósito feito, novo saldo: R$ 1.250,00. Outro depósito, novo saldo: R$ 1.300,00."
    results = [first, second]
    assert find_suspicious(text, allowed_for(account, results), valid_balances(account, results)) == []


def test_amounts_from_the_statement_and_from_the_user_are_approved():
    account = Account()
    account.deposit("100")
    text = "Você depositou R$ 100,00 antes e pediu R$ 5.000,00, que excede o saldo."
    assert find_suspicious(text, allowed_for(account, message="Transfere 5000")) == []


def test_safe_reply_is_deterministic():
    account = Account()
    result = account.deposit("10")
    assert safe_reply(account, [("deposit", result)]) == "Depósito de R$ 10,00 realizado. Novo saldo: R$ 1.010,00."
    assert "R$ 1.010,00" in safe_reply(account, [])
