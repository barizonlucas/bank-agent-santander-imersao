"""Deterministic guardrails applied BEFORE the model (they do not depend on the LLM)."""

from app import messages
from app.config import MAX_MESSAGE_LENGTH


def validate_user_message(message: object) -> str | None:
    """Return a user-facing error text when the input is unacceptable, else None."""
    if not isinstance(message, str) or not message.strip():
        return messages.EMPTY_MESSAGE
    if len(message) > MAX_MESSAGE_LENGTH:
        return messages.message_too_long(MAX_MESSAGE_LENGTH)
    return None
