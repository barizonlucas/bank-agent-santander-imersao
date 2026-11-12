"""Conta bancária fictícia — TODA a regra de negócio vive aqui.

O modelo de linguagem nunca altera o saldo diretamente: ele apenas pede, via
ferramentas, que esta classe execute uma operação. A classe valida tudo e é a
única fonte da verdade sobre saldo e extrato.

Transferências são feitas em DUAS ETAPAS, com a regra imposta em código:
  1. ``preparar_transferencia`` valida e cria uma operação pendente (não debita);
  2. ``confirmar_transferencia`` só é aceita em um TURNO POSTERIOR do usuário,
     ou seja, o usuário precisa ter enviado uma nova mensagem depois de ver o
     resumo. Isso impede que o modelo "se auto-confirme" na mesma resposta.
"""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.bank.money import ValorInvalidoError, format_brl, parse_valor
from app.config import SALDO_INICIAL, TAMANHO_MAX_DESTINATARIO

NOME_CONTA = "Conta Corrente Imersão"
NUMERO_CONTA = "0001 / 12345-6"


# --- Erros de negócio ---------------------------------------------------------
class OperacaoBancariaError(Exception):
    codigo = "ERRO_OPERACAO"


class SaldoInsuficienteError(OperacaoBancariaError):
    codigo = "SALDO_INSUFICIENTE"


class DestinoInvalidoError(OperacaoBancariaError):
    codigo = "DESTINATARIO_INVALIDO"


class SemOperacaoPendenteError(OperacaoBancariaError):
    codigo = "SEM_OPERACAO_PENDENTE"


class ConfirmacaoNaoAutorizadaError(OperacaoBancariaError):
    codigo = "CONFIRMACAO_REQUER_USUARIO"


# --- Modelos ------------------------------------------------------------------
@dataclass(frozen=True)
class Transacao:
    tipo: str  # "DEPOSITO" | "TRANSFERENCIA"
    valor: Decimal
    saldo_apos: Decimal
    destinatario: str | None = None
    data_hora: str = field(default_factory=lambda: datetime.now().strftime("%d/%m/%Y %H:%M:%S"))

    def to_dict(self) -> dict:
        return {
            "tipo": self.tipo,
            "valor": format_brl(self.valor),
            "saldo_apos": format_brl(self.saldo_apos),
            "destinatario": self.destinatario,
            "data_hora": self.data_hora,
        }


@dataclass(frozen=True)
class TransferenciaPendente:
    operacao_id: str
    valor: Decimal
    destinatario: str
    turno_criacao: int

    def to_dict(self) -> dict:
        return {
            "operacao_id": self.operacao_id,
            "valor": format_brl(self.valor),
            "destinatario": self.destinatario,
        }


_PADRAO_DESTINATARIO = re.compile(r"^[\wÀ-ÿ][\wÀ-ÿ .,'\-/]*$")
_AUTO_REFERENCIA = re.compile(r"(?i)\b(minha( própria| propria)? conta|eu mesmo|mim mesmo|para mim)\b")


def validar_destinatario(destinatario: object) -> str:
    if not isinstance(destinatario, str) or not destinatario.strip():
        raise DestinoInvalidoError("Informe para quem deseja transferir (ex.: 'conta da Ana').")
    nome = " ".join(destinatario.split())
    if len(nome) > TAMANHO_MAX_DESTINATARIO:
        raise DestinoInvalidoError(f"O destinatário deve ter no máximo {TAMANHO_MAX_DESTINATARIO} caracteres.")
    if not _PADRAO_DESTINATARIO.match(nome):
        raise DestinoInvalidoError("O destinatário contém caracteres inválidos.")
    if _AUTO_REFERENCIA.search(nome):
        raise DestinoInvalidoError("Não é possível transferir para a própria conta.")
    return nome


# --- Conta ---------------------------------------------------------------------
class Account:
    def __init__(self, saldo_inicial: Decimal = SALDO_INICIAL) -> None:
        self._saldo_inicial = parse_valor(saldo_inicial)
        self._saldo = self._saldo_inicial
        self._extrato: list[Transacao] = []
        self._pendente: TransferenciaPendente | None = None
        self._turno = 0
        self._lock = threading.RLock()

    # ---- leitura ----
    @property
    def saldo(self) -> Decimal:
        return self._saldo

    @property
    def pendente(self) -> TransferenciaPendente | None:
        return self._pendente

    @property
    def extrato(self) -> list[Transacao]:
        return list(self._extrato)

    def novo_turno_usuario(self) -> int:
        """Chamado pelo agente sempre que chega uma NOVA mensagem do usuário."""
        with self._lock:
            self._turno += 1
            return self._turno

    def consultar_saldo(self) -> dict:
        with self._lock:
            return {
                "ok": True,
                "conta": f"{NOME_CONTA} ({NUMERO_CONTA})",
                "saldo": format_brl(self._saldo),
            }

    # ---- depósito ----
    def depositar(self, valor: object) -> dict:
        with self._lock:
            v = parse_valor(valor)
            anterior = self._saldo
            self._saldo = anterior + v
            self._extrato.append(Transacao("DEPOSITO", v, self._saldo))
            self._checar_invariantes()
            return {
                "ok": True,
                "operacao": "DEPOSITO",
                "valor_depositado": format_brl(v),
                "saldo_anterior": format_brl(anterior),
                "novo_saldo": format_brl(self._saldo),
            }

    # ---- transferência (etapa 1) ----
    def preparar_transferencia(self, valor: object, destinatario: object) -> dict:
        with self._lock:
            v = parse_valor(valor)
            nome = validar_destinatario(destinatario)
            if v > self._saldo:
                raise SaldoInsuficienteError(
                    f"Saldo insuficiente: você tentou transferir {format_brl(v)}, "
                    f"mas o saldo disponível é {format_brl(self._saldo)}."
                )
            self._pendente = TransferenciaPendente(
                operacao_id=f"TRF-{uuid.uuid4().hex[:6].upper()}",
                valor=v,
                destinatario=nome,
                turno_criacao=self._turno,
            )
            return {
                "ok": True,
                "status": "AGUARDANDO_CONFIRMACAO_DO_USUARIO",
                "operacao_id": self._pendente.operacao_id,
                "origem": f"{NOME_CONTA} ({NUMERO_CONTA})",
                "destinatario": nome,
                "valor": format_brl(v),
                "saldo_atual": format_brl(self._saldo),
                "saldo_apos_transferencia": format_brl(self._saldo - v),
                "instrucao": "Mostre este resumo e peça ao usuário para responder 'confirmo' ou 'cancelar'. "
                "NÃO confirme por conta própria.",
            }

    # ---- transferência (etapa 2) ----
    def confirmar_transferencia(self, operacao_id: object) -> dict:
        with self._lock:
            p = self._exigir_pendente(operacao_id)
            if self._turno <= p.turno_criacao:
                raise ConfirmacaoNaoAutorizadaError(
                    "A confirmação precisa vir do usuário em uma nova mensagem. "
                    "Mostre o resumo e aguarde a resposta dele."
                )
            if p.valor > self._saldo:  # revalida: o saldo pode ter mudado
                self._pendente = None
                raise SaldoInsuficienteError(
                    f"Saldo insuficiente no momento da confirmação: saldo {format_brl(self._saldo)}, "
                    f"transferência {format_brl(p.valor)}. A operação foi cancelada."
                )
            anterior = self._saldo
            self._saldo = anterior - p.valor
            self._extrato.append(Transacao("TRANSFERENCIA", p.valor, self._saldo, p.destinatario))
            self._pendente = None
            self._checar_invariantes()
            return {
                "ok": True,
                "operacao": "TRANSFERENCIA",
                "status": "CONCLUIDA",
                "operacao_id": p.operacao_id,
                "origem": f"{NOME_CONTA} ({NUMERO_CONTA})",
                "destinatario": p.destinatario,
                "valor_transferido": format_brl(p.valor),
                "saldo_anterior": format_brl(anterior),
                "novo_saldo": format_brl(self._saldo),
            }

    def cancelar_transferencia(self, operacao_id: object) -> dict:
        with self._lock:
            p = self._exigir_pendente(operacao_id)
            self._pendente = None
            return {
                "ok": True,
                "status": "CANCELADA",
                "operacao_id": p.operacao_id,
                "saldo": format_brl(self._saldo),
            }

    # ---- auxiliares ----
    def _exigir_pendente(self, operacao_id: object) -> TransferenciaPendente:
        p = self._pendente
        if p is None:
            raise SemOperacaoPendenteError("Não há nenhuma transferência aguardando confirmação.")
        if not isinstance(operacao_id, str) or operacao_id.strip().upper() != p.operacao_id:
            raise SemOperacaoPendenteError(
                f"Identificador de operação inválido. A operação pendente é {p.operacao_id}."
            )
        return p

    def _checar_invariantes(self) -> None:
        """Defesa em profundidade: o saldo deve SEMPRE bater com o extrato."""
        depositos = sum((t.valor for t in self._extrato if t.tipo == "DEPOSITO"), Decimal("0"))
        transferencias = sum((t.valor for t in self._extrato if t.tipo == "TRANSFERENCIA"), Decimal("0"))
        esperado = self._saldo_inicial + depositos - transferencias
        # (if/raise em vez de assert: asserts somem quando o Python roda com -O)
        if self._saldo != esperado:
            raise RuntimeError("Inconsistência de saldo detectada")
        if self._saldo < 0:
            raise RuntimeError("Saldo negativo detectado")

    # ---- persistência ----
    def to_dict(self) -> dict:
        """Serializa o estado (valores como texto Decimal, nunca float)."""
        with self._lock:
            return {
                "saldo_inicial": str(self._saldo_inicial),
                "saldo": str(self._saldo),
                "turno": self._turno,
                "extrato": [
                    {"tipo": t.tipo, "valor": str(t.valor), "saldo_apos": str(t.saldo_apos),
                     "destinatario": t.destinatario, "data_hora": t.data_hora}
                    for t in self._extrato
                ],
                "pendente": None if self._pendente is None else {
                    "operacao_id": self._pendente.operacao_id, "valor": str(self._pendente.valor),
                    "destinatario": self._pendente.destinatario, "turno_criacao": self._pendente.turno_criacao,
                },
            }

    @classmethod
    def from_dict(cls, dados: dict) -> "Account":
        """Reconstrói a conta e VALIDA a consistência: um arquivo adulterado ou
        corrompido (saldo que não bate com o extrato, negativo...) é rejeitado."""
        conta = cls(Decimal(dados["saldo_inicial"]))
        conta._saldo = Decimal(dados["saldo"])
        conta._turno = int(dados.get("turno", 0))
        for t in dados.get("extrato", []):
            if t["tipo"] not in ("DEPOSITO", "TRANSFERENCIA"):
                raise ValueError("tipo de transação inválido")
            conta._extrato.append(Transacao(
                t["tipo"], parse_valor(t["valor"]), Decimal(t["saldo_apos"]), t.get("destinatario"), t["data_hora"]
            ))
        p = dados.get("pendente")
        if p:
            conta._pendente = TransferenciaPendente(
                p["operacao_id"], parse_valor(p["valor"]), validar_destinatario(p["destinatario"]),
                int(p["turno_criacao"]),
            )
        conta._checar_invariantes()
        return conta

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "conta": f"{NOME_CONTA} ({NUMERO_CONTA})",
                "saldo": format_brl(self._saldo),
                "extrato": [t.to_dict() for t in reversed(self._extrato)],
                "pendente": self._pendente.to_dict() if self._pendente else None,
            }


__all__ = [
    "Account",
    "OperacaoBancariaError",
    "SaldoInsuficienteError",
    "DestinoInvalidoError",
    "SemOperacaoPendenteError",
    "ConfirmacaoNaoAutorizadaError",
    "ValorInvalidoError",
]
