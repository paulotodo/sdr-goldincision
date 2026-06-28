"""
Schemas Pydantic v2 (DTOs) — camada de borda da API admin.

Convencao camelCase para todos os campos expostos via API.
Validacao estrita (sem campos extras — anti mass-assignment SEC-ADM-4).
"""
from app.schemas.curso import (
    CursoApresentacaoRead,
    CursoCreate,
    CursoLinkRead,
    CursoObjecaoRead,
    CursoRead,
    CursoTurmaRead,
    CursoUpdate,
)

__all__ = [
    "CursoCreate",
    "CursoRead",
    "CursoUpdate",
    "CursoApresentacaoRead",
    "CursoObjecaoRead",
    "CursoTurmaRead",
    "CursoLinkRead",
]
