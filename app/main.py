"""FastAPI API + static frontend for the restricted banking agent.

Run:  uv run uvicorn app.main:app --reload   ->  http://localhost:8000
Dependencies (session store, audit log, agent) are injected into `create_app`,
so tests can build an app with fakes instead of patching globals.
"""

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.about import build_about
from app.agent.agent import AgentReply, BankAgent, build_default_agent
from app.bank.sessions import Session, SessionStore, hash_session_id
from app.config import DATA_DIR, PROJECT_NAME
from app.governance.audit import AuditLog
from app.logging_setup import configure_logging

STATIC_DIR = Path(__file__).parent / "static"
PITCH_DIR = Path(__file__).resolve().parent.parent / "pitch"

SESSION_ID_FIELD = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ChatRequest(BaseModel):
    session_id: str = SESSION_ID_FIELD
    message: str = Field(max_length=2000)  # the agent's guardrail enforces the real limit


class SessionRequest(BaseModel):
    session_id: str = SESSION_ID_FIELD


api = APIRouter()


def _public_tool_call(call) -> dict[str, Any]:
    return {"name": call.name, "args": call.args, "result": call.result}


def _state_of(session: Session) -> dict[str, Any]:
    return {**session.account.snapshot(), "messages": session.transcript}


def _audit_exchange(state, body: ChatRequest, session: Session, reply: AgentReply, tool_calls, latency_ms: int) -> None:
    state.audit.record(
        session_hash=hash_session_id(body.session_id), message=body.message, reply_text=reply.text,
        tool_calls=tool_calls, kind=reply.kind, output_guardrail_hits=reply.output_guardrail_hits,
        latency_ms=latency_ms, model=reply.model or state.agent.model,
        balance_after=session.account.snapshot()["balance"],
    )


@api.post("/api/chat")
def chat(body: ChatRequest, request: Request) -> dict[str, Any]:
    state = request.app.state
    session = state.sessions.get(body.session_id)
    started = time.perf_counter()
    reply = state.agent.reply_to(session, body.message)
    latency_ms = round((time.perf_counter() - started) * 1000)
    tool_calls = [_public_tool_call(call) for call in reply.tool_calls]
    session.record_exchange(body.message, reply.text, tool_calls, datetime.now().isoformat(timespec="seconds"))
    state.sessions.save(body.session_id)
    _audit_exchange(state, body, session, reply, tool_calls, latency_ms)
    # The account state comes from the backend, never from the model's text.
    return {"reply": reply.text, "tools": tool_calls, "output_guardrail": reply.output_guardrail_hits,
            **session.account.snapshot()}


@api.get("/api/state")
def get_state(session_id: str, request: Request) -> dict[str, Any]:
    validated = SessionRequest(session_id=session_id).session_id
    return _state_of(request.app.state.sessions.get(validated))


@api.post("/api/reset")
def reset(body: SessionRequest, request: Request) -> dict[str, Any]:
    # Deletes account and conversation. The audit trail is kept (governance).
    return _state_of(request.app.state.sessions.reset(body.session_id))


@api.get("/api/governance")
def governance_metrics(request: Request) -> dict[str, Any]:
    return {"model": request.app.state.agent.model, **request.app.state.audit.metrics()}


@api.get("/api/about")
def about() -> dict[str, Any]:
    return build_about()


@api.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@api.get("/governance")
def governance_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "governance.html")


@api.get("/governanca")
def legacy_governance_route() -> RedirectResponse:
    # Old Portuguese URL kept as a redirect only; it serves no data itself.
    return RedirectResponse("/governance", status_code=301)


@api.get("/pitch")
def pitch() -> FileResponse:
    return FileResponse(PITCH_DIR / "index.html")


def create_app(
    sessions: SessionStore | None = None, audit: AuditLog | None = None, agent: BankAgent | None = None
) -> FastAPI:
    app = FastAPI(title=PROJECT_NAME)
    app.state.sessions = sessions or SessionStore(DATA_DIR)  # account + chat persisted until "reset session"
    app.state.audit = audit or AuditLog(DATA_DIR / "audit.jsonl")  # audit trail (governance)
    app.state.agent = agent or build_default_agent()
    app.include_router(api)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


configure_logging()
app = create_app()
