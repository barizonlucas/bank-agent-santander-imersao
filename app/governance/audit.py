"""Trilha de auditoria e métricas de governança da IA.

Cada mensagem do usuário gera UM evento com: o que foi pedido, como o agente
respondeu (categoria), quais ferramentas rodaram, se houve suspeita de prompt
injection, se o guardrail de saída agiu, latência e saldo resultante.

A classificação é DETERMINÍSTICA (regras em código, não opinião do LLM), para
que as métricas sejam auditáveis. Os eventos ficam em memória (para o painel) e
em ``data/audit.jsonl`` (registro permanente, uma linha JSON por evento).
"""

from __future__ import annotations

import json
import re
import threading
from collections import Counter, deque
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.agent.output_guard import extrair_valores
from app.agent.prompt import MENSAGEM_RECUSA
from app.bank.money import format_brl

CATEGORIAS = {
    "OPERACAO": "Operação executada",
    "CONSULTA": "Consulta de saldo",
    "RECUSA_ESCOPO": "Recusa (fora do escopo)",
    "ESCLARECIMENTO": "Pedido de esclarecimento",
    "VALIDACAO_BLOQUEADA": "Bloqueada pela validação",
    "ENTRADA_BLOQUEADA": "Bloqueada na entrada",
    "GUARDRAIL_SAIDA": "Corrigida pelo guardrail de saída",
    "ERRO_TECNICO": "Erro técnico",
}

# Heurística de MEDIÇÃO (não bloqueia nada: quem protege são o prompt e as ferramentas).
_PADROES_INJECAO = [
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
_RE_INJECAO = re.compile("|".join(_PADROES_INJECAO), re.IGNORECASE | re.MULTILINE)
_FERRAMENTAS_ESCRITA = {"depositar", "confirmar_operacao"}


def suspeita_injecao(mensagem: str) -> bool:
    return bool(_RE_INJECAO.search(mensagem or ""))


def classificar(texto: str, ferramentas: list[dict], tipo: str | None, guardrail_saida: list[str]) -> str:
    if tipo == "ENTRADA_INVALIDA":
        return "ENTRADA_BLOQUEADA"
    if tipo in ("ERRO_API", "LIMITE_ITERACOES"):
        return "ERRO_TECNICO"
    if guardrail_saida:
        return "GUARDRAIL_SAIDA"
    ok = [f for f in ferramentas if f["ok"]]
    if any(f["nome"] in _FERRAMENTAS_ESCRITA | {"transferir", "cancelar_operacao"} for f in ok):
        return "OPERACAO"
    if any(not f["ok"] for f in ferramentas):
        return "VALIDACAO_BLOQUEADA"
    if ok:
        return "CONSULTA"
    if texto.strip().startswith(MENSAGEM_RECUSA.split(":")[0]):
        return "RECUSA_ESCOPO"
    return "ESCLARECIMENTO"


def _volume(ferramentas: list[dict]) -> dict[str, Decimal]:
    vol = {"depositos": Decimal("0"), "transferencias": Decimal("0")}
    for f in ferramentas:
        if not f["ok"]:
            continue
        chave, campo = {"depositar": ("depositos", "valor_depositado"),
                        "confirmar_operacao": ("transferencias", "valor_transferido")}.get(f["nome"], (None, None))
        if chave:
            valores = [v for _, v in extrair_valores(f["resultado"].get(campo, "")) if v is not None]
            vol[chave] += sum(valores, Decimal("0"))
    return vol


class AuditLog:
    def __init__(self, arquivo: Path | None = None, capacidade: int = 1000) -> None:
        self._eventos: deque[dict] = deque(maxlen=capacidade)
        self._lock = threading.Lock()
        self._arquivo = Path(arquivo) if arquivo else None
        if self._arquivo:
            self._arquivo.parent.mkdir(parents=True, exist_ok=True)
            self._recarregar()

    def _recarregar(self) -> None:
        if not self._arquivo.exists():
            return
        for linha in self._arquivo.read_text(encoding="utf-8").splitlines()[-self._eventos.maxlen:]:
            try:
                self._eventos.append(json.loads(linha))
            except json.JSONDecodeError:
                continue

    def registrar(self, *, sessao_hash: str, mensagem: str, texto: str, ferramentas: list[dict],
                  tipo: str | None, guardrail_saida: list[str], latencia_ms: int, modelo: str,
                  saldo_apos: str) -> dict:
        resumo_ferramentas = [
            {"nome": f["nome"], "args": f["args"], "ok": bool(f["resultado"].get("ok")),
             "erro": f["resultado"].get("erro"), "resultado": f["resultado"]}
            for f in ferramentas
        ]
        vol = _volume(resumo_ferramentas)
        evento = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "sessao": sessao_hash,
            "mensagem": (mensagem or "")[:200],
            "resposta": (texto or "")[:300],
            "categoria": classificar(texto or "", resumo_ferramentas, tipo, guardrail_saida),
            "ferramentas": [{k: v for k, v in f.items() if k != "resultado"} for f in resumo_ferramentas],
            "suspeita_injecao": suspeita_injecao(mensagem),
            "alterou_saldo": any(f["ok"] and f["nome"] in _FERRAMENTAS_ESCRITA for f in resumo_ferramentas),
            "guardrail_saida": guardrail_saida,
            "volume_depositos": str(vol["depositos"]),
            "volume_transferencias": str(vol["transferencias"]),
            "latencia_ms": latencia_ms,
            "modelo": modelo,
            "saldo_apos": saldo_apos,
        }
        with self._lock:
            self._eventos.append(evento)
            if self._arquivo:
                with self._arquivo.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(evento, ensure_ascii=False) + "\n")
        return evento

    def metricas(self, ultimos: int = 20) -> dict:
        with self._lock:
            eventos = list(self._eventos)
        total = len(eventos)
        cat = Counter(e["categoria"] for e in eventos)
        injecoes = [e for e in eventos if e["suspeita_injecao"]]
        erros_validacao = Counter(
            f["erro"] for e in eventos for f in e["ferramentas"] if not f["ok"] and f.get("erro")
        )
        latencias = sorted(e["latencia_ms"] for e in eventos if e["categoria"] != "ENTRADA_BLOQUEADA")
        dep = sum((Decimal(e["volume_depositos"]) for e in eventos), Decimal("0"))
        trf = sum((Decimal(e["volume_transferencias"]) for e in eventos), Decimal("0"))
        conversas_fora_escopo = cat["RECUSA_ESCOPO"]

        def pct(n: int) -> float:
            return round(100 * n / total, 1) if total else 0.0

        return {
            "total_mensagens": total,
            "sessoes": len({e["sessao"] for e in eventos}),
            "por_categoria": [
                {"categoria": c, "rotulo": CATEGORIAS[c], "total": cat[c], "pct": pct(cat[c])} for c in CATEGORIAS
            ],
            "operacoes": {
                "depositos": sum(1 for e in eventos for f in e["ferramentas"] if f["ok"] and f["nome"] == "depositar"),
                "transferencias": sum(
                    1 for e in eventos for f in e["ferramentas"] if f["ok"] and f["nome"] == "confirmar_operacao"
                ),
                "volume_depositos": format_brl(dep),
                "volume_transferencias": format_brl(trf),
            },
            "seguranca": {
                "recusas_fora_escopo": conversas_fora_escopo,
                "tentativas_injecao": len(injecoes),
                "injecoes_que_alteraram_saldo": sum(1 for e in injecoes if e["alterou_saldo"]),
                "validacoes_bloqueadas": dict(erros_validacao),
                "guardrail_saida_acionado": cat["GUARDRAIL_SAIDA"],
                "entradas_bloqueadas": cat["ENTRADA_BLOQUEADA"],
            },
            "desempenho": {
                "latencia_media_ms": round(sum(latencias) / len(latencias)) if latencias else 0,
                "latencia_p95_ms": latencias[min(len(latencias) - 1, int(len(latencias) * 0.95))] if latencias else 0,
                "taxa_erro_tecnico_pct": pct(cat["ERRO_TECNICO"]),
            },
            "eventos_recentes": [
                {k: e[k] for k in ("ts", "sessao", "mensagem", "categoria", "ferramentas", "suspeita_injecao",
                                   "guardrail_saida", "latencia_ms", "saldo_apos")}
                for e in reversed(eventos[-ultimos:])
            ],
        }
