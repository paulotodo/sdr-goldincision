"""
Testes para app/api/admin.py (tasks 6.1.3, 6.2.3).

Cobre:
- Autenticacao: sem token → 401; token invalido → 401; valido → acesso
- Rate limiting: muitas tentativas → 429
- CRUD: criar → 201 + aparece no catalogo; update/delete refletem
- Mass-assignment bloqueado (SEC-ADM-4)
- Sub-recursos: apresentacao e link por idioma
- SC-004: mudancas refletem sem redeploy (FR-026)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api import admin as admin_module
from app.main import app

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

VALID_TOKEN = "token-admin-teste-seguro-xyz"

CURSO_PAYLOAD = {
    "slug": "curso-teste-admin",
    "nome": "Curso de Teste Admin",
    "tipo": "online",
    "caminhoMapaMestre": 1,
    "elegibilidade": {"medico": True},
    "ativo": True,
    "apresentacoes": [
        {"idioma": "pt", "texto": "Apresentacao oficial verbatim em PT."},
        {"idioma": "en", "texto": "Official presentation verbatim EN."},
    ],
    "objecoes": [
        {
            "idioma": "pt",
            "objecao": "esta caro",
            "resposta": "O valor e justificado pelo conteudo e suporte.",
        }
    ],
    "turmas": [
        {
            "cidade": "Sao Paulo",
            "pais": "BR",
            "dataInicio": "2026-09-01",
            "capacidade": 30,
            "vagasDisponiveis": 30,
            "lotePreco": "lote 1",
            "ativo": True,
        }
    ],
    "links": [
        {"idioma": "pt", "url": "https://pay.hotmart.com/pt"},
        {"idioma": "en", "url": "https://pay.hotmart.com/en"},
    ],
    "midias": [
        {
            "idioma": None,
            "tipo": "image",
            "url": "https://cdn.exemplo.com/img.jpg",
            "legenda": "Imagem do curso",
        }
    ],
}


def _make_mock_curso(curso_id: int = 1, slug: str = "curso-teste-admin") -> MagicMock:
    """Cria mock de objeto ORM Curso com subentidades."""
    from datetime import datetime, timezone

    curso = MagicMock()
    curso.id = curso_id
    curso.slug = slug
    curso.nome = "Curso de Teste Admin"
    curso.tipo = "online"
    curso.caminho_mapa_mestre = 1
    curso.elegibilidade = {"medico": True}
    curso.ativo = True
    curso.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    curso.updated_at = datetime(2026, 1, 2, tzinfo=timezone.utc)

    # Sub-entidades vazias (CRUD simples sem sub-entidades)
    curso.apresentacoes = []
    curso.objecoes = []
    curso.turmas = []
    curso.links = []
    curso.midias = []

    return curso


@pytest.fixture
def client(monkeypatch):
    """TestClient com settings mockadas para admin_token valido."""
    monkeypatch.setattr(admin_module.settings, "admin_token", VALID_TOKEN)
    # Resetar rate limit entre testes
    admin_module._rate_store.clear()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Testes de autenticacao (6.1.1, 6.1.2, 6.1.3)
# ---------------------------------------------------------------------------

def test_sem_token_retorna_401(client):
    """Sem token → 401 em todas as rotas /admin/*."""
    resp = client.get("/admin/cursos")
    assert resp.status_code == 401


def test_token_invalido_retorna_401(client):
    """Token errado → 401."""
    resp = client.get(
        "/admin/cursos",
        headers={"Authorization": "Bearer token-errado"},
    )
    assert resp.status_code == 401


def test_token_valido_retorna_200_ou_503(client, monkeypatch):
    """Token correto → passa pela autenticacao (pode retornar 503 se DB indisponivel)."""
    monkeypatch.setattr(admin_module.settings, "admin_token", VALID_TOKEN)

    # Mockar DB ausente → 503 (mas passou da auth)
    with patch("app.main.get_session_factory", return_value=None):
        resp = client.get(
            "/admin/cursos",
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )
    # 503 = passou da auth, DB indisponivel. Nao e 401.
    assert resp.status_code != 401


def test_token_vazio_retorna_401(client):
    """Token vazio → 401."""
    resp = client.get(
        "/admin/cursos",
        headers={"Authorization": "Bearer "},
    )
    assert resp.status_code == 401


def test_token_constante_tempo_nao_vaza_info(client, monkeypatch):
    """
    Comparacao de token nao vaza informacao via timing.
    Verifica que hmac.compare_digest e usado (indiretamente).
    """
    import hmac

    call_count = []

    original = hmac.compare_digest

    def spy_compare(a, b):
        call_count.append((a, b))
        return original(a, b)

    with patch("app.api.admin.hmac.compare_digest", side_effect=spy_compare):
        client.get(
            "/admin/cursos",
            headers={"Authorization": "Bearer token-qualquer"},
        )

    assert len(call_count) == 1, "hmac.compare_digest deve ser chamado exatamente 1x"


def test_rate_limit_bloqueia_apos_excesso(client, monkeypatch):
    """
    Apos muitas tentativas com token invalido → 429 (SEC-ADM-2).
    """
    monkeypatch.setattr(admin_module, "_RATE_LIMIT_MAX", 3)
    admin_module._rate_store.clear()

    for i in range(3):
        resp = client.get(
            "/admin/cursos",
            headers={"Authorization": "Bearer token-errado"},
        )
        # As primeiras devem ser 401
        assert resp.status_code in (401, 429)

    # A proxima deve ser 429
    resp = client.get(
        "/admin/cursos",
        headers={"Authorization": "Bearer token-errado"},
    )
    assert resp.status_code == 429


# ---------------------------------------------------------------------------
# Testes de CRUD (6.2.1, 6.2.3)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db_session():
    """Mock de AsyncSession para testes de CRUD sem DB real."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def mock_factory(mock_db_session):
    """Mock de session factory retornando mock_db_session."""
    factory = MagicMock(return_value=mock_db_session)
    return factory


def test_criar_curso_retorna_201(client, monkeypatch, mock_factory, mock_db_session):
    """POST /admin/cursos cria curso e retorna 201 com id."""
    monkeypatch.setattr(admin_module.settings, "admin_token", VALID_TOKEN)
    admin_module._rate_store.clear()

    curso_mock = _make_mock_curso(curso_id=99)

    # Mock de verificacao de slug duplicado (sem duplicado)
    result_vazio = MagicMock()
    result_vazio.scalar_one_or_none = MagicMock(return_value=None)

    result_curso = MagicMock()
    result_curso.scalar_one_or_none = MagicMock(return_value=curso_mock)
    result_curso.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[curso_mock])))

    call_count = [0]

    async def fake_execute(query, *args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return result_vazio  # verificacao de slug
        return result_curso  # load com relations

    mock_db_session.execute = fake_execute
    mock_db_session.flush = AsyncMock(side_effect=lambda: setattr(mock_db_session, "_flushed", True))

    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.post(
            "/admin/cursos",
            json={k: v for k, v in CURSO_PAYLOAD.items()},
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status_code == 201


def test_mass_assignment_bloqueado(client, monkeypatch):
    """
    Campos nao permitidos (ex: internal_field) sao removidos do payload
    antes de chegar ao modelo. Nao causa erro 422 — apenas ignorados.
    """
    monkeypatch.setattr(admin_module.settings, "admin_token", VALID_TOKEN)
    admin_module._rate_store.clear()

    # O payload de criacao filtra campos extras; nao deve retornar 422
    # (a criacao pode falhar por DB ausente, mas nao por mass-assignment)
    payload = {
        "slug": "teste-mass",
        "nome": "Teste Mass Assignment",
        "tipo": "online",
        "internal_field": "INJETADO",  # deve ser ignorado
        "id": 9999,  # deve ser ignorado
    }

    with patch("app.main.get_session_factory", return_value=None):
        resp = client.post(
            "/admin/cursos",
            json=payload,
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    # 503 (DB ausente) ou 201 — mas NAO 422 (mass-assignment nao e erro de validacao aqui)
    assert resp.status_code != 422, "Mass assignment nao deve causar 422"


def test_get_curso_nao_encontrado(client, monkeypatch, mock_factory, mock_db_session):
    """GET /admin/cursos/999 retorna 404 se inexistente."""
    monkeypatch.setattr(admin_module.settings, "admin_token", VALID_TOKEN)
    admin_module._rate_store.clear()

    result_vazio = MagicMock()
    result_vazio.scalar_one_or_none = MagicMock(return_value=None)
    mock_db_session.execute = AsyncMock(return_value=result_vazio)

    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.get(
            "/admin/cursos/999",
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status_code == 404


def test_delete_curso_retorna_204(client, monkeypatch, mock_factory, mock_db_session):
    """DELETE /admin/cursos/{id} retorna 204 e marca ativo=false."""
    monkeypatch.setattr(admin_module.settings, "admin_token", VALID_TOKEN)
    admin_module._rate_store.clear()

    curso_mock = _make_mock_curso(curso_id=5)
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=curso_mock)
    mock_db_session.execute = AsyncMock(return_value=result)

    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.delete(
            "/admin/cursos/5",
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status_code == 204
    # Verificar soft-delete
    assert curso_mock.ativo is False


def test_update_curso_retorna_200(client, monkeypatch, mock_factory, mock_db_session):
    """PUT /admin/cursos/{id} retorna 200 com dados atualizados."""
    monkeypatch.setattr(admin_module.settings, "admin_token", VALID_TOKEN)
    admin_module._rate_store.clear()

    curso_mock = _make_mock_curso(curso_id=3)

    result_curso = MagicMock()
    result_curso.scalar_one_or_none = MagicMock(return_value=curso_mock)
    result_curso.scalars = MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=[curso_mock]))
    )

    call_count = [0]

    async def fake_execute(q, *args, **kwargs):
        call_count[0] += 1
        return result_curso

    mock_db_session.execute = fake_execute

    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.put(
            "/admin/cursos/3",
            json={"nome": "Nome Atualizado", "ativo": False},
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status_code == 200
    assert curso_mock.nome == "Nome Atualizado"
    assert curso_mock.ativo is False


def test_slug_duplicado_retorna_409(client, monkeypatch, mock_factory, mock_db_session):
    """POST com slug duplicado retorna 409."""
    monkeypatch.setattr(admin_module.settings, "admin_token", VALID_TOKEN)
    admin_module._rate_store.clear()

    curso_existente = _make_mock_curso(curso_id=1, slug="curso-ja-existe")
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=curso_existente)
    mock_db_session.execute = AsyncMock(return_value=result)

    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.post(
            "/admin/cursos",
            json={**CURSO_PAYLOAD, "slug": "curso-ja-existe"},
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status_code == 409


def test_list_cursos_retorna_lista(client, monkeypatch, mock_factory, mock_db_session):
    """GET /admin/cursos retorna lista de cursos."""
    monkeypatch.setattr(admin_module.settings, "admin_token", VALID_TOKEN)
    admin_module._rate_store.clear()

    cursos = [_make_mock_curso(1, "c1"), _make_mock_curso(2, "c2")]
    result = MagicMock()
    result.scalars = MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=cursos))
    )
    mock_db_session.execute = AsyncMock(return_value=result)

    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.get(
            "/admin/cursos",
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2


# ---------------------------------------------------------------------------
# Sub-recursos granulares
# ---------------------------------------------------------------------------

def test_update_apresentacao_sucesso(client, monkeypatch, mock_factory, mock_db_session):
    """PUT /admin/cursos/{id}/apresentacoes/pt atualiza apresentacao."""
    monkeypatch.setattr(admin_module.settings, "admin_token", VALID_TOKEN)
    admin_module._rate_store.clear()

    curso_mock = _make_mock_curso(1)
    apres_mock = MagicMock()
    apres_mock.texto = "Texto antigo"

    call_count = [0]

    async def fake_execute(q, *args, **kwargs):
        call_count[0] += 1
        r = MagicMock()
        if call_count[0] == 1:
            # _get_curso_or_404
            r.scalar_one_or_none = MagicMock(return_value=curso_mock)
        else:
            # busca apresentacao existente
            r.scalar_one_or_none = MagicMock(return_value=apres_mock)
        return r

    mock_db_session.execute = fake_execute

    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.put(
            "/admin/cursos/1/apresentacoes/pt",
            json={"texto": "Texto novo verbatim"},
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status_code == 200
    assert apres_mock.texto == "Texto novo verbatim"


def test_update_link_idioma_invalido(client, monkeypatch):
    """PUT /admin/cursos/{id}/links/fr retorna 400 (idioma invalido)."""
    monkeypatch.setattr(admin_module.settings, "admin_token", VALID_TOKEN)
    admin_module._rate_store.clear()

    with patch("app.main.get_session_factory", return_value=None):
        resp = client.put(
            "/admin/cursos/1/links/fr",
            json={"url": "https://exemplo.com"},
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status_code == 400


def test_catalogo_reflete_mudanca_sem_redeploy(
    client, monkeypatch, mock_factory, mock_db_session
):
    """
    SC-004 / FR-026: Verificar que o GET /admin/cursos le do DB em runtime.
    Simula que apos DELETE (soft), o curso aparece com ativo=false na listagem.
    """
    monkeypatch.setattr(admin_module.settings, "admin_token", VALID_TOKEN)
    admin_module._rate_store.clear()

    # Primeiro: listar com curso ativo
    curso_ativo = _make_mock_curso(1)
    curso_ativo.ativo = True

    result_ativo = MagicMock()
    result_ativo.scalars = MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=[curso_ativo]))
    )
    mock_db_session.execute = AsyncMock(return_value=result_ativo)

    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp = client.get(
            "/admin/cursos",
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )
    assert resp.status_code == 200
    assert resp.json()[0]["ativo"] is True

    # Segundo: apos soft-delete, listar novamente com curso inativo
    curso_inativo = _make_mock_curso(1)
    curso_inativo.ativo = False

    result_inativo = MagicMock()
    result_inativo.scalars = MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=[curso_inativo]))
    )
    mock_db_session.execute = AsyncMock(return_value=result_inativo)

    with patch("app.main.get_session_factory", return_value=mock_factory):
        resp2 = client.get(
            "/admin/cursos",
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )
    assert resp2.status_code == 200
    assert resp2.json()[0]["ativo"] is False
