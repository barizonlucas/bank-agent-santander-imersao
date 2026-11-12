"""Thin gateway over the Gemini SDK: timeouts, model fallback and retries.

Why it exists (provenance): with the SDK defaults, one overloaded model could
stall a reply for 80+ seconds (the SDK retried 5x with backoff up to 60 s, and
our own retry loop stacked on top of that). Here the SDK retry is disabled and
we switch to the next model immediately; we only pause once, after every model
has failed in a round.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

from google import genai
from google.genai import types

from app.config import GEMINI_API_KEY, MODEL_TIMEOUT_SECONDS

log = logging.getLogger("bank_agent.llm")

ROUNDS = 2
PAUSE_BETWEEN_ROUNDS_SECONDS = 2
_TRANSIENT_MARKERS = (
    "429",
    "RESOURCE_EXHAUSTED",
    "500",
    "INTERNAL",
    "503",
    "UNAVAILABLE",
    "504",
    "DEADLINE_EXCEEDED",
    "timed out",
    "timeout",
    "Timeout",
)


def is_transient_error(exc: Exception) -> bool:
    description = f"{type(exc).__name__} {exc}"
    return any(marker in description for marker in _TRANSIENT_MARKERS)


class LlmGateway:
    def __init__(self, models: Sequence[str], client: Any | None = None) -> None:
        self._models = list(dict.fromkeys(models))  # primary first, no duplicates
        self._client = client

    @property
    def primary_model(self) -> str:
        return self._models[0]

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    @staticmethod
    def _build_client() -> genai.Client:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set. Copy .env.example to .env.")
        return genai.Client(
            api_key=GEMINI_API_KEY,
            http_options=types.HttpOptions(
                timeout=MODEL_TIMEOUT_SECONDS * 1000,
                retry_options=types.HttpRetryOptions(attempts=1),  # we own the retry policy
            ),
        )

    def generate(self, contents: list[types.Content], config: types.GenerateContentConfig) -> tuple[Any, str]:
        """Return (response, model_used). Calling the model is idempotent (it runs no
        tools), so repeating on transient errors is safe."""
        last_error: Exception | None = None
        attempts = self._models * ROUNDS
        for index, model in enumerate(attempts):
            if index and index % len(self._models) == 0:
                time.sleep(PAUSE_BETWEEN_ROUNDS_SECONDS)
            try:
                return self._call(model, contents, config), model
            except Exception as exc:  # noqa: BLE001
                self._reraise_unless_transient(model, exc)
                last_error = exc
        raise last_error  # type: ignore[misc]  # attempts is never empty

    def _call(self, model: str, contents: list[types.Content], config: types.GenerateContentConfig) -> Any:
        started = time.perf_counter()
        response = self.client.models.generate_content(model=model, contents=contents, config=config)
        log.info("model %s answered in %.2f s", model, time.perf_counter() - started)
        return response

    @staticmethod
    def _reraise_unless_transient(model: str, exc: Exception) -> None:
        if not is_transient_error(exc):
            raise exc
        log.warning("model %s unavailable (%s); trying the next one", model, str(exc)[:60])
