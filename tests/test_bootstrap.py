"""
Smoke tests do bootstrap da aplicacao (task 1.1.5).

Verifica:
- Aplicacao sobe sem excecao
- GET /health retorna 200 com body correto
- Resposta do /health < 3s
- Falha do rag_seed (extensao vector/tabela chunk pre-swap) nao derruba
  o boot (task 2.2.8, Onda 3 — RAG hibrido)
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    """TestClient sincrono para smoke tests (nao requer DB/Redis)."""
    with TestClient(app) as c:
        yield c


def test_app_bootstraps(client):
    """Aplicacao sobe sem excecao e atende requisicoes."""
    # Se chegou ate aqui sem exception, o bootstrap funcionou
    assert client is not None


def test_logging_configurado_para_stdout():
    """_configure_logging deixa o root logger em <= INFO com handler, para que os
    logger.info da aplicacao apareçam no stdout em producao."""
    import logging

    import app.main

    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        app.main._configure_logging()
        assert root.handlers, "root logger sem handler — logs da app nao chegam ao stdout"
        assert root.level <= logging.INFO, "root acima de INFO — logger.info descartado"
        # Um logger de aplicacao propaga para o root e emite via handler do root
        assert logging.getLogger("app.core.flow").getEffectiveLevel() <= logging.INFO
    finally:
        # Restaura o estado do logging para nao afetar a captura do pytest nos demais testes
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_health_returns_200(client):
    """GET /health retorna 200."""
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_ok_status(client):
    """GET /health retorna body com status 'ok'."""
    response = client.get("/health")
    body = response.json()
    assert body.get("status") == "ok"
    assert body.get("service") == "sdr-whatsapp"


def test_health_response_time_under_3s(client):
    """GET /health responde em menos de 3s (US6-AS3)."""
    start = time.monotonic()
    response = client.get("/health")
    elapsed = time.monotonic() - start
    assert response.status_code == 200
    assert elapsed < 3.0, f"Health check demorou {elapsed:.2f}s (limite: 3s)"


def test_admin_without_token_returns_401(client):
    """GET /admin/cursos sem token retorna 401."""
    response = client.get("/admin/cursos")
    assert response.status_code == 401


def test_admin_with_invalid_token_returns_401(client):
    """GET /admin/cursos com token invalido retorna 401."""
    response = client.get(
        "/admin/cursos",
        headers={"Authorization": "Bearer token-invalido-xpto"},
    )
    assert response.status_code == 401


def test_webhook_stub_returns_200(client):
    """POST /webhook/chatmaster (sem token configurado) retorna 200."""
    response = client.post(
        "/webhook/chatmaster",
        json={"mensagem": [{"type": "text", "text": "ola"}], "sender": "5511999999999"},
    )
    assert response.status_code == 200
    assert response.json().get("ack") == "ok"


def test_boot_tolera_falha_rag_seed_extensao_vector_ausente(monkeypatch):
    """
    Task 2.2.8: se `run_rag_seed` falhar (ex.: extensao `vector`/tabela
    `chunk` ainda ausente, pre-swap da imagem do Postgres para
    pgvector/pgvector — task 10.3.3), o boot do app NAO e derrubado —
    mesmo padrao try/except nao-fatal ja usado para `run_seed`
    (app/main.py, Decision 0/research.md).
    """
    import app.rag_seed as rag_seed_module

    async def _boom(*_args, **_kwargs):
        raise RuntimeError('relation "chunk" does not exist')

    monkeypatch.setattr(rag_seed_module, "run_rag_seed", AsyncMock(side_effect=_boom))

    with TestClient(app) as boot_client:
        response = boot_client.get("/health")

    assert response.status_code == 200
