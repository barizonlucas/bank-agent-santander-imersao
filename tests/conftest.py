import pytest
from fastapi.testclient import TestClient

from app.agent.agent import BankAgent
from app.bank.sessions import SessionStore
from app.governance.audit import AuditLog
from app.main import create_app


@pytest.fixture
def make_client(tmp_path):
    """Build an API client with an injected agent. Nothing writes to data/: sessions and
    the audit trail go to a temporary directory."""

    def build(agent: BankAgent | None = None) -> TestClient:
        app = create_app(SessionStore(tmp_path), AuditLog(tmp_path / "audit.jsonl"), agent)
        return TestClient(app)

    return build
