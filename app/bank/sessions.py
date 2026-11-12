"""Sessões: cada session_id tem sua conta, o contexto do LLM e a transcrição do chat.

Persistência: após cada mensagem, conta + transcrição são gravadas em
``data/sessions/<hash>.json``. Assim a conversa e o saldo sobrevivem a um
recarregamento da página e até a um reinício do servidor — até o usuário clicar
em "Reiniciar sessão", que apaga o arquivo.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from google.genai import types

from app.bank.account import Account
from app.config import MAX_HISTORICO

log = logging.getLogger("bank_agent.sessions")


@dataclass
class Sessao:
    conta: Account = field(default_factory=Account)
    historico: list = field(default_factory=list)  # contexto do LLM: list[types.Content]
    transcricao: list[dict] = field(default_factory=list)  # o que o usuário vê no chat
    valores_confiaveis: set[Decimal] = field(default_factory=set)  # usado pelo guardrail de saída
    lock: threading.RLock = field(default_factory=threading.RLock)

    def registrar_troca(self, mensagem: str, resposta: str, ferramentas: list[dict], ts: str) -> None:
        with self.lock:
            self.transcricao.append({"papel": "user", "texto": mensagem, "ts": ts})
            self.transcricao.append({"papel": "bot", "texto": resposta, "ferramentas": ferramentas, "ts": ts})


def hash_sessao(session_id: str) -> str:
    """Identificador não reversível usado em nomes de arquivo e na auditoria."""
    return hashlib.sha256(session_id.encode()).hexdigest()[:16]


def _historico_da_transcricao(transcricao: list[dict]) -> list:
    """Reconstrói o contexto do LLM só com os TEXTOS (o estado real vem da conta)."""
    conteudos = [
        types.Content(role="user" if m["papel"] == "user" else "model", parts=[types.Part.from_text(text=m["texto"])])
        for m in transcricao[-MAX_HISTORICO:]
        if m.get("texto")
    ]
    while conteudos and conteudos[0].role != "user":
        conteudos.pop(0)
    return conteudos


class SessionStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self._sessoes: dict[str, Sessao] = {}
        self._lock = threading.Lock()
        self._dir = Path(data_dir) / "sessions" if data_dir else None
        if self._dir:
            self._dir.mkdir(parents=True, exist_ok=True)

    def _arquivo(self, session_id: str) -> Path | None:
        return self._dir / f"{hash_sessao(session_id)}.json" if self._dir else None

    def _carregar(self, session_id: str) -> Sessao:
        arq = self._arquivo(session_id)
        if not arq or not arq.exists():
            return Sessao()
        try:
            dados = json.loads(arq.read_text(encoding="utf-8"))
            transcricao = list(dados.get("transcricao", []))
            return Sessao(
                conta=Account.from_dict(dados["conta"]),
                historico=_historico_da_transcricao(transcricao),
                transcricao=transcricao,
                valores_confiaveis={Decimal(v) for v in dados.get("valores_confiaveis", [])},
            )
        except Exception:  # noqa: BLE001 — arquivo corrompido/adulterado: não confiamos nele
            log.warning("sessão %s inválida no disco; iniciando uma nova", arq.name, exc_info=True)
            return Sessao()

    def obter(self, session_id: str) -> Sessao:
        with self._lock:
            if session_id not in self._sessoes:
                self._sessoes[session_id] = self._carregar(session_id)
            return self._sessoes[session_id]

    def salvar(self, session_id: str) -> None:
        arq = self._arquivo(session_id)
        if not arq:
            return
        sessao = self.obter(session_id)
        with sessao.lock:
            dados = {
                "conta": sessao.conta.to_dict(),
                "transcricao": sessao.transcricao,
                "valores_confiaveis": sorted(str(v) for v in sessao.valores_confiaveis),
            }
        tmp = arq.with_suffix(".tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, arq)  # gravação atômica

    def resetar(self, session_id: str) -> Sessao:
        with self._lock:
            arq = self._arquivo(session_id)
            if arq and arq.exists():
                arq.unlink()
            self._sessoes[session_id] = Sessao()
            return self._sessoes[session_id]
