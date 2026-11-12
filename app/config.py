"""Configuração central do projeto (lida do .env)."""

import os
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Regras de negócio -------------------------------------------------------
SALDO_INICIAL = Decimal("1000.00")
LIMITE_POR_OPERACAO = Decimal("1000000.00")  # teto de segurança por operação
TAMANHO_MAX_DESTINATARIO = 60

# --- Persistência / auditoria ----------------------------------------------
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

# --- Agente / LLM -----------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")  # principal: rápido (~1 s/chamada)
# Modelos de reserva, usados na hora se o principal estiver sobrecarregado (429/503/timeout)
GEMINI_MODELOS_RESERVA = tuple(
    m.strip() for m in os.getenv("GEMINI_MODELOS_RESERVA", "gemini-3.5-flash-lite").split(",") if m.strip()
)
# tempo máximo de UMA chamada ao modelo (a API do Gemini exige no mínimo 10 s)
TIMEOUT_MODELO_S = max(10, int(os.getenv("TIMEOUT_MODELO_S", "10")))
MAX_ITERACOES_FERRAMENTAS = 5  # chamadas de ferramenta por mensagem do usuário
TAMANHO_MAX_MENSAGEM = 500
MAX_HISTORICO = 40  # mensagens mantidas no contexto do modelo

# --- Sobre o projeto (exibido em /api/about e no painel "Sobre") -----------
PROJETO_NOME = "Agente Bancário Restrito"
AUTOR_NOME = "Lucas Borges Corrêa"
AUTOR_LINKEDIN = "https://www.linkedin.com/in/lucasborgescorrea/"
CURSO_NOME = "Santander Imersão Digital 2026 — Formação de Especialista em IA (Santander + Alura)"
CURSO_URL = "https://app.santanderopenacademy.com/pt-BR/program/santander-imersao-digital-2026"
