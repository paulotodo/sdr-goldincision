"""
Ponto de entrada da aplicacao FastAPI — SDR WhatsApp GoldIncision.

Registra rotas, inicializa pools de DB/Redis no startup e os fecha no shutdown.
Expoe get_redis_client() / get_session_factory() para uso por outros modulos.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Optional

from fastapi import FastAPI

from app.config import settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Estado global dos pools (inicializado no lifespan)
_engine: Optional[Any] = None
_session_factory: Optional[Any] = None
_redis_client: Optional[Any] = None


def get_redis_client() -> Optional[Any]:
    """Retorna o cliente Redis inicializado (ou None se indisponivel/em testes)."""
    return _redis_client


def get_session_factory() -> Optional[Any]:
    """Retorna a factory de sessao SQLAlchemy (ou None se nao inicializado)."""
    return _session_factory


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia ciclo de vida: inicializa recursos no startup, fecha no shutdown."""
    global _engine, _session_factory, _redis_client

    logger.info("sdr-whatsapp: startup iniciando...")

    # Inicializar pool async do SQLAlchemy (importacao lazy)
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        _engine = create_async_engine(
            settings.database_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            echo=False,
        )
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
        logger.info("sdr-whatsapp: pool Postgres inicializado")
    except Exception:
        logger.exception("sdr-whatsapp: falha ao inicializar pool Postgres")

    # Inicializar pool Redis (importacao lazy)
    try:
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=False,  # bytes — compativel com Lua eval e pipelines
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        await _redis_client.ping()
        logger.info("sdr-whatsapp: pool Redis inicializado")
    except Exception:
        logger.exception("sdr-whatsapp: falha ao inicializar pool Redis")
        _redis_client = None

    logger.info("sdr-whatsapp: pronto para receber requisicoes")
    yield

    # Shutdown
    logger.info("sdr-whatsapp: shutdown...")
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None
    if _engine:
        await _engine.dispose()
        _engine = None
    logger.info("sdr-whatsapp: shutdown concluido")


app = FastAPI(
    title="SDR WhatsApp GoldIncision",
    description="Consultor Virtual Oficial da GoldIncision via WhatsApp",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Registrar routers (importacao apos criacao do app para evitar circular imports)
from app.api import admin, webhook  # noqa: E402

app.include_router(webhook.router, tags=["webhook"])
app.include_router(admin.router, tags=["admin"])


@app.get("/health", tags=["infra"])
async def health() -> dict:
    """
    Endpoint de healthcheck — usado pelo Traefik e pelo HEALTHCHECK do Docker.
    NAO expoe informacoes sensiveis.
    Retorna 200 quando a app esta respondendo.
    """
    redis_ok = False
    if _redis_client:
        try:
            await _redis_client.ping()
            redis_ok = True
        except Exception:
            pass

    return {
        "status": "ok",
        "service": "sdr-whatsapp",
        "redis": "ok" if redis_ok else "unavailable",
        "db": "ok" if _engine is not None else "unavailable",
    }
