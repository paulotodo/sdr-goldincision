"""
Smoke tests do bootstrap da aplicacao (task 1.1.5).

Verifica:
- Aplicacao sobe sem excecao
- GET /health retorna 200 com body correto
- Resposta do /health < 3s
"""
from __future__ import annotations

import time

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
