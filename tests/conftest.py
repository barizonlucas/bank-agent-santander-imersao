import pytest

from app.bank.sessions import SessionStore
from app.governance.audit import AuditLog
from app.main import app


@pytest.fixture(autouse=True)
def isolar_dados(tmp_path):
    """Nenhum teste escreve em data/: sessões e auditoria vão para um diretório temporário."""
    app.state.sessions = SessionStore(tmp_path)
    app.state.audit = AuditLog(tmp_path / "audit.jsonl")
    yield
