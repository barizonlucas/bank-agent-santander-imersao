"""Central configuration (read from the environment / .env)."""

import os
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Business rules ----------------------------------------------------------
INITIAL_BALANCE = Decimal("1000.00")
MAX_AMOUNT_PER_OPERATION = Decimal("1000000.00")  # safety ceiling for a single operation
MAX_RECIPIENT_LENGTH = 60

# --- Persistence / audit -----------------------------------------------------
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

# --- Agent / LLM -------------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")  # primary: fast (~1 s per call)
# Used immediately when the primary model is overloaded (429/5xx/timeout).
GEMINI_FALLBACK_MODELS = tuple(
    name.strip() for name in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.5-flash-lite").split(",") if name.strip()
)
# The Gemini API rejects deadlines under 10 s (HTTP 400), hence the floor.
MODEL_TIMEOUT_SECONDS = max(10, int(os.getenv("MODEL_TIMEOUT_SECONDS", "10")))
MAX_TOOL_ITERATIONS = 5  # tool calls allowed per user message
MAX_MESSAGE_LENGTH = 500
MAX_HISTORY_MESSAGES = 40  # messages kept in the model context

# --- About page (served by /api/about) ---------------------------------------
PROJECT_NAME = "Agente Bancário Restrito"
AUTHOR_NAME = "Lucas Borges Corrêa"
AUTHOR_LINKEDIN = "https://www.linkedin.com/in/lucasborgescorrea/"
COURSE_NAME = "Santander Imersão Digital 2026 — Formação de Especialista em IA (Santander + Alura)"
COURSE_URL = "https://app.santanderopenacademy.com/pt-BR/program/santander-imersao-digital-2026"
