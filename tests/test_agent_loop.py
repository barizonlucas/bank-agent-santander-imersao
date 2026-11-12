"""Tests the agent loop with a FAKE model, including a 'malicious' model that tries
to bypass the rules. The account state must stay correct."""

from decimal import Decimal

from app.agent import agent as agent_module
from app.bank.sessions import Session
from app.config import MAX_TOOL_ITERATIONS
from tests.fake_gemini import FakeClient, make_agent, text, tool_call


def run(script, messages, session=None):
    session = session or Session()
    agent = make_agent(FakeClient(script))
    return session, [agent.reply_to(session, message) for message in messages]


def test_deposit_through_the_tool():
    session, [reply] = run(
        [[tool_call("deposit", amount="250")], [text("Depósito feito. Novo saldo: R$ 1.250,00")]],
        ["Depositar 250 reais"],
    )
    assert session.account.balance == Decimal("1250.00")
    assert reply.tool_calls[0].result["new_balance"] == "R$ 1.250,00"


def test_model_tries_a_negative_deposit():
    session, [reply] = run([[tool_call("deposit", amount="-50")], [text("Não é possível.")]], ["Deposita -50"])
    assert session.account.balance == Decimal("1000.00")
    assert reply.tool_calls[0].result["ok"] is False


def test_model_tries_to_self_confirm_in_the_same_turn():
    """A 'malicious' model prepares AND confirms in one reply, without the user confirming."""
    session = Session()

    class SelfConfirmingModel(FakeClient):
        def generate_content(self, model, contents, config):
            if len(self.model_calls) == 1:  # after `transfer`, it tries to confirm with the real id
                operation_id = session.account.pending.operation_id
                self.script = [[tool_call("confirm_operation", operation_id=operation_id)], [text("Pronto!")]]
            return super().generate_content(model, contents, config)

    agent = make_agent(SelfConfirmingModel([[tool_call("transfer", amount="100", recipient="Ana")]]))
    reply = agent.reply_to(session, "Transferir 100 para a Ana")
    assert session.account.balance == Decimal("1000.00"), "must not debit without the user's confirmation"
    assert reply.tool_calls[1].result["error"] == "CONFIRMATION_REQUIRES_USER"


def test_transfer_flow_with_user_confirmation():
    session = Session()
    client = FakeClient([[tool_call("transfer", amount="100", recipient="conta da Ana")], [text("Confirma?")]])
    agent = make_agent(client)
    agent.reply_to(session, "Transferir 100 para a conta da Ana")
    operation_id = session.account.pending.operation_id
    client.script = [[tool_call("confirm_operation", operation_id=operation_id)], [text("Feito.")]]
    agent.reply_to(session, "confirmo")
    assert session.account.balance == Decimal("900.00")


def test_unknown_tool():
    session, [reply] = run([[tool_call("delete_everything")], [text("Não posso.")]], ["Apaga tudo"])
    assert reply.tool_calls[0].result["error"] == "TOOL_NOT_FOUND"
    assert session.account.balance == Decimal("1000.00")


def test_refusal_uses_no_tools():
    session, [reply] = run([[text("Desculpe, só posso ajudar com operações da sua conta.")]], ["Me conta uma piada"])
    assert reply.tool_calls == [] and session.account.balance == Decimal("1000.00")


def test_iteration_limit():
    script = [[tool_call("get_balance")]] * (MAX_TOOL_ITERATIONS + 2)
    session, [reply] = run(script, ["saldo"])
    assert reply.kind == agent_module.ITERATION_LIMIT
    assert session.history == []  # the turn is discarded from the history


def test_api_failure_keeps_the_state_and_a_clean_history(monkeypatch):
    monkeypatch.setattr("app.agent.llm_gateway.time.sleep", lambda seconds: None)
    session, [reply] = run(
        [[tool_call("deposit", amount="10")]] + [RuntimeError("429 RESOURCE_EXHAUSTED")] * 4, ["Deposita 10"]
    )
    assert session.account.balance == Decimal("1010.00")  # the executed deposit is kept...
    assert "R$ 1.010,00" in reply.text  # ...and reported with the real account value
    assert session.history == []


def test_input_guardrail_does_not_call_the_model():
    client = FakeClient([])
    agent = make_agent(client)
    empty = agent.reply_to(Session(), "   ")
    too_long = agent.reply_to(Session(), "x" * 501)
    assert "digite" in empty.text and "muito longa" in too_long.text
    assert client.models_called == []


def test_output_guardrail_blocks_a_hallucinated_amount():
    # The model checks the balance (R$ 1.000,00) but WRITES another amount.
    session, [reply] = run(
        [[tool_call("get_balance")], [text("Seu saldo atual é de R$ 1.300,00.")]], ["Qual meu saldo?"]
    )
    assert reply.output_guardrail_hits
    assert reply.text == "Seu saldo atual é R$ 1.000,00."  # deterministic reply
    assert "1.300" not in str(session.history[-1])  # the history keeps only the safe version


def test_output_guardrail_approves_a_correct_reply():
    _, [reply] = run(
        [[tool_call("deposit", amount="250")], [text("Pronto! Novo saldo: R$ 1.250,00.")]], ["Depositar 250 reais"]
    )
    assert reply.output_guardrail_hits == [] and reply.text == "Pronto! Novo saldo: R$ 1.250,00."


def test_output_guardrail_catches_a_stale_balance_across_turns():
    session = Session()
    client = FakeClient([[tool_call("deposit", amount="250")], [text("Novo saldo: R$ 1.250,00.")]])
    agent = make_agent(client)
    agent.reply_to(session, "Depositar 250")
    client.script = [[text("Seu saldo atual é de R$ 1.000,00.")]]  # next turn the model "remembers" wrongly
    reply = agent.reply_to(session, "E agora, qual meu saldo?")
    assert reply.output_guardrail_hits and "R$ 1.250,00" in reply.text
