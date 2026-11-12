"""Guardrail de SAÍDA: verifica os valores monetários no texto gerado pela IA.

Mesmo com o saldo calculado em código, o modelo ainda ESCREVE a resposta e
poderia "alucinar" um número (ex.: "seu saldo é R$ 1.300,00" quando é
R$ 1.250,00). Este módulo extrai todo valor em reais do texto e só aprova a
resposta se CADA valor vier de uma fonte confiável:

  * resultados das ferramentas (deste turno e de turnos anteriores da sessão);
  * estado real da conta (saldo, saldo inicial, pendência, extrato);
  * valores digitados pelo próprio usuário;
  * constantes de regra (limite por operação).

Se algo não bater, a resposta é substituída por uma mensagem determinística
montada a partir dos resultados das ferramentas — nunca do texto do modelo.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.bank.account import Account
from app.bank.money import format_brl
from app.config import LIMITE_POR_OPERACAO, SALDO_INICIAL

# "R$ 1.234,56" | "R$1.000" | "R$ 50" | "1.234,56 reais" | "250 reais"
_VALOR_NO_TEXTO = re.compile(
    r"R\$\s*-?\s*(\d[\d.,]*\d|\d)|(\d[\d.,]*\d|\d)\s*(?:reais|real)\b", re.IGNORECASE
)
_NUMERO_LIVRE = re.compile(r"\d[\d.,]*\d|\d")


def _parse_br(texto: str) -> Decimal | None:
    """Interpreta um número escrito no padrão brasileiro (como a IA deve escrever)."""
    t = texto.strip().rstrip(".,")
    try:
        if "," in t:  # 1.234,56 -> 1234.56
            if t.count(",") > 1:
                return None
            return Decimal(t.replace(".", "").replace(",", "."))
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", t):  # 1.000 -> 1000
            return Decimal(t.replace(".", ""))
        if re.fullmatch(r"\d+", t):
            return Decimal(t)
    except InvalidOperation:
        return None
    return None


def _interpretacoes_usuario(token: str) -> set[Decimal]:
    """O usuário pode escrever '50.5' (ponto decimal) ou '1.000' (milhar): aceitamos ambas."""
    valores = set()
    br = _parse_br(token)
    if br is not None:
        valores.add(br)
    try:
        valores.add(Decimal(token.replace(",", "")))
    except InvalidOperation:
        pass
    return valores


def extrair_valores(texto: str) -> list[tuple[str, Decimal | None]]:
    """Retorna [(trecho_original, valor)] para cada menção monetária; valor None = ilegível."""
    achados = []
    for m in _VALOR_NO_TEXTO.finditer(texto or ""):
        numero = m.group(1) or m.group(2)
        achados.append((m.group(0).strip(), _parse_br(numero)))
    return achados


def _valores_em(obj) -> set[Decimal]:
    """Coleta valores monetários de qualquer estrutura (resultados de ferramentas)."""
    if isinstance(obj, dict):
        return set().union(*(_valores_em(v) for v in obj.values())) if obj else set()
    if isinstance(obj, (list, tuple)):
        return set().union(*(_valores_em(v) for v in obj)) if obj else set()
    if isinstance(obj, str):
        return {v for _, v in extrair_valores(obj) if v is not None}
    return set()


def valores_do_usuario(mensagem: str) -> set[Decimal]:
    valores: set[Decimal] = set()
    for token in _NUMERO_LIVRE.findall(mensagem or ""):
        valores |= _interpretacoes_usuario(token)
    return valores


def valores_permitidos(conta: Account, resultados: list[dict], mensagem_usuario: str,
                       confiaveis_sessao: set[Decimal] | None = None) -> set[Decimal]:
    permitidos: set[Decimal] = {conta.saldo, SALDO_INICIAL, LIMITE_POR_OPERACAO}
    if conta.pendente:
        permitidos |= {conta.pendente.valor, conta.saldo - conta.pendente.valor}
    for t in conta.extrato:
        permitidos |= {t.valor, t.saldo_apos}
    permitidos |= _valores_em(resultados)
    permitidos |= valores_do_usuario(mensagem_usuario)
    permitidos |= confiaveis_sessao or set()
    return {v.quantize(Decimal("0.01")) for v in permitidos}


# "saldo atual: R$ X", "seu saldo é de R$ X", "novo saldo: R$ X", "saldo disponível R$ X"
_MENCAO_SALDO = re.compile(
    r"(saldo\s+atual|seu\s+saldo|novo\s+saldo|saldo\s+dispon[ií]vel|saldo\s+da\s+(?:sua\s+)?conta)"
    r"[^R\d\n]{0,30}R\$\s*(\d[\d.,]*\d|\d)",
    re.IGNORECASE,
)


def saldos_validos(conta: Account, resultados: list[dict]) -> set[Decimal]:
    """Valores aceitos quando o texto afirma qual É o saldo: o saldo real agora, ou o
    'novo_saldo' de uma operação deste mesmo turno (resposta com várias operações)."""
    validos = {conta.saldo}
    for r in resultados:
        if r.get("ok"):
            for chave in ("novo_saldo", "saldo", "saldo_atual"):
                if chave in r:
                    validos |= {v for _, v in extrair_valores(r[chave]) if v is not None}
    return {v.quantize(Decimal("0.01")) for v in validos}


def verificar(texto: str, permitidos: set[Decimal], saldos: set[Decimal] | None = None) -> list[str]:
    """Retorna a lista de trechos SUSPEITOS (vazia = resposta aprovada).

    Duas checagens:
      1. todo valor em reais precisa estar entre os valores confiáveis;
      2. se o texto AFIRMA o saldo atual/novo, o valor precisa ser o saldo real
         (pega saldo desatualizado, não só valor inventado).
    """
    suspeitos = []
    for trecho, valor in extrair_valores(texto):
        if valor is None or valor.quantize(Decimal("0.01")) not in permitidos:
            suspeitos.append(trecho)
    if saldos is not None:
        for m in _MENCAO_SALDO.finditer(texto or ""):
            valor = _parse_br(m.group(2))
            if valor is None or valor.quantize(Decimal("0.01")) not in saldos:
                suspeitos.append(m.group(0).strip())
    return list(dict.fromkeys(suspeitos))


def resposta_segura(conta: Account, resultados: list[tuple[str, dict]]) -> str:
    """Mensagem 100% determinística, montada só com dados das ferramentas e da conta."""
    linhas = []
    for nome, r in resultados:
        if not r.get("ok"):
            linhas.append(r.get("mensagem", "Não foi possível concluir a operação."))
        elif nome == "depositar":
            linhas.append(f"Depósito de {r['valor_depositado']} realizado. Novo saldo: {r['novo_saldo']}.")
        elif nome == "transferir":
            linhas.append(
                f"Transferência de {r['valor']} para {r['destinatario']} aguardando sua confirmação "
                f"(saldo após a transferência: {r['saldo_apos_transferencia']}). Responda 'confirmo' ou 'cancelar'."
            )
        elif nome == "confirmar_operacao":
            linhas.append(
                f"Transferência de {r['valor_transferido']} para {r['destinatario']} concluída. "
                f"Novo saldo: {r['novo_saldo']}."
            )
        elif nome == "cancelar_operacao":
            linhas.append("Transferência cancelada. Nenhum valor foi debitado.")
        elif nome == "consultar_saldo":
            linhas.append(f"Seu saldo atual é {r['saldo']}.")
    if not linhas:
        linhas.append(f"Não consegui gerar uma resposta confiável. Seu saldo atual é {format_brl(conta.saldo)}.")
    return " ".join(dict.fromkeys(linhas))  # remove duplicadas mantendo a ordem
