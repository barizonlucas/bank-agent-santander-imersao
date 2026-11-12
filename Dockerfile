# Imagem do Banco Imersão (mesmo padrão dos demais apps Python do homelab: uv + slim + usuário não-root)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    DATA_DIR=/data

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY pitch ./pitch

# /data guarda sessões e auditoria (volume). UID 65532 = mesmo usuário dos outros apps.
RUN mkdir -p /data && chown 65532:65532 /data
USER 65532:65532

EXPOSE 8000

# Sem curl na imagem slim: o healthcheck usa o próprio Python.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/about', timeout=4)"]

# 1 worker de propósito: as sessões vivem em memória + arquivo.
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
