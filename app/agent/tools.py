"""Declaração das ferramentas (function calling) e despacho para a conta.

As descrições são precisas porque são elas que o modelo lê para decidir o que
chamar. O parâmetro `valor` é STRING de propósito: o parse exato é feito em
Python (app.bank.money), nunca pelo modelo e nunca via float do JSON.
"""

from google.genai import types

from app.bank.account import Account, OperacaoBancariaError
from app.bank.money import ValorInvalidoError

_VALOR = types.Schema(
    type=types.Type.STRING,
    description='Valor em reais como texto, formato "1234.56" (ponto decimal, sem separador de milhar). '
    "Deve ser exatamente o valor que o usuário pediu.",
)
_OPERACAO_ID = types.Schema(
    type=types.Type.STRING,
    description="Identificador retornado por `transferir` (ex.: TRF-1A2B3C).",
)

DECLARACOES = [
    types.FunctionDeclaration(
        name="consultar_saldo",
        description="Retorna o saldo atual e exato da conta do usuário. Use sempre que precisar informar o saldo.",
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
    types.FunctionDeclaration(
        name="depositar",
        description="Deposita um valor positivo na conta do usuário e retorna o novo saldo. "
        "Use apenas quando o usuário informar claramente o valor.",
        parameters=types.Schema(type=types.Type.OBJECT, properties={"valor": _VALOR}, required=["valor"]),
    ),
    types.FunctionDeclaration(
        name="transferir",
        description="ETAPA 1 de uma transferência: valida o valor e o saldo e cria uma transferência "
        "PENDENTE (ainda não debita). Depois mostre o resumo e peça confirmação ao usuário.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "valor": _VALOR,
                "destinatario": types.Schema(
                    type=types.Type.STRING,
                    description="Para quem enviar, exatamente como o usuário disse (ex.: 'conta da Ana').",
                ),
            },
            required=["valor", "destinatario"],
        ),
    ),
    types.FunctionDeclaration(
        name="confirmar_operacao",
        description="ETAPA 2: efetiva a transferência pendente. Só use depois que o usuário confirmou "
        "explicitamente em uma nova mensagem.",
        parameters=types.Schema(
            type=types.Type.OBJECT, properties={"operacao_id": _OPERACAO_ID}, required=["operacao_id"]
        ),
    ),
    types.FunctionDeclaration(
        name="cancelar_operacao",
        description="Cancela a transferência pendente quando o usuário desistir.",
        parameters=types.Schema(
            type=types.Type.OBJECT, properties={"operacao_id": _OPERACAO_ID}, required=["operacao_id"]
        ),
    ),
]

FERRAMENTAS = [types.Tool(function_declarations=DECLARACOES)]

# Ferramentas que alteram estado (útil para logs e para os evals)
FERRAMENTAS_DE_ESCRITA = {"depositar", "confirmar_operacao"}


def executar_ferramenta(conta: Account, nome: str, args: dict | None) -> dict:
    """Despacha a chamada do modelo para a conta. NUNCA levanta exceção para o modelo:
    qualquer problema vira um resultado {"ok": False, ...} que ele deve explicar."""
    args = dict(args or {})
    try:
        if nome == "consultar_saldo":
            return conta.consultar_saldo()
        if nome == "depositar":
            return conta.depositar(args.get("valor"))
        if nome == "transferir":
            return conta.preparar_transferencia(args.get("valor"), args.get("destinatario"))
        if nome == "confirmar_operacao":
            return conta.confirmar_transferencia(args.get("operacao_id"))
        if nome == "cancelar_operacao":
            return conta.cancelar_transferencia(args.get("operacao_id"))
        return {"ok": False, "erro": "FERRAMENTA_INEXISTENTE", "mensagem": "Operação não disponível."}
    except (ValorInvalidoError, OperacaoBancariaError) as exc:
        return {"ok": False, "erro": exc.codigo, "mensagem": str(exc)}
    except Exception:  # noqa: BLE001 — falha inesperada não pode vazar para o modelo
        return {"ok": False, "erro": "ERRO_INTERNO", "mensagem": "Não foi possível concluir a operação."}
