"""Sessions: each session_id owns its account, the LLM context and the chat transcript.

Persistence: after every message, account + transcript are written to
``data/sessions/<hash>.json``. The conversation and balance therefore survive a
page reload and even a server restart, until the user clicks "reset session",
which deletes the file.
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
from typing import Any

from google.genai import types

from app.bank.account import Account
from app.config import MAX_HISTORY_MESSAGES

log = logging.getLogger("bank_agent.sessions")


@dataclass
class Session:
    account: Account = field(default_factory=Account)
    history: list[types.Content] = field(default_factory=list)  # LLM context
    transcript: list[dict[str, Any]] = field(default_factory=list)  # what the user sees in the chat
    trusted_amounts: set[Decimal] = field(default_factory=set)  # used by the output guardrail
    lock: threading.RLock = field(default_factory=threading.RLock)

    def record_exchange(self, message: str, reply: str, tools: list[dict[str, Any]], timestamp: str) -> None:
        with self.lock:
            self.transcript.append({"role": "user", "text": message, "ts": timestamp})
            self.transcript.append({"role": "bot", "text": reply, "tools": tools, "ts": timestamp})


def hash_session_id(session_id: str) -> str:
    """Non-reversible identifier used in file names and in the audit trail."""
    return hashlib.sha256(session_id.encode()).hexdigest()[:16]


def _history_from_transcript(transcript: list[dict[str, Any]]) -> list[types.Content]:
    """Rebuild the LLM context from TEXT only (the real state comes from the account)."""
    contents = [
        types.Content(
            role="user" if entry["role"] == "user" else "model", parts=[types.Part.from_text(text=entry["text"])]
        )
        for entry in transcript[-MAX_HISTORY_MESSAGES:]
        if entry.get("text")
    ]
    while contents and contents[0].role != "user":
        contents.pop(0)
    return contents


class SessionStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._dir = Path(data_dir) / "sessions" if data_dir else None
        if self._dir:
            self._dir.mkdir(parents=True, exist_ok=True)

    def _file_for(self, session_id: str) -> Path | None:
        return self._dir / f"{hash_session_id(session_id)}.json" if self._dir else None

    def _load(self, session_id: str) -> Session:
        file = self._file_for(session_id)
        if not file or not file.exists():
            return Session()
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            transcript = list(data.get("transcript", []))
            return Session(
                account=Account.from_dict(data["account"]),
                history=_history_from_transcript(transcript),
                transcript=transcript,
                trusted_amounts={Decimal(v) for v in data.get("trusted_amounts", [])},
            )
        except Exception:  # noqa: BLE001 - corrupt or tampered file: we do not trust it
            log.warning("invalid session file %s; starting a fresh session", file.name, exc_info=True)
            return Session()

    def get(self, session_id: str) -> Session:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = self._load(session_id)
            return self._sessions[session_id]

    def save(self, session_id: str) -> None:
        file = self._file_for(session_id)
        if not file:
            return
        session = self.get(session_id)
        with session.lock:
            data = {
                "account": session.account.to_dict(),
                "transcript": session.transcript,
                "trusted_amounts": sorted(str(v) for v in session.trusted_amounts),
            }
        tmp = file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, file)  # atomic write

    def reset(self, session_id: str) -> Session:
        with self._lock:
            file = self._file_for(session_id)
            if file and file.exists():
                file.unlink()
            self._sessions[session_id] = Session()
            return self._sessions[session_id]
