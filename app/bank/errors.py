"""Business errors. Each carries a stable English `code` the agent and the API expose."""


class BankingError(Exception):
    code = "OPERATION_ERROR"


class InvalidAmountError(BankingError, ValueError):
    code = "INVALID_AMOUNT"


class InsufficientBalanceError(BankingError):
    code = "INSUFFICIENT_BALANCE"


class InvalidRecipientError(BankingError):
    code = "INVALID_RECIPIENT"


class NoPendingOperationError(BankingError):
    code = "NO_PENDING_OPERATION"


class ConfirmationNotAuthorizedError(BankingError):
    code = "CONFIRMATION_REQUIRES_USER"
