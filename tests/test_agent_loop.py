"""Testa o loop do agente com um modelo FALSO — inclusive um modelo 'malicioso'
que tenta burlar as regras. O estado da conta deve permanecer correto."""

from decimal import Decimal

from app.agent.agent import BankAgent
from app.bank.sessions import Sessao
from app.config import MAX_ITERACOES_FERRAMENTAS
from tests.fake_gemini import FakeClient, chamada, texto


def rodar(roteiro, mensagens, sessao=None):
    sessao = sessao or Sessao()
    agente = BankAgent(client=FakeClient(roteiro), model="fake", reservas=("fake-reserva",))
    respostas = [agente.responder(sessao, m) for m in mensagens]
    return sessao, respostas


def test_deposito_via_ferramenta():
    s, [r] = rodar([[chamada("depositar", valor="250")], [texto("Depósito feito. Novo saldo: R$ 1.250,00")]],
                   ["Depositar 250 reais"])
    assert s.conta.saldo == Decimal("1250.00")
    assert r.chamadas[0].resultado["novo_saldo"] == "R$ 1.250,00"


def test_modelo_tenta_depositar_negativo():
    s, [r] = rodar([[chamada("depositar", valor="-50")], [texto("Não é possível.")]], ["Deposita -50"])
    assert s.conta.saldo == Decimal("1000.00")
    assert r.chamadas[0].resultado["ok"] is False


def test_modelo_tenta_auto_confirmar_no_mesmo_turno():
    """Modelo 'malicioso' prepara E confirma na mesma resposta, sem o usuário confirmar."""
    s = Sessao()

    class ModeloQueSeAutoConfirma(FakeClient):
        def generate_content(self, model, contents, config):
            if len(self.chamadas_modelo) == 1:  # após o `transferir`, tenta confirmar com o id real
                op = s.conta.pendente.operacao_id
                self.roteiro = [[chamada("confirmar_operacao", operacao_id=op)], [texto("Pronto!")]]
            return super().generate_content(model, contents, config)

    agente = BankAgent(client=ModeloQueSeAutoConfirma([[chamada("transferir", valor="100", destinatario="Ana")]]),
                       model="f")
    r = agente.responder(s, "Transferir 100 para a Ana")
    assert s.conta.saldo == Decimal("1000.00"), "não pode debitar sem confirmação do usuário"
    assert r.chamadas[1].resultado["erro"] == "CONFIRMACAO_REQUER_USUARIO"


def test_fluxo_transferencia_com_confirmacao_do_usuario():
    s = Sessao()
    a = BankAgent(client=FakeClient([[chamada("transferir", valor="100", destinatario="conta da Ana")],
                                     [texto("Confirma?")]]), model="f")
    a.responder(s, "Transferir 100 para a conta da Ana")
    op = s.conta.pendente.operacao_id
    a._client = FakeClient([[chamada("confirmar_operacao", operacao_id=op)], [texto("Feito.")]])
    a.responder(s, "confirmo")
    assert s.conta.saldo == Decimal("900.00")


def test_ferramenta_inexistente():
    s, [r] = rodar([[chamada("apagar_tudo")], [texto("Não posso.")]], ["Apaga tudo"])
    assert r.chamadas[0].resultado["erro"] == "FERRAMENTA_INEXISTENTE"
    assert s.conta.saldo == Decimal("1000.00")


def test_recusa_sem_ferramentas():
    s, [r] = rodar([[texto("Desculpe, só posso ajudar com operações da sua conta.")]], ["Me conta uma piada"])
    assert r.chamadas == [] and s.conta.saldo == Decimal("1000.00")


def test_limite_de_iteracoes():
    roteiro = [[chamada("consultar_saldo")]] * (MAX_ITERACOES_FERRAMENTAS + 2)
    s, [r] = rodar(roteiro, ["saldo"])
    assert r.erro == "LIMITE_ITERACOES"
    assert s.historico == []  # turno descartado do histórico


def test_erro_transitorio_e_repetido(monkeypatch):
    monkeypatch.setattr("app.agent.agent.time.sleep", lambda s: None)
    s, [r] = rodar([RuntimeError("503 UNAVAILABLE"), [chamada("depositar", valor="10")],
                    RuntimeError("429 RESOURCE_EXHAUSTED"), [texto("Feito.")]], ["Deposita 10"])
    assert r.erro is None and r.texto == "Feito."
    assert s.conta.saldo == Decimal("1010.00")  # depósito executado UMA vez só


def test_fallback_troca_de_modelo_sem_esperar(monkeypatch):
    esperas = []
    monkeypatch.setattr("app.agent.agent.time.sleep", esperas.append)
    cliente = FakeClient([RuntimeError("503 UNAVAILABLE"), [texto("Olá!")]])
    agente = BankAgent(client=cliente, model="principal", reservas=("reserva",))
    s = Sessao()
    r = agente.responder(s, "oi")
    assert r.texto == "Olá!" and r.modelo == "reserva"
    assert cliente.modelos_chamados == ["principal", "reserva"]
    assert esperas == []  # trocou de modelo na hora, sem dormir
    assert [c.role for c in s.historico] == ["user", "model"]


def test_fallback_em_504(monkeypatch):
    monkeypatch.setattr("app.agent.agent.time.sleep", lambda s: None)
    cliente = FakeClient([RuntimeError("504 DEADLINE_EXCEEDED"), [texto("Olá!")]])
    r = BankAgent(client=cliente, model="principal", reservas=("reserva",)).responder(Sessao(), "oi")
    assert r.modelo == "reserva" and r.texto == "Olá!"


def test_erro_nao_transitorio_nao_troca_de_modelo():
    cliente = FakeClient([RuntimeError("400 INVALID_ARGUMENT")])
    r = BankAgent(client=cliente, model="principal", reservas=("reserva",)).responder(Sessao(), "oi")
    assert r.tipo == "ERRO_API" and cliente.modelos_chamados == ["principal"]


def test_erro_da_api_mantem_estado_e_historico_limpo(monkeypatch):
    monkeypatch.setattr("app.agent.agent.time.sleep", lambda s: None)
    s, [r] = rodar([[chamada("depositar", valor="10")]] + [RuntimeError("429 RESOURCE_EXHAUSTED")] * 4,
                   ["Deposita 10"])
    assert s.conta.saldo == Decimal("1010.00")  # o depósito executado é mantido...
    assert "R$ 1.010,00" in r.texto              # ...e informado com o valor real da conta
    assert s.historico == []


def test_guardrail_entrada_nao_chama_modelo():
    s, [r1, r2] = rodar([], ["   ", "x" * 501])
    assert "digite" in r1.texto and "muito longa" in r2.texto


def test_guardrail_saida_barra_valor_alucinado():
    # O modelo consulta o saldo (R$ 1.000,00) mas ESCREVE outro valor.
    s, [r] = rodar([[chamada("consultar_saldo")], [texto("Seu saldo atual é de R$ 1.300,00.")]], ["Qual meu saldo?"])
    assert r.guardrail_saida  # valor barrado
    assert r.texto == "Seu saldo atual é R$ 1.000,00."  # resposta determinística
    assert "1.300" not in str(s.historico[-1])  # histórico guarda só a versão segura


def test_guardrail_saida_aprova_resposta_correta():
    s, [r] = rodar([[chamada("depositar", valor="250")], [texto("Pronto! Novo saldo: R$ 1.250,00.")]],
                   ["Depositar 250 reais"])
    assert r.guardrail_saida == [] and r.texto == "Pronto! Novo saldo: R$ 1.250,00."


def test_guardrail_saida_saldo_desatualizado_entre_turnos():
    s = Sessao()
    a = BankAgent(client=FakeClient([[chamada("depositar", valor="250")], [texto("Novo saldo: R$ 1.250,00.")]]),
                  model="f")
    a.responder(s, "Depositar 250")
    # No turno seguinte o modelo "lembra" errado em vez de consultar
    a._client = FakeClient([[texto("Seu saldo atual é de R$ 1.000,00.")]])
    r = a.responder(s, "E agora, qual meu saldo?")
    assert r.guardrail_saida and "R$ 1.250,00" in r.texto
