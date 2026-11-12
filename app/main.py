"""API FastAPI + frontend estático do Agente Bancário Restrito.

Rodar:  uv run uvicorn app.main:app --reload   ->  http://localhost:8000
"""

import logging
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent.agent import BankAgent
from app.agent.tools import DECLARACOES
from app.bank.sessions import SessionStore, hash_sessao
from app.config import (
    AUTOR_LINKEDIN, AUTOR_NOME, CURSO_NOME, CURSO_URL, DATA_DIR, GEMINI_MODEL, GEMINI_MODELOS_RESERVA, MAX_ITERACOES_FERRAMENTAS,
    PROJETO_NOME,
)
from app.governance.audit import AuditLog

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC = Path(__file__).parent / "static"
PITCH = Path(__file__).resolve().parent.parent / "pitch"

app = FastAPI(title="Agente Bancário Restrito")
app.state.sessions = SessionStore(DATA_DIR)          # conta + conversa persistidas até "Reiniciar sessão"
app.state.audit = AuditLog(DATA_DIR / "audit.jsonl")  # trilha de auditoria (governança)
app.state.agent = BankAgent()


class ChatIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    message: str = Field(max_length=2000)  # o guardrail do agente aplica o limite real


class SessionIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


def _estado(sessao) -> dict:
    return {**sessao.conta.snapshot(), "mensagens": sessao.transcricao}


@app.post("/api/chat")
def chat(body: ChatIn) -> dict:
    sessoes: SessionStore = app.state.sessions
    sessao = sessoes.obter(body.session_id)
    inicio = time.perf_counter()
    resposta = app.state.agent.responder(sessao, body.message)
    latencia_ms = round((time.perf_counter() - inicio) * 1000)

    ferramentas = [{"nome": c.nome, "args": c.args, "resultado": c.resultado} for c in resposta.chamadas]
    sessao.registrar_troca(body.message, resposta.texto, ferramentas, datetime.now().isoformat(timespec="seconds"))
    sessoes.salvar(body.session_id)
    app.state.audit.registrar(
        sessao_hash=hash_sessao(body.session_id), mensagem=body.message, texto=resposta.texto,
        ferramentas=ferramentas, tipo=resposta.tipo, guardrail_saida=resposta.guardrail_saida,
        latencia_ms=latencia_ms, modelo=resposta.modelo or app.state.agent.model, saldo_apos=sessao.conta.snapshot()["saldo"],
    )
    return {
        "reply": resposta.texto,
        "tools": ferramentas,
        "guardrail_saida": resposta.guardrail_saida,
        **sessao.conta.snapshot(),  # estado vindo do backend, nunca do texto do LLM
    }


@app.get("/api/state")
def state(session_id: str) -> dict:
    return _estado(app.state.sessions.obter(SessionIn(session_id=session_id).session_id))


@app.post("/api/reset")
def reset(body: SessionIn) -> dict:
    # Apaga conta e conversa da sessão. A trilha de auditoria é mantida (governança).
    return _estado(app.state.sessions.resetar(body.session_id))


@app.get("/api/governance")
def governance() -> dict:
    return {"modelo": app.state.agent.model, **app.state.audit.metricas()}


@app.get("/api/about")
def about() -> dict:
    """Informações públicas do projeto. Não expõe chave, prompt nem detalhes internos sensíveis."""
    return {
        "projeto": {
            "nome": PROJETO_NOME,
            "descricao": "Agente de IA que gerencia uma conta bancária fictícia (saldo inicial R$ 1.000,00) "
            "e executa somente três operações: consultar saldo, depositar e transferir.",
        },
        "stack": [
            {"camada": "Backend", "tecnologia": "Python 3.11+ · FastAPI · Uvicorn"},
            {"camada": "IA", "tecnologia": "SDK google-genai (Gemini API)"},
            {"camada": "Frontend", "tecnologia": "HTML · CSS · JavaScript (sem frameworks)"},
            {"camada": "Regras de negócio", "tecnologia": "Python + Decimal (validação nas ferramentas)"},
            {"camada": "Testes", "tecnologia": "pytest (unitários, loop com modelo falso, API) + evals com modelo real"},
            {"camada": "Ambiente", "tecnologia": "uv (dependências) · python-dotenv"},
        ],
        "llm": {
            "provedor": "Google Gemini",
            "modelo": GEMINI_MODEL,
            "modelos_reserva": list(GEMINI_MODELOS_RESERVA),
            "uso": "Function calling manual: o modelo só interpreta a intenção e escolhe a ferramenta; "
            "cálculo, validação e saldo ficam no código.",
            "temperatura": 0,
            "max_ferramentas_por_mensagem": MAX_ITERACOES_FERRAMENTAS,
            "ferramentas": [d.name for d in DECLARACOES],
        },
        "autor": {"nome": AUTOR_NOME, "linkedin": AUTOR_LINKEDIN},
        "curso": {"nome": CURSO_NOME, "url": CURSO_URL},
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/governanca")
def governanca_page() -> FileResponse:
    return FileResponse(STATIC / "governanca.html")


@app.get("/pitch")
def pitch() -> FileResponse:
    return FileResponse(PITCH / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
