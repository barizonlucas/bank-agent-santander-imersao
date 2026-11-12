"""Utilidades monetárias: parse e formatação EXATOS usando Decimal.

Decisão de projeto: nunca usamos float para dinheiro. O modelo envia o valor
como *texto* e este módulo é o único responsável por convertê-lo. Qualquer
entrada duvidosa é RECUSADA em vez de "corrigida" ou arredondada.
"""

import re
from decimal import Decimal, InvalidOperation

from app.config import LIMITE_POR_OPERACAO


class ValorInvalidoError(ValueError):
    """Valor monetário inválido (não numérico, <= 0, casas demais, etc.)."""

    codigo = "VALOR_INVALIDO"


CENTAVO = Decimal("0.01")

# Formatos aceitos (após remover "R$" e espaços). Separador de milhar só é
# reconhecido quando NÃO há ambiguidade com o separador decimal:
#   1.234,56  | 1.234.567       -> milhar BR (tem vírgula decimal ou 2+ grupos)
#   1,234.56  | 1,234,567       -> milhar US (tem ponto decimal ou 2+ grupos)
#   1234.56   | 1234,56 | 250   -> sem milhar
# Casos como "10.999" ou "1,234" são lidos como decimal e, por terem 3 casas,
# são RECUSADOS — o agente então pede esclarecimento ao usuário.
_PADRAO_BR_MILHAR = re.compile(r"^\d{1,3}(\.\d{3})+,\d+$|^\d{1,3}(\.\d{3}){2,}$")
_PADRAO_US_MILHAR = re.compile(r"^\d{1,3}(,\d{3})+\.\d+$|^\d{1,3}(,\d{3}){2,}$")
_PADRAO_SIMPLES_PONTO = re.compile(r"^\d+(\.\d+)?$")
_PADRAO_SIMPLES_VIRGULA = re.compile(r"^\d+(,\d+)?$")


def _normalizar_texto(texto: str) -> str:
    t = texto.strip()
    t = re.sub(r"(?i)^r\$\s*", "", t)  # remove prefixo R$
    t = re.sub(r"(?i)\s*(reais|real)$", "", t)  # remove sufixo "reais"
    t = t.replace(" ", "")

    if t.startswith(("-", "+")):
        sinal = t[0]
        if sinal == "-":
            raise ValorInvalidoError("O valor deve ser maior que zero (valores negativos não são permitidos).")
        t = t[1:]

    if _PADRAO_BR_MILHAR.match(t):
        return t.replace(".", "").replace(",", ".")
    if _PADRAO_US_MILHAR.match(t):
        return t.replace(",", "")
    if _PADRAO_SIMPLES_PONTO.match(t):
        return t
    if _PADRAO_SIMPLES_VIRGULA.match(t):
        return t.replace(",", ".")
    raise ValorInvalidoError(f"'{texto}' não é um valor numérico válido.")


def parse_valor(entrada: object) -> Decimal:
    """Converte a entrada em Decimal com 2 casas, validando TODAS as regras.

    Regras:
      * tipo numérico (int, Decimal, float finito) ou texto numérico;
      * booleanos são recusados (True não é 1 real);
      * maior que zero;
      * no máximo 2 casas decimais (não arredondamos silenciosamente);
      * até o limite por operação.
    """
    if isinstance(entrada, bool) or entrada is None:
        raise ValorInvalidoError("Informe um valor numérico.")

    try:
        if isinstance(entrada, Decimal):
            valor = entrada
        elif isinstance(entrada, int):
            valor = Decimal(entrada)
        elif isinstance(entrada, float):
            # repr() dá a menor representação decimal exata do float (ex.: 0.1 -> "0.1")
            valor = Decimal(repr(entrada))
        elif isinstance(entrada, str):
            if not entrada.strip():
                raise ValorInvalidoError("Informe um valor numérico.")
            valor = Decimal(_normalizar_texto(entrada))
        else:
            raise ValorInvalidoError("Informe um valor numérico.")
    except InvalidOperation as exc:
        raise ValorInvalidoError(f"'{entrada}' não é um valor numérico válido.") from exc

    if not valor.is_finite():
        raise ValorInvalidoError("O valor precisa ser um número finito.")
    if valor <= 0:
        raise ValorInvalidoError("O valor deve ser maior que zero.")
    # Mais de 2 casas escritas (ex.: "1.500", "10.999") é recusado mesmo que os
    # dígitos extras sejam zero: é ambíguo (1,50 ou 1.500?) e pedimos esclarecimento.
    if valor.as_tuple().exponent < -2 or valor != valor.quantize(CENTAVO):
        raise ValorInvalidoError("O valor pode ter no máximo 2 casas decimais (centavos).")
    if valor > LIMITE_POR_OPERACAO:
        raise ValorInvalidoError(f"O valor excede o limite por operação de {format_brl(LIMITE_POR_OPERACAO)}.")

    return valor.quantize(CENTAVO)


def format_brl(valor: Decimal) -> str:
    """Formata no padrão brasileiro: R$ 1.234,56."""
    q = valor.quantize(CENTAVO)
    sinal = "-" if q < 0 else ""
    inteiro, centavos = f"{abs(q):.2f}".split(".")
    inteiro_milhar = f"{int(inteiro):,}".replace(",", ".")
    return f"{sinal}R$ {inteiro_milhar},{centavos}"
