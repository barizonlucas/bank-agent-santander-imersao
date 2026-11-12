"""Agent loop: Gemini with MANUAL function calling.

Flow for each user message:
  1. input guardrail (code, no LLM);
  2. register a new "user turn" on the account (basis of the confirmation rule);
  3. call the model; if it asks for tools we run them in Python and hand back the
     result; repeat until it answers in text (at most N iterations);
  4. OUTPUT guardrail: every R$ amount in the text must come from the tools or the
     account; otherwise the reply is replaced by a deterministic message;
  5. the frontend also receives the account snapshot straight from the backend,
     so the balance on screen never depends on text the model generated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from google.genai import types

from app import messages
from app.agent import output_guard, tools
from app.agent.guardrails import validate_user_message
from app.agent.llm_gateway import LlmGateway
from app.agent.prompt import SYSTEM_PROMPT
from app.bank.account import ToolResult
from app.bank.money import format_brl
from app.bank.sessions import Session
from app.config import GEMINI_FALLBACK_MODELS, GEMINI_MODEL, MAX_HISTORY_MESSAGES, MAX_TOOL_ITERATIONS

log = logging.getLogger("bank_agent")

# Reply kinds (None = normal flow)
INVALID_INPUT = "INVALID_INPUT"
API_ERROR = "API_ERROR"
ITERATION_LIMIT = "ITERATION_LIMIT"


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    result: ToolResult


@dataclass
class AgentReply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    error: str | None = None
    kind: str | None = None
    output_guardrail_hits: list[str] = field(default_factory=list)  # amounts the AI "invented"
    model: str | None = None  # model that actually answered (may be a fallback)


@dataclass
class _Turn:
    """Mutable state of one user message while the loop runs."""

    session: Session
    message: str
    history_start: int
    tool_calls: list[ToolCall] = field(default_factory=list)
    model_used: str | None = None


class BankAgent:
    def __init__(self, gateway: LlmGateway) -> None:
        self._gateway = gateway
        self._config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=tools.TOOLS,
            temperature=0,
            # We turn the SDK's automatic execution off: WE run the tools.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.AUTO)
            ),
        )

    @property
    def model(self) -> str:
        return self._gateway.primary_model

    # ------------------------------------------------------------------
    def reply_to(self, session: Session, message: str) -> AgentReply:
        input_error = validate_user_message(message)
        if input_error:
            return AgentReply(text=input_error, kind=INVALID_INPUT)
        with session.lock:
            turn = self._start_turn(session, message)
            try:
                return self._run_turn(turn)
            except Exception as exc:  # noqa: BLE001
                return self._failure_reply(turn, exc)

    @staticmethod
    def _start_turn(session: Session, message: str) -> _Turn:
        session.account.start_user_turn()
        turn = _Turn(session, message, history_start=len(session.history))
        session.history.append(types.Content(role="user", parts=[types.Part.from_text(text=message.strip())]))
        return turn

    def _run_turn(self, turn: _Turn) -> AgentReply:
        for _ in range(MAX_TOOL_ITERATIONS + 1):
            response, turn.model_used = self._gateway.generate(turn.session.history, self._config)
            content = _model_content(response)
            calls = [part.function_call for part in content.parts or [] if part.function_call]
            if not calls:
                return self._finish_with_text(turn, content)
            if len(turn.tool_calls) >= MAX_TOOL_ITERATIONS:
                break  # safety limit: avoids tool loops
            self._execute_tool_calls(turn, content, calls)
        return self._iteration_limit_reply(turn)

    def _execute_tool_calls(self, turn: _Turn, model_content: types.Content, calls: list[types.FunctionCall]) -> None:
        history = turn.session.history
        history.append(model_content)
        response_parts = []
        for call in calls:
            args = dict(call.args or {})
            result = tools.execute_tool(turn.session.account, call.name, args)
            log.info("tool call", extra={"tool": call.name, "tool_args": args, "tool_ok": result.get("ok")})
            turn.tool_calls.append(ToolCall(call.name, args, result))
            response_parts.append(types.Part.from_function_response(name=call.name, response={"result": result}))
        history.append(types.Content(role="user", parts=response_parts))

    def _finish_with_text(self, turn: _Turn, content: types.Content) -> AgentReply:
        text = _text_of(content) or messages.DEFAULT_REPLY
        text, blocked = self._apply_output_guardrail(turn, text)
        if blocked:  # the history keeps the SAFE version, never the hallucinated text
            content = types.Content(role="model", parts=[types.Part.from_text(text=text)])
        turn.session.history.append(content)
        _trim_history(turn.session)
        return AgentReply(text=text, tool_calls=turn.tool_calls, output_guardrail_hits=blocked, model=turn.model_used)

    def _apply_output_guardrail(self, turn: _Turn, text: str) -> tuple[str, list[str]]:
        session, account = turn.session, turn.session.account
        results = [call.result for call in turn.tool_calls]
        session.trusted_amounts |= output_guard.allowed_amounts(account, results, turn.message)
        blocked = output_guard.find_suspicious(
            text, session.trusted_amounts, output_guard.valid_balances(account, results)
        )
        if not blocked:
            return text, []
        log.warning("output guardrail blocked amounts %s in reply %r", blocked, text[:200])
        return output_guard.safe_reply(account, [(c.name, c.result) for c in turn.tool_calls]), blocked

    def _iteration_limit_reply(self, turn: _Turn) -> AgentReply:
        del turn.session.history[turn.history_start :]
        text = self._failure_text(turn, messages.CANNOT_COMPLETE_REQUEST)
        return AgentReply(text, turn.tool_calls, ITERATION_LIMIT, ITERATION_LIMIT, model=turn.model_used)

    def _failure_reply(self, turn: _Turn, exc: Exception) -> AgentReply:
        log.exception("model call failed")
        # Undo the turn in the HISTORY only: account state changes solely through tools,
        # and we tell the user exactly what was already executed.
        del turn.session.history[turn.history_start :]
        text = self._failure_text(turn, _failure_reason(exc))
        return AgentReply(text, turn.tool_calls, str(exc), API_ERROR, model=turn.model_used)

    @staticmethod
    def _failure_text(turn: _Turn, reason: str) -> str:
        wrote = any(c.result.get("ok") and c.name in tools.BALANCE_CHANGING_TOOLS for c in turn.tool_calls)
        balance = turn.session.account.get_balance()["balance"] if wrote else None  # from the account, not the model
        return messages.failure_reply(reason, balance)


def build_default_agent() -> BankAgent:
    return BankAgent(LlmGateway((GEMINI_MODEL, *GEMINI_FALLBACK_MODELS)))


# ---------------------------------------------------------------------------
def _failure_reason(exc: Exception) -> str:
    quota = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
    return messages.MODEL_QUOTA_EXHAUSTED if quota else messages.MODEL_UNAVAILABLE


def _model_content(response: Any) -> types.Content:
    if not response.candidates or response.candidates[0].content is None:
        return types.Content(role="model", parts=[types.Part.from_text(text=messages.DEFAULT_REPLY)])
    content = response.candidates[0].content
    if not content.parts:
        content.parts = [types.Part.from_text(text=messages.DEFAULT_REPLY)]
    content.role = "model"
    return content


def _text_of(content: types.Content) -> str:
    return "".join(part.text for part in content.parts or [] if part.text and not part.thought).strip()


def _trim_history(session: Session) -> None:
    """Keep the context lean, always starting at a user TEXT message."""
    history = session.history
    if len(history) <= MAX_HISTORY_MESSAGES:
        return
    cut = len(history) - MAX_HISTORY_MESSAGES
    while cut < len(history) and not (history[cut].role == "user" and _text_of(history[cut])):
        cut += 1
    del history[:cut]
