"""
Schemas Pydantic v2 para Curso/Turma/Objecao (camelCase).

Campos estritamente validados (model_config extra="forbid") para
prevenir mass-assignment (SEC-ADM-4, FR-025).

Regra de nomenclatura: fields em camelCase como alias (para a API)
+ snake_case como nome Python interno.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda s: "".join(
            word.capitalize() if i else word
            for i, word in enumerate(s.split("_"))
        ),
        populate_by_name=True,  # aceita snake_case tambem
        extra="forbid",         # anti mass-assignment (SEC-ADM-4)
    )


# ---------------------------------------------------------------------------
# Apresentacao Oficial
# ---------------------------------------------------------------------------

class CursoApresentacaoRead(_CamelModel):
    id: int
    curso_id: int
    idioma: str
    texto: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Objecao
# ---------------------------------------------------------------------------

class CursoObjecaoRead(_CamelModel):
    id: int
    curso_id: int
    idioma: str
    objecao: str
    resposta: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Turma
# ---------------------------------------------------------------------------

class CursoTurmaRead(_CamelModel):
    id: int
    curso_id: int
    cidade: str
    pais: Optional[str] = None
    data_inicio: Optional[date] = None
    capacidade: Optional[int] = None
    vagas_disponiveis: Optional[int] = None
    lote_preco: Optional[str] = None
    ativo: bool


# ---------------------------------------------------------------------------
# Link de Inscricao
# ---------------------------------------------------------------------------

class CursoLinkRead(_CamelModel):
    id: int
    curso_id: int
    idioma: str
    url: str


# ---------------------------------------------------------------------------
# Curso — Create
# ---------------------------------------------------------------------------

class CursoCreate(_CamelModel):
    """Payload de criacao de curso. Todos os campos escritaveis explicitamente listados."""
    slug: str = Field(..., min_length=3, max_length=100, pattern=r"^[a-z0-9-]+$")
    nome: str = Field(..., min_length=2, max_length=200)
    tipo: str = Field(..., pattern=r"^(online|presencial|licenciamento|franquia)$")
    caminho_mapa_mestre: Optional[int] = Field(default=None, ge=1, le=6)
    elegibilidade: dict = Field(default_factory=dict)
    ativo: bool = Field(default=True)

    @field_validator("slug")
    @classmethod
    def slug_lowercase(cls, v: str) -> str:
        return v.lower()


# ---------------------------------------------------------------------------
# Curso — Update (campos opcionais)
# ---------------------------------------------------------------------------

class CursoUpdate(_CamelModel):
    """Payload de atualizacao parcial de curso (todos os campos opcionais)."""
    nome: Optional[str] = Field(default=None, min_length=2, max_length=200)
    tipo: Optional[str] = Field(default=None, pattern=r"^(online|presencial|licenciamento|franquia)$")
    caminho_mapa_mestre: Optional[int] = Field(default=None, ge=1, le=6)
    elegibilidade: Optional[dict] = None
    ativo: Optional[bool] = None


# ---------------------------------------------------------------------------
# Curso — Read (resposta completa)
# ---------------------------------------------------------------------------

class CursoRead(_CamelModel):
    model_config = ConfigDict(
        alias_generator=lambda s: "".join(
            word.capitalize() if i else word
            for i, word in enumerate(s.split("_"))
        ),
        populate_by_name=True,
        extra="ignore",  # Read pode ter campos extras (sem problema)
    )

    id: int
    slug: str
    nome: str
    tipo: str
    caminho_mapa_mestre: Optional[int] = None
    elegibilidade: dict = Field(default_factory=dict)
    ativo: bool
    created_at: datetime
    updated_at: datetime
    apresentacoes: list[CursoApresentacaoRead] = Field(default_factory=list)
    objecoes: list[CursoObjecaoRead] = Field(default_factory=list)
    turmas: list[CursoTurmaRead] = Field(default_factory=list)
    links: list[CursoLinkRead] = Field(default_factory=list)
