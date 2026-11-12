"""Enforces the project's Clean Code rules (see CLAUDE.md) so they cannot silently regress.

* functions: at most 20 lines of code (docstring, blank lines and comments excluded);
* files: under 500 lines;
* control-flow nesting (if/for/while/try/match): at most 2 levels;
* identifiers and comments in English (Portuguese is allowed only inside string values).
"""

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_FILES = sorted((ROOT / "app").rglob("*.py"))
ALL_PYTHON_FILES = sorted(p for folder in ("app", "tests", "evals") for p in (ROOT / folder).rglob("*.py"))

MAX_FUNCTION_LINES = 20
MAX_FILE_LINES = 500
MAX_NESTING = 2
NESTING_NODES = (ast.If, ast.For, ast.While, ast.Try, ast.Match)

PORTUGUESE_IDENTIFIER_WORDS = {
    "saldo",
    "valor",
    "valores",
    "conta",
    "depositar",
    "deposito",
    "transferir",
    "transferencia",
    "extrato",
    "sessao",
    "mensagem",
    "ferramenta",
    "ferramentas",
    "destinatario",
    "operacao",
    "pendente",
    "resposta",
    "texto",
    "turno",
    "historico",
    "erro",
    "limite",
    "recusa",
    "entrada",
    "saida",
    "chamada",
    "contexto",
    "governanca",
    "auditoria",
    "painel",
    "relatorio",
    "caso",
    "casos",
    "nome",
    "tipo",
}
PORTUGUESE_COMMENT_WORDS = {"não", "uma", "dos", "das", "para", "mas", "porque", "quando", "também", "você", "pelo"}
QUOTED = re.compile(r'"[^"]*"|\'[^\']*\'|`[^`]*`')
ACCENTED = re.compile(r"[à-úÀ-Ú]")


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def _functions(tree: ast.AST):
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _code_lines(function: ast.AST, source_lines: list[str]) -> int:
    """Lines of real code: no blank lines, comments or docstring."""
    first = function.body[0]
    has_docstring = (
        isinstance(first, ast.Expr)
        and isinstance(getattr(first, "value", None), ast.Constant)
        and isinstance(first.value.value, str)
    )
    skip = set(range(first.lineno, first.end_lineno + 1)) if has_docstring else set()
    span = range(function.lineno, function.end_lineno + 1)
    return sum(
        1
        for n in span
        if n not in skip and source_lines[n - 1].strip() and not source_lines[n - 1].strip().startswith("#")
    )


def _max_nesting(node: ast.AST, depth: int = 0) -> int:
    deepest = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue  # nested functions are measured on their own
        child_depth = depth + 1 if isinstance(child, NESTING_NODES) else depth
        deepest = max(deepest, _max_nesting(child, child_depth))
    return deepest


def _words(identifier: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", identifier)
    return [w.lower() for w in spaced.split("_") if w]


def _identifiers(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg) or isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    return names


@pytest.mark.parametrize("path", APP_FILES, ids=_relative)
def test_files_are_small(path):
    assert len(path.read_text().splitlines()) < MAX_FILE_LINES, f"{_relative(path)} should be split by responsibility"


@pytest.mark.parametrize("path", APP_FILES, ids=_relative)
def test_functions_are_short_and_flat(path):
    source = path.read_text()
    lines = source.splitlines()
    too_long, too_deep = [], []
    for function in _functions(ast.parse(source)):
        if _code_lines(function, lines) > MAX_FUNCTION_LINES:
            too_long.append(f"{function.name}:{function.lineno}")
        if _max_nesting(function) > MAX_NESTING:
            too_deep.append(f"{function.name}:{function.lineno}")
    assert not too_long, f"functions over {MAX_FUNCTION_LINES} lines: {too_long}"
    assert not too_deep, f"functions nested deeper than {MAX_NESTING}: {too_deep}"


@pytest.mark.parametrize("path", ALL_PYTHON_FILES, ids=_relative)
def test_identifiers_are_english(path):
    found = {n for n in _identifiers(ast.parse(path.read_text())) if set(_words(n)) & PORTUGUESE_IDENTIFIER_WORDS}
    accented = {n for n in _identifiers(ast.parse(path.read_text())) if ACCENTED.search(n)}
    assert not found | accented, f"Portuguese identifiers in {_relative(path)}: {sorted(found | accented)}"


def _comments(path: Path) -> list[tuple[int, str]]:
    tokens = tokenize.generate_tokens(io.StringIO(path.read_text()).readline)
    return [(t.start[0], t.string) for t in tokens if t.type == tokenize.COMMENT]


@pytest.mark.parametrize("path", ALL_PYTHON_FILES, ids=_relative)
def test_comments_are_english(path):
    offenders = []
    for line, comment in _comments(path):
        text = QUOTED.sub("", comment).lower()  # quoted Portuguese examples are allowed
        if ACCENTED.search(text) or set(re.findall(r"[\wà-ú]+", text)) & PORTUGUESE_COMMENT_WORDS:
            offenders.append(f"{_relative(path)}:{line}: {comment.strip()[:70]}")
    assert not offenders, "Portuguese comments:\n" + "\n".join(offenders)
