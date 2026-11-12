# CLAUDE.md — Restricted Banking Agent

Fictitious bank account managed by a Gemini agent that may ONLY check balance, deposit and transfer.
Everything else must be refused. Money is `Decimal`, never `float`.

## Commands
- Install: `uv sync`
- Tests (one command, no setup, no network): `uv run pytest`
- Lint + format: `uv run ruff check . && uv run ruff format .` (run before every commit)
- Run: `uv run uvicorn app.main:app --reload` → http://localhost:8000 (needs `.env`, see `.env.example`)
- Live evals against the real model (uses API quota): `uv run python -m evals.run_evals`

## Language (hard rule)
- ALL code is in English: identifiers, comments, docstrings, log messages, exception messages, tests,
  commit messages, README, JSON keys, routes, tool names, enum values.
- Portuguese is allowed ONLY as string VALUES that the end user reads or that match Portuguese input:
  `app/messages.py` (all user-facing text), `app/agent/prompt.py` (system prompt), regexes that parse
  Portuguese text (`R$ 1.234,56`, injection patterns), visible text in `app/static/*`, and `pitch/`.
- New user-facing text goes in `app/messages.py`, never inline in logic.
- `tests/test_code_style.py` fails the build on Portuguese identifiers/comments.

## Code style
- Functions: at most 20 lines of code. Split if longer. One thing per function (SRP).
- Files under 500 lines; split by responsibility.
- Names are specific and unique (grep-friendly). Avoid `data`, `handler`, `Manager`, `utils`.
- Explicit types on every function. No bare `dict` for domain shapes when a `TypedDict`/dataclass fits.
- No duplication: extract shared logic (e.g. `tools.BALANCE_CHANGING_TOOLS` is the single source).
- Early returns over nested ifs. At most 2 levels of control-flow nesting.
- Exception messages include the offending value and the expected shape.
- Prefer dispatch tables over long `if` chains (`tools._HANDLERS`, `output_guard._SAFE_LINES`).
- Formatting is `ruff format`. Do not discuss style beyond that.

## Comments
- Keep existing comments when refactoring: they carry intent and provenance.
- Write WHY (decision, bug, upstream constraint), never WHAT. No `# increment counter` above `i += 1`.
- Docstrings on public functions: intent, plus a usage example when non-obvious.

## Architecture (where things live)
- `app/bank/`: business rules. `account.py` is the ONLY place that changes the balance; `money.py`
  parses/validates amounts; `errors.py`, `models.py`, `recipient.py`, `sessions.py` (persistence).
- `app/agent/`: `agent.py` turn loop · `llm_gateway.py` timeouts/fallback over `google-genai` ·
  `tools.py` function declarations + dispatch · `output_guard.py` output guardrail ·
  `guardrails.py` input guardrail · `prompt.py` system prompt.
- `app/governance/audit.py`: audit trail + metrics. `app/main.py`: FastAPI (`create_app`).
- `app/static/`: plain HTML/CSS/JS. `pitch/`: slide deck. `evals/`: live-model test suite.

## Invariants (never break)
- The model NEVER changes the balance; it asks tools, and the tools validate in Python.
- A transfer needs two steps and the confirmation must come from a LATER user message (`Account` enforces it).
- Every R$ amount in a model reply must come from a tool/account/user (output guardrail).
- The balance shown in the UI comes from the backend snapshot, never from model text.
- Static assets are referenced as `/static/<file>?v=<content hash>` (`app/pages.py`): Cloudflare caches `/static` for 4 h.
- Secrets live only in `.env` (gitignored). `/api/about` must not leak the key or the system prompt.

## Tests
- Every new function gets a test; every bug fix gets a regression test.
- Fake external I/O with the named fakes in `tests/fake_gemini.py`; inject them (`BankAgent(LlmGateway(...))`,
  `create_app(sessions, audit, agent)`), do not monkey-patch globals.
- Tests are F.I.R.S.T.: no network, nothing written outside `tmp_path`.

## Dependencies
- Inject dependencies through constructors/parameters. Wrap third-party SDKs behind a thin interface we own
  (`LlmGateway` wraps `google-genai`).
