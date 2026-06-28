"""
Endpoint de recepcao de webhook do ChatMaster (via n8n por overlay interna).

Este endpoint NAO e exposto via Traefik — apenas alcancavel pela overlay
network_main compartilhada com o n8n (block-001/dec-015, Principio VI).
O n8n acessa http://app:8000/webhook/chatmaster via DNS interno do Swarm.

Pipeline de processamento (tasks 3.1 + 3.2):
1. Ack 200 imediato (nao triggar retry do n8n)
2. Validacao opcional de X-Webhook-Token (tempo constante — SEC-WH-1)
3. Ignorar fromMe:true (FR-002)
4. Ignorar mensagens de grupo (isGroup:true)
5. Idempotencia por chamadoId+sha256 (24h — FR-037-INFRA-IDEMP)
6. Debounce de rajada (RPUSH + flush apos janela — SC-005)
7. Lock por ticket (SET NX PX 30000 — FR-035-INFRA-MUTEX)
8. Filtro de estado do ticket (em_handoff/encerrado — FR-024)
9. Processamento em background (BackgroundTasks)
"""
from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, Request, status
from pydantic import ValidationError

from app.config import settings
from app.core.debounce import DebounceManager
from app.core.idempotency import IdempotencyChecker
from app.core.locks import TicketLock
from app.schemas.webhook import WebhookPayload

logger = logging.getLogger(__name__)

router = APIRouter()

# Limite de tamanho do corpo HTTP (bytes) — SEC-WH-4
_MAX_BODY_BYTES = 512 * 1024  # 512 KB


def _get_redis():
    """Obtem cliente Redis do estado da aplicacao (injetado no lifespan)."""
    from app.main import get_redis_client  # importacao tardia para evitar circular
    return get_redis_client()


async def _process_consolidated_messages(
    chamado_id: int,
    messages: list[dict],
) -> None:
    """
    Processa lista consolidada de mensagens apos janela de debounce.

    Esta funcao roda em background (BackgroundTasks ou asyncio.Task).
    Cada etapa e defensiva: erros sao logados sem propagar para o n8n.

    Pipeline:
    1. Adquirir lock por ticket (serial — FR-035)
    2. Verificar estado do ticket (handoff/encerrado → nop)
    3. Invocar motor conversacional (FASE 4)
    """
    redis = _get_redis()
    lock = TicketLock(redis)

    async with lock.acquire(chamado_id) as acquired:
        if not acquired:
            logger.warning(
                "webhook: lock ocupado chamado_id=%s — descartando (%d msgs)",
                chamado_id, len(messages),
            )
            return

        logger.info(
            "webhook: processando %d msg(s) consolidadas chamado_id=%s",
            len(messages), chamado_id,
        )

        # Verificar estado do ticket antes de agir (FR-024)
        # (Implementacao completa: FASE 4 com acesso ao DB)
        # Por ora, apenas log — o motor conversacional faz a verificacao real
        logger.debug(
            "webhook: lock acquired chamado_id=%s — entregando ao motor (FASE 4)",
            chamado_id,
        )

        # TODO (FASE 4): invocar motor conversacional com lista de mensagens
        # await motor.handle(chamado_id, messages)


@router.post(
    "/webhook/chatmaster",
    status_code=status.HTTP_200_OK,
    summary="Recepcao de evento do ChatMaster via n8n (overlay interna)",
    include_in_schema=False,  # Nao aparece no Swagger publico (endpoint interno)
)
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_token: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
) -> dict:
    """
    Recebe evento do ChatMaster encaminhado pelo n8n.

    Responde 200 IMEDIATAMENTE e processa em background apos janela de
    debounce. Retornos != 2xx causam reenvio pelo n8n — por isso ack e
    sempre 200 mesmo em erros de processamento interno.

    Seguranca (block-001 / SEC-WH-1 / FR-002):
    - Endpoint so alcancavel pela overlay network_main (sem rota Traefik)
    - X-Webhook-Token verificado em tempo constante quando configurado
    - fromMe:true e isGroup:true descartados silenciosamente
    - Limite de tamanho do corpo (SEC-WH-4)
    """
    # -------------------------------------------------------------------------
    # 1. Limite de tamanho do corpo (SEC-WH-4)
    # -------------------------------------------------------------------------
    body_bytes = await request.body()
    if len(body_bytes) > _MAX_BODY_BYTES:
        logger.warning(
            "webhook: corpo excede limite %d bytes (%d) — descartando",
            _MAX_BODY_BYTES, len(body_bytes),
        )
        return {"ack": "ok"}  # Ack 200 sempre

    # -------------------------------------------------------------------------
    # 2. Validacao opcional de X-Webhook-Token (defesa em profundidade SEC-WH-1)
    # -------------------------------------------------------------------------
    if settings.webhook_token:
        provided = x_webhook_token or ""
        # Comparacao em tempo constante (anti-timing — SEC-WH-1)
        if not hmac.compare_digest(
            provided.encode("utf-8"),
            settings.webhook_token.encode("utf-8"),
        ):
            logger.warning(
                "webhook: X-Webhook-Token invalido — descartando (sem retry trigger)"
            )
            return {"ack": "ok"}  # 200 para nao triggar retry

    # -------------------------------------------------------------------------
    # 3. Parse do payload com Pydantic tolerante (extra=ignore)
    # -------------------------------------------------------------------------
    try:
        import json as _json
        raw_body = _json.loads(body_bytes)
        payload = WebhookPayload.model_validate(raw_body)
    except (ValueError, ValidationError) as exc:
        logger.warning("webhook: payload invalido — %s", exc)
        return {"ack": "ok"}  # 200 mesmo em payload malformado

    # -------------------------------------------------------------------------
    # 4. Ignorar fromMe:true (mensagens proprias do agente — FR-002)
    # -------------------------------------------------------------------------
    if payload.fromMe:
        logger.debug("webhook: fromMe=true chamado_id=%s — ignorado", payload.chamadoId)
        return {"ack": "ok"}

    # -------------------------------------------------------------------------
    # 5. Ignorar mensagens de grupo (fora do escopo)
    # -------------------------------------------------------------------------
    if payload.isGroup:
        logger.debug("webhook: isGroup=true chamado_id=%s — ignorado", payload.chamadoId)
        return {"ack": "ok"}

    # -------------------------------------------------------------------------
    # 6. Filtro de estado do ticket (FR-024): em_handoff/encerrado → nop
    # Pre-check com o status do payload (verificacao definitiva via DB em FASE 4)
    # -------------------------------------------------------------------------
    if payload.ticketData and payload.ticketData.is_handoff:
        logger.info(
            "webhook: ticket em_handoff/encerrado chamado_id=%s status=%s — ignorado",
            payload.chamadoId,
            payload.ticketData.status,
        )
        return {"ack": "ok"}

    # -------------------------------------------------------------------------
    # 7. Idempotencia (FR-037-INFRA-IDEMP): SET NX EX 86400
    # -------------------------------------------------------------------------
    redis = _get_redis()
    if redis is not None:
        idemp = IdempotencyChecker(redis)
        if await idemp.is_duplicate(payload.chamadoId, raw_body):
            logger.info(
                "webhook: evento duplicado descartado chamado_id=%s", payload.chamadoId
            )
            return {"ack": "ok"}

    # -------------------------------------------------------------------------
    # 8. Debounce (FR-003 / SC-005): RPUSH + flush apos janela
    # -------------------------------------------------------------------------
    msg_data = {
        "chamadoId": payload.chamadoId,
        "sender": payload.contact_number,
        "mensagem": [m.model_dump() for m in payload.mensagem],
        "ticketStatus": payload.ticket_status,
        "ticketData": payload.ticketData.model_dump() if payload.ticketData else None,
    }

    if redis is not None:
        debounce = DebounceManager(redis, debounce_seconds=settings.debounce_seconds)
        background_tasks.add_task(
            debounce.push_and_schedule,
            payload.chamadoId,
            msg_data,
            _process_consolidated_messages,
        )
    else:
        # Fallback sem Redis: processar diretamente (testes/dev)
        background_tasks.add_task(
            _process_consolidated_messages,
            payload.chamadoId,
            [msg_data],
        )

    logger.info(
        "webhook: ack chamado_id=%s sender=%s msgs=%d",
        payload.chamadoId,
        payload.contact_number or "?",
        len(payload.mensagem),
    )
    return {"ack": "ok"}
