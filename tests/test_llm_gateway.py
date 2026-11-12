"""Model fallback, retries and timeouts of the LLM gateway."""

import pytest

from app.agent.llm_gateway import LlmGateway, is_transient_error
from app.bank.sessions import Session
from tests.fake_gemini import FakeClient, make_agent, text


@pytest.fixture
def sleeps(monkeypatch):
    recorded = []
    monkeypatch.setattr("app.agent.llm_gateway.time.sleep", recorded.append)
    return recorded


def test_switches_model_immediately_on_a_transient_error(sleeps):
    client = FakeClient([RuntimeError("503 UNAVAILABLE"), [text("Olá!")]])
    session = Session()
    reply = make_agent(client, models=("primary", "backup")).reply_to(session, "oi")
    assert reply.text == "Olá!" and reply.model == "backup"
    assert client.models_called == ["primary", "backup"]
    assert sleeps == []  # switched at once, no sleeping
    assert [c.role for c in session.history] == ["user", "model"]


def test_deadline_exceeded_also_triggers_the_fallback(sleeps):
    client = FakeClient([RuntimeError("504 DEADLINE_EXCEEDED"), [text("Olá!")]])
    reply = make_agent(client, models=("primary", "backup")).reply_to(Session(), "oi")
    assert reply.model == "backup" and reply.text == "Olá!"


def test_non_transient_error_does_not_switch_model():
    client = FakeClient([RuntimeError("400 INVALID_ARGUMENT")])
    reply = make_agent(client, models=("primary", "backup")).reply_to(Session(), "oi")
    assert reply.kind == "API_ERROR" and client.models_called == ["primary"]


def test_pauses_once_after_every_model_failed_in_a_round(sleeps):
    client = FakeClient([RuntimeError("503 UNAVAILABLE")] * 2 + [[text("Olá!")]])
    reply = make_agent(client, models=("primary", "backup")).reply_to(Session(), "oi")
    assert reply.text == "Olá!" and len(sleeps) == 1


def test_gives_up_after_all_rounds(sleeps):
    gateway = LlmGateway(("primary", "backup"), client=FakeClient([RuntimeError("503 UNAVAILABLE")] * 4))
    with pytest.raises(RuntimeError, match="503"):
        gateway.generate([], None)


@pytest.mark.parametrize("message", ["429 RESOURCE_EXHAUSTED", "The read operation timed out", "500 INTERNAL"])
def test_transient_markers(message):
    assert is_transient_error(RuntimeError(message))
