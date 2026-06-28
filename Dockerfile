# ============================================================================
# Dockerfile multi-stage — SDR WhatsApp GoldIncision
# Stage 1: builder (instala dependencias)
# Stage 2: runtime (imagem enxuta, usuario non-root UID 1001)
# ============================================================================

# --- Stage 1: builder ---
FROM python:3.12-slim AS builder

WORKDIR /build

# Instalar pip atual e dependencias de sistema minimas
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copiar apenas o manifesto de dependencias primeiro (cache layer)
COPY pyproject.toml ./

# Instalar dependencias em /install para copiar na stage 2
RUN pip install --no-cache-dir --prefix=/install \
    fastapi \
    "uvicorn[standard]" \
    pydantic \
    pydantic-settings \
    "sqlalchemy[asyncio]" \
    asyncpg \
    alembic \
    "redis[hiredis]" \
    openai \
    httpx \
    python-multipart \
    python-jose

# --- Stage 2: runtime ---
FROM python:3.12-slim

# Dependencias de sistema minimas para asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar pacotes instalados do stage builder
COPY --from=builder /install /usr/local

# Criar usuario non-root (UID 1001) — SEC principal
RUN adduser --system --no-create-home --uid 1001 --group appuser

WORKDIR /app

# Copiar codigo da aplicacao
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY knowledge_base/ ./knowledge_base/
COPY pyproject.toml ./

# Ajustar permissoes
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Healthcheck via HTTP (usado pelo Docker Swarm e Traefik)
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--log-level", "info", "--no-access-log"]
