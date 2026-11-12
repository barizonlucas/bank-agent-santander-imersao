"""Recipient validation for transfers."""

import re

from app import messages
from app.bank.errors import InvalidRecipientError
from app.config import MAX_RECIPIENT_LENGTH

_ALLOWED_NAME = re.compile(r"^[\wÀ-ÿ][\wÀ-ÿ .,'\-/]*$")
# Matches Portuguese user input ("minha conta", "para mim"...).
_SELF_REFERENCE = re.compile(r"(?i)\b(minha( própria| propria)? conta|eu mesmo|mim mesmo|para mim)\b")


def validate_recipient(raw: object) -> str:
    """Return the normalized recipient name or raise InvalidRecipientError."""
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidRecipientError(messages.RECIPIENT_MISSING)
    name = " ".join(raw.split())
    if len(name) > MAX_RECIPIENT_LENGTH:
        raise InvalidRecipientError(messages.recipient_too_long(MAX_RECIPIENT_LENGTH))
    if not _ALLOWED_NAME.match(name):
        raise InvalidRecipientError(messages.RECIPIENT_INVALID_CHARACTERS)
    if _SELF_REFERENCE.search(name):
        raise InvalidRecipientError(messages.RECIPIENT_IS_SELF)
    return name
