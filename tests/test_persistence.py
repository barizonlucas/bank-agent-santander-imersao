import json
from decimal import Decimal

from app.bank.account import Account
from app.bank.sessions import SessionStore, hash_sessao

SID = "sessao-persistencia-1"


def test_account_ida_e_volta():
    c = Account()
    c.depositar("250")
    c.novo_turno_usuario()
    c.preparar_transferencia("100", "Ana")
    c2 = Account.from_dict(json.loads(json.dumps(c.to_dict())))
    assert c2.saldo == Decimal("1250.00") and len(c2.extrato) == 1
    assert c2.pendente.valor == Decimal("100.00")
    c2.novo_turno_usuario()
    c2.confirmar_transferencia(c2.pendente.operacao_id)
    assert c2.saldo == Decimal("1150.00")


def test_sessao_sobrevive_a_reinicio_do_servidor(tmp_path):
    store = SessionStore(tmp_path)
    s = store.obter(SID)
    s.conta.depositar("250")
    s.registrar_troca("Depositar 250", "Feito! Novo saldo: R$ 1.250,00", [], "2026-11-12T10:00:00")
    store.salvar(SID)

    novo = SessionStore(tmp_path).obter(SID)  # "reinício": novo store lendo do disco
    assert novo.conta.saldo == Decimal("1250.00")
    assert [m["papel"] for m in novo.transcricao] == ["user", "bot"]
    assert [c.role for c in novo.historico] == ["user", "model"]  # contexto do LLM reconstruído


def test_arquivo_adulterado_e_rejeitado(tmp_path):
    store = SessionStore(tmp_path)
    store.obter(SID).conta.depositar("10")
    store.salvar(SID)
    arq = tmp_path / "sessions" / f"{hash_sessao(SID)}.json"
    dados = json.loads(arq.read_text())
    dados["conta"]["saldo"] = "999999.00"  # saldo que não bate com o extrato
    arq.write_text(json.dumps(dados))
    assert SessionStore(tmp_path).obter(SID).conta.saldo == Decimal("1000.00")  # sessão nova, segura


def test_reset_apaga_arquivo(tmp_path):
    store = SessionStore(tmp_path)
    store.obter(SID).conta.depositar("10")
    store.salvar(SID)
    store.resetar(SID)
    assert not (tmp_path / "sessions" / f"{hash_sessao(SID)}.json").exists()
    assert SessionStore(tmp_path).obter(SID).conta.saldo == Decimal("1000.00")
