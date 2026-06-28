"""
Endpoint de recepcao de webhook do ChatMaster (via n8n por overlay interna).

Este endpoint NAO e exposto via Traefik — apenas alcancavel pela overlay
interna compartilhada com o n8n (block-001/dec-015, Principio VI).

Implementacao completa: FASE 3 (tasks 3.1, 3.2, 3.3).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Header, Request, status
from fastapi.responses import JSONResponse

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/webhook/chatmaster",
    status_code=status.HTTP_200_OK,
    summary="Recepcao de evento do ChatMaster via n8n (overlay interna)",
)
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_token: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
) -> dict:
    """
    Recebe evento do ChatMaster encaminhado pelo n8n.

    Responde 200 imediatamente (ack rapido) e processa em background apos
    janela de debounce. Retornos != 2xx causam reenvio pelo n8n — por isso
    ack e sempre 200 mesmo em erros de processamento.

    Seguranca (SEC-WH-1):
    - Endpoint so alcancavel pela overlay interna (sem rota Traefik)
    - X-Webhook-Token verificado em tempo constante quando configurado

    STUB: logica completa implementada em FASE 3.
    """
    # Validacao opcional de X-Webhook-Token (defesa em profundidade)
    if settings.webhook_token:
        provided = x_webhook_token or ""
        expected = settings.webhook_token
        # Comparacao em tempo constante (anti-timing — SEC-WH-1)
        if not hmac.compare_digest(provided.encode(), expected.encode()):
            logger.warning(
                "webhook: X-Webhook-Token invalido — descartando (sem retry trigger)"
            )
            # Retorna 200 mesmo assim para nao triggar retry do n8n
            return {"ack": "ok"}

    # TODO (FASE 3): parse do payload com Pydantic tolerante
    # TODO (FASE 3): ignorar fromMe:true
    # TODO (FASE 3): validar limites de tamanho (SEC-WH-4)
    # TODO (FASE 3): idempotencia por chamadoId+hash (Redis SET NX EX 86400)
    # TODO (FASE 3): debounce (RPUSH + flush apos janela)
    # TODO (FASE 3): lock por ticket (SET NX PX 30000)
    # TODO (FASE 3): filtro de estado do ticket (em_handoff/encerrado)
    # TODO (FASE 3): processar em background via BackgroundTasks

    return {"ack": "ok"}
