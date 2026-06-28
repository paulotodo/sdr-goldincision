"""
Admin API — CRUD de cursos como dados (Principio VII, FR-025/026).

Protegida por token em tempo constante (SEC-ADM-1/2/4).
Implementacao completa: FASE 6 (tasks 6.1, 6.2).
"""
from __future__ import annotations

import hmac
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")

security = HTTPBearer(auto_error=False)


def verify_admin_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    """
    Dependency: verifica token de admin em tempo constante (anti-timing).
    Deny-by-default: sem token → 401.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticacao ausente",
            headers={"WWW-Authenticate": "Bearer"},
        )

    provided = credentials.credentials
    expected = settings.admin_token

    if not expected:
        # Admin token nao configurado — deny all
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin API nao configurada",
        )

    # Comparacao em tempo constante (anti-timing — SEC-ADM-1)
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalido",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return provided


@router.get("/cursos", dependencies=[Depends(verify_admin_token)])
async def list_cursos() -> dict:
    """Lista todos os cursos ativos (STUB — FASE 6)."""
    # TODO (FASE 6): consultar DB
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="FASE 6")


@router.post("/cursos", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_admin_token)])
async def create_curso(request: Request) -> dict:
    """Cria novo curso (STUB — FASE 6)."""
    # TODO (FASE 6): validar payload Pydantic estrito (anti mass-assignment SEC-ADM-4)
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="FASE 6")


@router.put("/cursos/{curso_id}", dependencies=[Depends(verify_admin_token)])
async def update_curso(curso_id: int, request: Request) -> dict:
    """Atualiza curso (STUB — FASE 6)."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="FASE 6")


@router.delete("/cursos/{curso_id}", dependencies=[Depends(verify_admin_token)])
async def delete_curso(curso_id: int) -> dict:
    """Soft-delete de curso (STUB — FASE 6)."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="FASE 6")
