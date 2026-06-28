"""
Testes de modelo/mapper (task 2.1.5).

Verifica:
- Roundtrip snake_case <-> camelCase nos schemas
- Paridade de campos obrigatorios no mapper
- Validacao Pydantic (campos extras sao bloqueados — SEC-ADM-4)
- Formato de idioma aceito/rejeitado
"""
from __future__ import annotations

import pytest

from app.repository.mapper import dto_to_curso_dict
from app.schemas.curso import CursoCreate, CursoUpdate

# ---------------------------------------------------------------------------
# Testes de schema CursoCreate
# ---------------------------------------------------------------------------

def test_curso_create_valid():
    """CursoCreate aceita payload valido em camelCase."""
    payload = {
        "slug": "curso-online-hg",
        "nome": "Curso Online Harmonizacao Glutea",
        "tipo": "online",
        "caminhoMapaMestre": 1,
        "elegibilidade": {"medico": True},
        "ativo": True,
    }
    curso = CursoCreate.model_validate(payload)
    assert curso.slug == "curso-online-hg"
    assert curso.nome == "Curso Online Harmonizacao Glutea"
    assert curso.tipo == "online"
    assert curso.caminho_mapa_mestre == 1
    assert curso.elegibilidade == {"medico": True}
    assert curso.ativo is True


def test_curso_create_snake_case_also_accepted():
    """CursoCreate aceita snake_case via populate_by_name=True."""
    payload = {
        "slug": "hg360-sp",
        "nome": "HG360 Sao Paulo",
        "tipo": "presencial",
        "caminho_mapa_mestre": 3,
    }
    curso = CursoCreate.model_validate(payload)
    assert curso.slug == "hg360-sp"
    assert curso.caminho_mapa_mestre == 3


def test_curso_create_rejects_extra_fields():
    """CursoCreate bloqueia campos extras (anti mass-assignment SEC-ADM-4)."""
    payload = {
        "slug": "test-curso",
        "nome": "Nome Valido",
        "tipo": "online",
        "campo_extra_injetado": "valor_malicioso",  # deve ser rejeitado
    }
    with pytest.raises(Exception):  # ValidationError do Pydantic
        CursoCreate.model_validate(payload)


def test_curso_create_invalid_tipo():
    """CursoCreate rejeita tipo invalido."""
    with pytest.raises(Exception):
        CursoCreate.model_validate({"slug": "test-x", "nome": "Nome Valido", "tipo": "invalido"})


def test_curso_create_slug_must_be_lowercase():
    """Slug deve ser em minusculas (pattern ^[a-z0-9-]+$)."""
    # Slug em maiusculas deve ser rejeitado pelo pattern
    with pytest.raises(Exception):
        CursoCreate.model_validate({"slug": "CURSO-ONLINE", "nome": "Nome Valido", "tipo": "online"})
    # Slug valido em minusculas deve ser aceito
    curso = CursoCreate.model_validate({"slug": "curso-online", "nome": "Nome Valido", "tipo": "online"})
    assert curso.slug == "curso-online"


def test_curso_create_caminho_range():
    """caminhoMapaMestre deve estar entre 1 e 6."""
    with pytest.raises(Exception):
        CursoCreate.model_validate({
            "slug": "teste-caminho", "nome": "Nome Valido", "tipo": "online", "caminhoMapaMestre": 0
        })
    with pytest.raises(Exception):
        CursoCreate.model_validate({
            "slug": "teste-caminho", "nome": "Nome Valido", "tipo": "online", "caminhoMapaMestre": 7
        })
    # Valores validos 1..6
    for v in range(1, 7):
        c = CursoCreate.model_validate({
            "slug": f"curso-{v}", "nome": "Nome Valido", "tipo": "online", "caminhoMapaMestre": v
        })
        assert c.caminho_mapa_mestre == v


# ---------------------------------------------------------------------------
# Testes de CursoUpdate
# ---------------------------------------------------------------------------

def test_curso_update_partial():
    """CursoUpdate aceita atualizacao parcial."""
    upd = CursoUpdate.model_validate({"ativo": False})
    assert upd.ativo is False
    assert upd.nome is None


def test_curso_update_rejects_extra_fields():
    """CursoUpdate bloqueia campos extras."""
    with pytest.raises(Exception):
        CursoUpdate.model_validate({"id": 999, "ativo": False})  # id nao e campo editavel


# ---------------------------------------------------------------------------
# Testes do mapper
# ---------------------------------------------------------------------------

def test_mapper_to_curso_dict_valid():
    """dto_to_curso_dict converte camelCase para snake_case corretamente."""
    dto = {
        "slug": "hg360-barcelona",
        "nome": "HG360 Barcelona",
        "tipo": "presencial",
        "ativo": True,
    }
    result = dto_to_curso_dict(dto)
    assert result["slug"] == "hg360-barcelona"
    assert result["nome"] == "HG360 Barcelona"
    assert result["tipo"] == "presencial"
    assert result["ativo"] is True


def test_mapper_rejects_unknown_fields():
    """dto_to_curso_dict rejeita campos desconhecidos (anti mass-assignment)."""
    with pytest.raises(ValueError, match="Campos nao permitidos"):
        dto_to_curso_dict({
            "slug": "test-x",
            "nome": "Nome",
            "tipo": "online",
            "campo_extra": "val",
        })


def test_mapper_camel_case_field():
    """dto_to_curso_dict converte caminhoMapaMestre -> caminho_mapa_mestre."""
    result = dto_to_curso_dict({"caminhoMapaMestre": 4})
    assert "caminho_mapa_mestre" in result
    assert result["caminho_mapa_mestre"] == 4


def test_mapper_id_is_not_writable():
    """id nao e campo escritavel — deve ser rejeitado pelo mapper."""
    with pytest.raises(ValueError, match="Campos nao permitidos"):
        dto_to_curso_dict({"id": 1, "slug": "test-x", "nome": "Nome", "tipo": "online"})


def test_mapper_empty_dto():
    """dto vazio retorna dict vazio (todos os campos sao opcionais no update)."""
    result = dto_to_curso_dict({})
    assert result == {}
