"""Audit trail and AI-governance metrics.

Each user message produces ONE event with: what was asked, how the agent replied
(category), which tools ran, whether prompt injection was suspected, whether the
output guardrail acted, latency and the resulting balance.

Classification is DETERMINISTIC (rules in code, not the LLM's opinion), so the
metrics are auditable. Events are kept in memory (for the dashboard) and in
``data/audit.jsonl`` (permanent record, one JSON line per event).
"""

from __future__ import annotations

import json
import re
import threading
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app import messages
from app.agent import agent as agent_module
from app.agent import tools
from app.agent.output_guard import extract_amounts
from app.bank.money import format_brl

CATEGORIES = tuple(messages.CATEGORY_LABELS)
_OPERATION_TOOLS = tools.BALANCE_CHANGING_TOOLS | {tools.TRANSFER, tools.CANCEL_OPERATION}

# Volume tracking: tool name -> (volume bucket, result field holding the amount).
_VOLUME_FIELDS = {
    tools.DEPOSIT: ("deposits", "deposited_amount"),
    tools.CONFIRM_OPERATION: ("transfers", "transferred_amount"),
}

# MEASUREMENT heuristic (it blocks nothing: the prompt and the tools protect).
# The patterns match Portuguese user input.
_INJECTION_PATTERNS = [
    r"ignor\w*\s+(?:\w+\s+){0,3}(instru|regra|orienta)",
    r"esque[cç]a\s+(?:\w+\s+){0,3}(instru|regra)",
    r"modo\s+(administrador|admin|desenvolvedor|dev|deus|god|debug|irrestrito)",
    r"system\s*prompt|prompt\s+(do\s+)?sistema|suas\s+instru[cç][oõ]es|instru[cç][oõ]es\s+(que\s+voc[eê]\s+recebeu|iniciais)",
    r"\bjailbreak\b|\bDAN\b",
    r"sou\s+o\s+(gerente|desenvolvedor|administrador|dono|diretor|professor)",
    r"aja\s+como|finja\s+(ser|que)|a\s+partir\s+de\s+agora\s+voc[eê]",
    r"^\s*(sistema|system)\s*:",
    r"(altere|mude|defina|zere)\s+(o\s+)?saldo",
]
_INJECTION_REGEX = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE | re.MULTILINE)


def is_injection_suspected(message: str) -> bool:
    return bool(_INJECTION_REGEX.search(message or ""))


def classify(
    reply_text: str, tool_summaries: list[dict[str, Any]], kind: str | None, output_guardrail_hits: list[str]
) -> str:
    """Map one exchange to exactly one category (first matching rule wins)."""
    if kind == agent_module.INVALID_INPUT:
        return "INPUT_BLOCKED"
    if kind in (agent_module.API_ERROR, agent_module.ITERATION_LIMIT):
        return "TECHNICAL_ERROR"
    if output_guardrail_hits:
        return "OUTPUT_GUARDRAIL"
    return _classify_by_tools(reply_text, tool_summaries)


def _classify_by_tools(reply_text: str, tool_summaries: list[dict[str, Any]]) -> str:
    succeeded = [t for t in tool_summaries if t["ok"]]
    if any(t["name"] in _OPERATION_TOOLS for t in succeeded):
        return "OPERATION"
    if any(not t["ok"] for t in tool_summaries):
        return "VALIDATION_BLOCKED"
    if succeeded:
        return "BALANCE_QUERY"
    if reply_text.strip().startswith(messages.SCOPE_REFUSAL.split(":")[0]):
        return "OUT_OF_SCOPE_REFUSAL"
    return "CLARIFICATION"


def _volumes(tool_summaries: list[dict[str, Any]]) -> dict[str, Decimal]:
    volumes = {"deposits": Decimal("0"), "transfers": Decimal("0")}
    for tool in tool_summaries:
        bucket, field_name = _VOLUME_FIELDS.get(tool["name"], (None, None))
        if tool["ok"] and bucket:
            amounts = [a for _, a in extract_amounts(tool["result"].get(field_name, "")) if a is not None]
            volumes[bucket] += sum(amounts, Decimal("0"))
    return volumes


def _summarize_tools(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": call["name"],
            "args": call["args"],
            "ok": bool(call["result"].get("ok")),
            "error": call["result"].get("error"),
            "result": call["result"],
        }
        for call in tool_calls
    ]


@dataclass(frozen=True)
class Exchange:
    """Everything the audit needs to know about one user message and the agent's answer."""

    session_hash: str
    message: str
    reply_text: str
    tool_calls: list[dict[str, Any]]
    kind: str | None
    output_guardrail_hits: list[str]
    latency_ms: int
    model: str
    balance_after: str


def _describe_exchange(exchange: Exchange, summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """The part of an audit event that is derived from the exchange itself."""
    volumes = _volumes(summaries)
    category = classify(exchange.reply_text or "", summaries, exchange.kind, exchange.output_guardrail_hits)
    return {
        "message": (exchange.message or "")[:200],
        "reply": (exchange.reply_text or "")[:300],
        "category": category,
        "tools": [{k: v for k, v in t.items() if k != "result"} for t in summaries],
        "injection_suspected": is_injection_suspected(exchange.message),
        "changed_balance": any(t["ok"] and t["name"] in tools.BALANCE_CHANGING_TOOLS for t in summaries),
        "output_guardrail": exchange.output_guardrail_hits,
        "deposit_volume": str(volumes["deposits"]),
        "transfer_volume": str(volumes["transfers"]),
    }


class AuditLog:
    def __init__(self, file: Path | None = None, capacity: int = 1000) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._file = Path(file) if file else None
        if self._file:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._reload()

    def _reload(self) -> None:
        if not self._file.exists():
            return
        for line in self._file.read_text(encoding="utf-8").splitlines()[-self._events.maxlen :]:
            try:
                self._events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    def record(self, exchange: Exchange) -> dict[str, Any]:
        summaries = _summarize_tools(exchange.tool_calls)
        event = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "session": exchange.session_hash,
            **_describe_exchange(exchange, summaries),
            "latency_ms": exchange.latency_ms,
            "model": exchange.model,
            "balance_after": exchange.balance_after,
        }
        self._append(event)
        return event

    def _append(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.append(event)
            if self._file:
                with self._file.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def metrics(self, recent: int = 20) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)
        counts = Counter(e["category"] for e in events)
        return {
            "total_messages": len(events),
            "sessions": len({e["session"] for e in events}),
            "by_category": _category_breakdown(counts, len(events)),
            "operations": _operation_totals(events),
            "security": _security_summary(events, counts),
            "performance": _performance_summary(events, counts),
            "recent_events": _recent_events(events, recent),
        }


# ---- metric builders (pure functions over the event list) ------------------------
def _percent(part: int, total: int) -> float:
    return round(100 * part / total, 1) if total else 0.0


def _category_breakdown(counts: Counter, total: int) -> list[dict[str, Any]]:
    return [
        {"category": c, "label": messages.CATEGORY_LABELS[c], "total": counts[c], "pct": _percent(counts[c], total)}
        for c in CATEGORIES
    ]


def _count_successful(events: list[dict[str, Any]], tool_name: str) -> int:
    return sum(1 for e in events for t in e["tools"] if t["ok"] and t["name"] == tool_name)


def _operation_totals(events: list[dict[str, Any]]) -> dict[str, Any]:
    deposit_volume = sum((Decimal(e["deposit_volume"]) for e in events), Decimal("0"))
    transfer_volume = sum((Decimal(e["transfer_volume"]) for e in events), Decimal("0"))
    return {
        "deposits": _count_successful(events, tools.DEPOSIT),
        "transfers": _count_successful(events, tools.CONFIRM_OPERATION),
        "deposit_volume": format_brl(deposit_volume),
        "transfer_volume": format_brl(transfer_volume),
    }


def _security_summary(events: list[dict[str, Any]], counts: Counter) -> dict[str, Any]:
    injections = [e for e in events if e["injection_suspected"]]
    validation_errors = Counter(t["error"] for e in events for t in e["tools"] if not t["ok"] and t.get("error"))
    return {
        "out_of_scope_refusals": counts["OUT_OF_SCOPE_REFUSAL"],
        "injection_attempts": len(injections),
        "injections_that_changed_balance": sum(1 for e in injections if e["changed_balance"]),
        "blocked_validations": dict(validation_errors),
        "output_guardrail_triggered": counts["OUTPUT_GUARDRAIL"],
        "blocked_inputs": counts["INPUT_BLOCKED"],
    }


def _performance_summary(events: list[dict[str, Any]], counts: Counter) -> dict[str, Any]:
    latencies = sorted(e["latency_ms"] for e in events if e["category"] != "INPUT_BLOCKED")
    p95_index = min(len(latencies) - 1, int(len(latencies) * 0.95)) if latencies else 0
    return {
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        "p95_latency_ms": latencies[p95_index] if latencies else 0,
        "technical_error_rate_pct": _percent(counts["TECHNICAL_ERROR"], len(events)),
    }


_RECENT_FIELDS = (
    "ts",
    "session",
    "message",
    "category",
    "tools",
    "injection_suspected",
    "output_guardrail",
    "latency_ms",
    "balance_after",
)


def _recent_events(events: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return [{k: e[k] for k in _RECENT_FIELDS} for e in reversed(events[-limit:])]
