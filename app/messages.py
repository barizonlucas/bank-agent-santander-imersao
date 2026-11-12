"""Every piece of text shown to the end user (Brazilian Portuguese).

This is the ONLY module (besides the system prompt and the regexes that match
Portuguese input) where Portuguese is allowed. Keeping it in one place makes the
rest of the codebase English-only and makes a future translation a one-file job.
"""

ACCOUNT_LABEL = "Conta Corrente Imersão (0001 / 12345-6)"

# --- Amount validation -------------------------------------------------------
AMOUNT_NOT_NUMERIC = "Informe um valor numérico."
AMOUNT_NEGATIVE = "O valor deve ser maior que zero (valores negativos não são permitidos)."
AMOUNT_NOT_POSITIVE = "O valor deve ser maior que zero."
AMOUNT_NOT_FINITE = "O valor precisa ser um número finito."
AMOUNT_TOO_MANY_DECIMALS = "O valor pode ter no máximo 2 casas decimais (centavos)."


def amount_unparseable(raw: object) -> str:
    return f"'{raw}' não é um valor numérico válido."


def amount_over_limit(limit: str) -> str:
    return f"O valor excede o limite por operação de {limit}."


# --- Recipient validation ----------------------------------------------------
RECIPIENT_MISSING = "Informe para quem deseja transferir (ex.: 'conta da Ana')."
RECIPIENT_INVALID_CHARACTERS = "O destinatário contém caracteres inválidos."
RECIPIENT_IS_SELF = "Não é possível transferir para a própria conta."


def recipient_too_long(max_length: int) -> str:
    return f"O destinatário deve ter no máximo {max_length} caracteres."


# --- Account rules -----------------------------------------------------------
CONFIRMATION_REQUIRES_NEW_MESSAGE = (
    "A confirmação precisa vir do usuário em uma nova mensagem. Mostre o resumo e aguarde a resposta dele."
)
NO_PENDING_TRANSFER = "Não há nenhuma transferência aguardando confirmação."


def insufficient_balance(requested: str, available: str) -> str:
    return f"Saldo insuficiente: você tentou transferir {requested}, mas o saldo disponível é {available}."


def insufficient_balance_at_confirmation(available: str, requested: str) -> str:
    return (
        f"Saldo insuficiente no momento da confirmação: saldo {available}, "
        f"transferência {requested}. A operação foi cancelada."
    )


def wrong_operation_id(pending_id: str) -> str:
    return f"Identificador de operação inválido. A operação pendente é {pending_id}."


# --- Tools -------------------------------------------------------------------
TOOL_UNAVAILABLE = "Operação não disponível."
OPERATION_FAILED = "Não foi possível concluir a operação."

# --- Input guardrail ---------------------------------------------------------
EMPTY_MESSAGE = "Por favor, digite uma mensagem. Posso consultar seu saldo, fazer um depósito ou uma transferência."


def message_too_long(max_length: int) -> str:
    return (
        f"Sua mensagem é muito longa (máximo de {max_length} caracteres). "
        "Escreva de forma objetiva qual operação deseja: consultar saldo, depositar ou transferir."
    )


# --- Agent replies -----------------------------------------------------------
DEFAULT_REPLY = "Posso ajudar você a consultar o saldo, fazer um depósito ou uma transferência. O que deseja?"
SCOPE_REFUSAL = (
    "Desculpe, só posso ajudar com operações da sua conta: consultar saldo, depositar ou transferir. "
    "Como posso ajudar?"
)
CANNOT_COMPLETE_REQUEST = "Não consegui concluir seu pedido."
MODEL_UNAVAILABLE = "O serviço de IA está temporariamente indisponível."
MODEL_QUOTA_EXHAUSTED = "O limite de uso gratuito da IA foi atingido no momento. Aguarde alguns segundos."


def failure_reply(reason: str, balance_after_writes: str | None) -> str:
    """`balance_after_writes` is set only when a write already succeeded in this turn."""
    text = f"{reason} Tente novamente."
    if balance_after_writes:
        text += f" Atenção: operações já concluídas nesta mensagem foram mantidas. Saldo atual: {balance_after_writes}."
    return text


# --- Deterministic reply used when the output guardrail rejects the model text
def safe_deposit(deposited: str, new_balance: str) -> str:
    return f"Depósito de {deposited} realizado. Novo saldo: {new_balance}."


def safe_transfer_pending(amount: str, recipient: str, balance_after: str) -> str:
    return (
        f"Transferência de {amount} para {recipient} aguardando sua confirmação "
        f"(saldo após a transferência: {balance_after}). Responda 'confirmo' ou 'cancelar'."
    )


def safe_transfer_done(amount: str, recipient: str, new_balance: str) -> str:
    return f"Transferência de {amount} para {recipient} concluída. Novo saldo: {new_balance}."


SAFE_TRANSFER_CANCELLED = "Transferência cancelada. Nenhum valor foi debitado."


def safe_balance(balance: str) -> str:
    return f"Seu saldo atual é {balance}."


def safe_unreliable_reply(balance: str) -> str:
    return f"Não consegui gerar uma resposta confiável. Seu saldo atual é {balance}."


# --- Governance dashboard labels --------------------------------------------
CATEGORY_LABELS = {
    "OPERATION": "Operação executada",
    "BALANCE_QUERY": "Consulta de saldo",
    "OUT_OF_SCOPE_REFUSAL": "Recusa (fora do escopo)",
    "CLARIFICATION": "Pedido de esclarecimento",
    "VALIDATION_BLOCKED": "Bloqueada pela validação",
    "INPUT_BLOCKED": "Bloqueada na entrada",
    "OUTPUT_GUARDRAIL": "Corrigida pelo guardrail de saída",
    "TECHNICAL_ERROR": "Erro técnico",
}

# --- About page --------------------------------------------------------------
PROJECT_DESCRIPTION = (
    "Agente de IA que gerencia uma conta bancária fictícia (saldo inicial R$ 1.000,00) "
    "e executa somente três operações: consultar saldo, depositar e transferir."
)

LLM_USAGE = (
    "Function calling manual: o modelo só interpreta a intenção e escolhe a ferramenta; "
    "cálculo, validação e saldo ficam no código."
)
STACK_ROWS = [
    {"layer": "Backend", "technology": "Python 3.11+ · FastAPI · Uvicorn"},
    {"layer": "IA", "technology": "SDK google-genai (Gemini API)"},
    {"layer": "Frontend", "technology": "HTML · CSS · JavaScript (sem frameworks)"},
    {"layer": "Regras de negócio", "technology": "Python + Decimal (validação nas ferramentas)"},
    {"layer": "Testes", "technology": "pytest (unitários, loop com modelo falso, API) + evals com modelo real"},
    {"layer": "Ambiente", "technology": "uv (dependências) · python-dotenv"},
]
