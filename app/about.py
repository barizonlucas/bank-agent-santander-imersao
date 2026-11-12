"""Public project information served by /api/about.

Exposes no key, no system prompt and no sensitive internals.
"""

from typing import Any

from app import messages
from app.agent.tools import DECLARATIONS
from app.config import (
    AUTHOR_LINKEDIN,
    AUTHOR_NAME,
    COURSE_NAME,
    COURSE_URL,
    GEMINI_FALLBACK_MODELS,
    GEMINI_MODEL,
    MAX_TOOL_ITERATIONS,
    PROJECT_NAME,
)


def build_about() -> dict[str, Any]:
    return {
        "project": {"name": PROJECT_NAME, "description": messages.PROJECT_DESCRIPTION},
        "stack": messages.STACK_ROWS,
        "llm": {
            "provider": "Google Gemini",
            "model": GEMINI_MODEL,
            "fallback_models": list(GEMINI_FALLBACK_MODELS),
            "usage": messages.LLM_USAGE,
            "temperature": 0,
            "max_tools_per_message": MAX_TOOL_ITERATIONS,
            "tools": [declaration.name for declaration in DECLARATIONS],
        },
        "author": {"name": AUTHOR_NAME, "linkedin": AUTHOR_LINKEDIN},
        "course": {"name": COURSE_NAME, "url": COURSE_URL},
    }
