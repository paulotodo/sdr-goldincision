"""
Testes de `/admin/chunks` (task 3.1.6, 3.1.7, 3.1.8 — Onda 3, RAG hibrido).

Seguranca (dec-020 finding #1, API3 BOPLA):
- payload malicioso com fonte_tabela/fonte_id nao sequestra linha auto-
  sincronizada (constraint UNIQUE preservada, campo rejeitado via
  extra='forbid' -> 422) — task 3.1.6
- tipo='objecao'/'faq' via /admin/chunks -> 422 (curadoria manual e
  exclusiva de tipo='base'; objecao/faq sao auto-sincronizados por
  app/rag_seed.py) — task 3.1.7
- conteudo >4000 caracteres -> 422 (dec-020 finding #2) — task 3.1.8

Mesmo padrao de mocks de tests/test_admin.py: TestClient + mock da
session factory (sem DB real).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api import admin as admin_module
from app.main import app

VALID_TOKEN = "token-admin-teste-seguro-xyz"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(admin_module.settings, "admin_token", VALID_TOKEN)
    admin_module._rate_store.clear()
    return TestClient(app)


@pytest.fixture
def mock_db_session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.delete = AsyncMock()
    return session


@pytest.fixture
def mock_factory(mock_db_session):
    return MagicMock(return_value=mock_db_session)


def _auth_headers():
    return {"Authorization": f"Bearer {VALID_TOKEN}"}


# ---------------------------------------------------------------------------
# Autenticacao (deny-by-default — mesma garantia das demais rotas /admin)
# ---------------------------------------------------------------------------

def test_chunks_sem_token_retorna_401(client):
    resp = client.post("/admin/chunks", json={
        "tipo": "base", "idioma": "pt", "conteudo": "x",
    })
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Task 3.1.7 — tipo travado em 'base', 422 para objecao/faq
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tipo_malicioso", ["objecao", "faq"])
def test_post_chunks_tipo_objecao_ou_faq_retorna_422(client, mock_factory, tipo_malicioso):
    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.post(
            "/admin/chunks",
            json={"tipo": tipo_malicioso, "idioma": "pt", "conteudo": "tentativa maliciosa"},
            headers=_auth_headers(),
        )
    assert resp.status_code == 422


def test_post_chunks_tipo_base_aceito(client, mock_factory, mock_db_session):
    """Controle positivo: tipo='base' e aceito (contraste com o teste acima)."""
    async def fake_flush():
        mock_db_session.add.call_args[0][0].id = 55

    mock_db_session.flush = AsyncMock(side_effect=fake_flush)

    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.post(
            "/admin/chunks",
            json={"tipo": "base", "idioma": "pt", "conteudo": "conteudo curado pelo admin"},
            headers=_auth_headers(),
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tipo"] == "base"
    assert body["fonte_tabela"] == "admin"
    assert body["fonte_id"] == 55  # == id autoincrementado do proprio chunk


# ---------------------------------------------------------------------------
# Task 3.1.6 — fonte_tabela/fonte_id/embedding/ativo nunca client-supplied
# ---------------------------------------------------------------------------

def test_post_chunks_payload_malicioso_fonte_tabela_faq_rejeitado(client, mock_factory, mock_db_session):
    """
    Payload tentando sequestrar a linha auto-sincronizada de um Faq real
    via fonte_tabela='faq' + fonte_id=<id existente> e REJEITADO (422,
    extra='forbid') antes de qualquer escrita — session.add() nunca e
    chamado com dados do atacante; a constraint UNIQUE(fonte_tabela,
    fonte_id, idioma) nunca chega a ser exercitada com valor malicioso.
    """
    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.post(
            "/admin/chunks",
            json={
                "tipo": "base",
                "idioma": "pt",
                "conteudo": "conteudo legitimo",
                "fonte_tabela": "faq",
                "fonte_id": 1,
            },
            headers=_auth_headers(),
        )
    assert resp.status_code == 422
    mock_db_session.add.assert_not_called()


@pytest.mark.parametrize("campo_proibido,valor", [
    ("embedding", [0.1] * 1536),
    ("ativo", False),
    ("fonte_tabela", "admin"),
    ("fonte_id", 999),
])
def test_post_chunks_campos_proibidos_sempre_rejeitados(client, mock_factory, campo_proibido, valor):
    payload = {"tipo": "base", "idioma": "pt", "conteudo": "x", campo_proibido: valor}
    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.post("/admin/chunks", json=payload, headers=_auth_headers())
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Task 3.1.8 — conteudo > 4000 chars -> 422
# ---------------------------------------------------------------------------

def test_post_chunks_conteudo_acima_de_4000_chars_retorna_422(client, mock_factory):
    conteudo_grande = "x" * 4001
    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.post(
            "/admin/chunks",
            json={"tipo": "base", "idioma": "pt", "conteudo": conteudo_grande},
            headers=_auth_headers(),
        )
    assert resp.status_code == 422


def test_post_chunks_conteudo_exatamente_4000_chars_aceito(client, mock_factory, mock_db_session):
    async def fake_flush():
        mock_db_session.add.call_args[0][0].id = 1

    mock_db_session.flush = AsyncMock(side_effect=fake_flush)
    conteudo_limite = "x" * 4000

    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.post(
            "/admin/chunks",
            json={"tipo": "base", "idioma": "pt", "conteudo": conteudo_limite},
            headers=_auth_headers(),
        )
    assert resp.status_code == 201


def test_post_chunks_conteudo_vazio_retorna_422(client, mock_factory):
    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.post(
            "/admin/chunks",
            json={"tipo": "base", "idioma": "pt", "conteudo": ""},
            headers=_auth_headers(),
        )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /admin/chunks — lista somente tipo='base'
# ---------------------------------------------------------------------------

def test_get_chunks_lista_apenas_base(client, mock_factory, mock_db_session):
    chunk_mock = MagicMock()
    chunk_mock.id = 1
    chunk_mock.curso_id = None
    chunk_mock.tipo = "base"
    chunk_mock.idioma = "pt"
    chunk_mock.conteudo = "conteudo curado"
    chunk_mock.fonte_tabela = "admin"
    chunk_mock.fonte_id = 1
    chunk_mock.ativo = True

    result = MagicMock()
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[chunk_mock])))
    mock_db_session.execute = AsyncMock(return_value=result)

    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.get("/admin/chunks", headers=_auth_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["tipo"] == "base"


# ---------------------------------------------------------------------------
# DELETE /admin/chunks/{id} — restrito a tipo='base'
# ---------------------------------------------------------------------------

def test_delete_chunk_base_retorna_204(client, mock_factory, mock_db_session):
    chunk_mock = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=chunk_mock)
    mock_db_session.execute = AsyncMock(return_value=result)

    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.delete("/admin/chunks/1", headers=_auth_headers())

    assert resp.status_code == 204
    mock_db_session.delete.assert_called_once_with(chunk_mock)


def test_delete_chunk_inexistente_ou_nao_base_retorna_404(client, mock_factory, mock_db_session):
    """DELETE restrito a tipo='base' — a query ja filtra por tipo='base',
    entao um chunk objecao/faq com o mesmo id tambem cai neste 404
    (nunca removido por aqui — dec-020 finding #1)."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    mock_db_session.execute = AsyncMock(return_value=result)

    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.delete("/admin/chunks/999", headers=_auth_headers())

    assert resp.status_code == 404
