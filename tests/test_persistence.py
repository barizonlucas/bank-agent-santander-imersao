import json
from decimal import Decimal

from app.bank.account import Account
from app.bank.sessions import SessionStore, hash_session_id

SESSION_ID = "session-persistence-1"


def test_account_round_trip():
    account = Account()
    account.deposit("250")
    account.start_user_turn()
    account.prepare_transfer("100", "Ana")
    restored = Account.from_dict(json.loads(json.dumps(account.to_dict())))
    assert restored.balance == Decimal("1250.00") and len(restored.statement) == 1
    assert restored.pending.amount == Decimal("100.00")
    restored.start_user_turn()
    restored.confirm_transfer(restored.pending.operation_id)
    assert restored.balance == Decimal("1150.00")


def test_session_survives_a_server_restart(tmp_path):
    store = SessionStore(tmp_path)
    session = store.get(SESSION_ID)
    session.account.deposit("250")
    session.record_exchange("Depositar 250", "Feito! Novo saldo: R$ 1.250,00", [], "2026-11-12T10:00:00")
    store.save(SESSION_ID)

    reloaded = SessionStore(tmp_path).get(SESSION_ID)  # "restart": a new store reading from disk
    assert reloaded.account.balance == Decimal("1250.00")
    assert [m["role"] for m in reloaded.transcript] == ["user", "bot"]
    assert [c.role for c in reloaded.history] == ["user", "model"]  # LLM context rebuilt


def test_tampered_file_is_rejected(tmp_path):
    store = SessionStore(tmp_path)
    store.get(SESSION_ID).account.deposit("10")
    store.save(SESSION_ID)
    file = tmp_path / "sessions" / f"{hash_session_id(SESSION_ID)}.json"
    data = json.loads(file.read_text())
    data["account"]["balance"] = "999999.00"  # balance that does not match the statement
    file.write_text(json.dumps(data))
    assert SessionStore(tmp_path).get(SESSION_ID).account.balance == Decimal("1000.00")  # fresh, safe session


def test_reset_deletes_the_file(tmp_path):
    store = SessionStore(tmp_path)
    store.get(SESSION_ID).account.deposit("10")
    store.save(SESSION_ID)
    store.reset(SESSION_ID)
    assert not (tmp_path / "sessions" / f"{hash_session_id(SESSION_ID)}.json").exists()
    assert SessionStore(tmp_path).get(SESSION_ID).account.balance == Decimal("1000.00")
