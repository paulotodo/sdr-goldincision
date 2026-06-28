"""
Ponto de entrada da aplicacao FastAPI — SDR WhatsApp GoldIncision.

Registra rotas, inicializa pools de DB/Redis no startup e os fecha no shutdown.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, webhook

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia ciclo de vida: inicializa recursos no startup, fecha no shutdown."""
    logger.info("sdr-whatsapp: startup iniciando...")

    # TODO (FASE 2): inicializar pool async do SQLAlchemy + engine
    # TODO (FASE 2): inicializar pool Redis
    # TODO (FASE 2): rodar migrations/seed se necessario

    logger.info("sdr-whatsapp: pronto para receber requisicoes")
    yield

    # Shutdown
    logger.info("sdr-whatsapp: shutdown...")
    # TODO (FASE 2): fechar pool DB e Redis


app = FastAPI(
    title="SDR WhatsApp GoldIncision",
    description="Consultor Virtual Oficial da GoldIncision via WhatsApp",
    version="0.1.0",
    # Desabilitar docs em producao via env se necessario
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Registrar routers
app.include_router(webhook.router, tags=["webhook"])
app.include_router(admin.router, tags=["admin"])


@app.get("/health", tags=["infra"])
async def health() -> dict:
    """
    Endpoint de healthcheck — usado pelo Traefik e pelo HEALTHCHECK do Docker.
    NAO expoe informacoes sensiveis.
    Retorna 200 quando a app esta respondendo.
    """
    return {"status": "ok", "service": "sdr-whatsapp"}
