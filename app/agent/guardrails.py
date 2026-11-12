"""Guardrails determinísticos aplicados ANTES do modelo (não dependem do LLM)."""

from app.config import TAMANHO_MAX_MENSAGEM


def validar_entrada(mensagem: object) -> str | None:
    """Retorna uma mensagem de erro se a entrada for inaceitável, senão None."""
    if not isinstance(mensagem, str) or not mensagem.strip():
        return "Por favor, digite uma mensagem. Posso consultar seu saldo, fazer um depósito ou uma transferência."
    if len(mensagem) > TAMANHO_MAX_MENSAGEM:
        return (
            f"Sua mensagem é muito longa (máximo de {TAMANHO_MAX_MENSAGEM} caracteres). "
            "Escreva de forma objetiva qual operação deseja: consultar saldo, depositar ou transferir."
        )
    return None
