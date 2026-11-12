"""Tool declarations (function calling) and dispatch to the account.

The descriptions are precise because the model reads them to decide what to
call. The amount parameter is a STRING on purpose: exact parsing happens in
Python (app.bank.money), never in the model and never through a JSON float.
"""

from collections.abc import Callable
from typing import Any

from google.genai import types

from app import messages
from app.bank.account import Account, ToolResult
from app.bank.errors import BankingError

GET_BALANCE = "get_balance"
DEPOSIT = "deposit"
TRANSFER = "transfer"
CONFIRM_OPERATION = "confirm_operation"
CANCEL_OPERATION = "cancel_operation"

# Tools that can change the balance; shared by the agent, the audit and the evals.
BALANCE_CHANGING_TOOLS = frozenset({DEPOSIT, CONFIRM_OPERATION})

_AMOUNT = types.Schema(
    type=types.Type.STRING,
    description='Amount in reais as text, format "1234.56" (decimal point, no thousands separator). '
    "Must be exactly the amount the user asked for.",
)
_OPERATION_ID = types.Schema(
    type=types.Type.STRING,
    description="Identifier returned by `transfer` (e.g. TRF-1A2B3C).",
)


def _object(properties: dict[str, types.Schema], required: list[str]) -> types.Schema:
    return types.Schema(type=types.Type.OBJECT, properties=properties, required=required or None)


DECLARATIONS = [
    types.FunctionDeclaration(
        name=GET_BALANCE,
        description="Returns the current, exact balance of the user's account. Use it whenever you must state the balance.",
        parameters=_object({}, []),
    ),
    types.FunctionDeclaration(
        name=DEPOSIT,
        description="Deposits a positive amount into the user's account and returns the new balance. "
        "Use only when the user clearly states the amount.",
        parameters=_object({"amount": _AMOUNT}, ["amount"]),
    ),
    types.FunctionDeclaration(
        name=TRANSFER,
        description="STEP 1 of a transfer: validates amount and balance and creates a PENDING transfer "
        "(no debit yet). Then show the summary and ask the user for confirmation.",
        parameters=_object(
            {
                "amount": _AMOUNT,
                "recipient": types.Schema(
                    type=types.Type.STRING,
                    description="Who to send to, exactly as the user said it (e.g. 'conta da Ana').",
                ),
            },
            ["amount", "recipient"],
        ),
    ),
    types.FunctionDeclaration(
        name=CONFIRM_OPERATION,
        description="STEP 2: executes the pending transfer. Only use it after the user explicitly "
        "confirmed in a new message.",
        parameters=_object({"operation_id": _OPERATION_ID}, ["operation_id"]),
    ),
    types.FunctionDeclaration(
        name=CANCEL_OPERATION,
        description="Cancels the pending transfer when the user gives up.",
        parameters=_object({"operation_id": _OPERATION_ID}, ["operation_id"]),
    ),
]

TOOLS = [types.Tool(function_declarations=DECLARATIONS)]

_HANDLERS: dict[str, Callable[[Account, dict[str, Any]], ToolResult]] = {
    GET_BALANCE: lambda account, args: account.get_balance(),
    DEPOSIT: lambda account, args: account.deposit(args.get("amount")),
    TRANSFER: lambda account, args: account.prepare_transfer(args.get("amount"), args.get("recipient")),
    CONFIRM_OPERATION: lambda account, args: account.confirm_transfer(args.get("operation_id")),
    CANCEL_OPERATION: lambda account, args: account.cancel_transfer(args.get("operation_id")),
}


def _failure(code: str, message: str) -> ToolResult:
    return {"ok": False, "error": code, "message": message}


def execute_tool(account: Account, name: str, args: dict[str, Any] | None) -> ToolResult:
    """Dispatch a model tool call to the account. NEVER raises toward the model:
    every problem becomes an `{"ok": False, ...}` result the model must explain."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return _failure("TOOL_NOT_FOUND", messages.TOOL_UNAVAILABLE)
    try:
        return handler(account, dict(args or {}))
    except BankingError as exc:
        return _failure(exc.code, str(exc))
    except Exception:  # noqa: BLE001 - an unexpected failure must not leak to the model
        return _failure("INTERNAL_ERROR", messages.OPERATION_FAILED)
