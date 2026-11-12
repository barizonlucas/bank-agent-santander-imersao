"""Fake Gemini client: returns scripted replies (no network)."""

from typing import Any

from google.genai import types

from app.agent.agent import BankAgent
from app.agent.llm_gateway import LlmGateway


def tool_call(name: str, **args: Any) -> types.Part:
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


def text(content: str) -> types.Part:
    return types.Part.from_text(text=content)


class _Response:
    def __init__(self, parts: list[types.Part]) -> None:
        self.candidates = [types.Candidate(content=types.Content(role="model", parts=parts))]


class FakeClient:
    """`script` has one item per model call: a list of Parts, or an Exception to raise."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.model_calls: list[list[types.Content]] = []
        self.models_called: list[str] = []
        self.models = self  # mimics `client.models.generate_content(...)`

    def generate_content(self, model: str, contents: list[types.Content], config: Any) -> _Response:
        self.model_calls.append(list(contents))
        self.models_called.append(model)
        if not self.script:
            raise AssertionError("script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Response(item)


def make_agent(client: FakeClient, models: tuple[str, ...] = ("fake",)) -> BankAgent:
    return BankAgent(LlmGateway(models, client=client))
