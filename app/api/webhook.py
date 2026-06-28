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

from fastapi import APIRouter, BackgroundTasks, Header, Query, Request, status
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

# Numero da Nidia (fallback quando settings.nidia_phone nao configurado)
_NIDIA_DEFAULT = "+55 21 97423-9844"


def _get_redis():
    """Obtem cliente Redis do estado da aplicacao (injetado no lifespan)."""
    from app.main import get_redis_client  # importacao tardia para evitar circular
    return get_redis_client()


async def _handle_engine(
    chamado_id: int,
    messages: list[dict],
) -> None:
    """
    Executa o pipeline completo: carregar contexto, rodar FlowEngine,
    persistir atualizacoes e enviar resposta via ChatMaster (F1).

    Parametros:
        chamado_id: ID do chamado/ticket no ChatMaster
        messages: lista de msg_data consolidadas pelo debounce
    """
    if not messages:
        return

    first_msg = messages[0]
    sender = first_msg.get("sender")

    # Extrair texto de todas as mensagens consolidadas
    all_texts: list[str] = []
    for msg_data in messages:
        for m in (msg_data.get("mensagem") or []):
            if isinstance(m, dict):
                msg_type = m.get("type") or m.get("mediaType", "")
                if msg_type == "text":
                    text = (m.get("text") or m.get("conteudo") or "").strip()
                    if text:
                        all_texts.append(text)
    user_message = "\n".join(all_texts).strip()
    if not user_message:
        logger.info("webhook: nenhum texto extraido chamado_id=%s — ignorado", chamado_id)
        return

    # Obter session factory do estado global da aplicacao
    from app.main import get_session_factory
    session_factory = get_session_factory()
    if session_factory is None:
        logger.error(
            "webhook: session factory nao disponivel chamado_id=%s — motor nao invocado",
            chamado_id,
        )
        return

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.config import settings as cfg
    from app.core.flow import FlowEngine
    from app.core.intent import IntentClassifier
    from app.core.memory import MemoryManager
    from app.core.responder import GroundedResponder
    from app.integrations.chatmaster import make_chatmaster_client
    from app.integrations.openai_client import OpenAIClient
    from app.repository.models import Contato, Ticket

    async with session_factory() as db_session:
        try:
            # ---------------------------------------------------------------
            # 1. Upsert Contato pelo numero (criacao ou atualizacao de nome)
            # ---------------------------------------------------------------
            nome_lead = first_msg.get("nome") or ""
            stmt_c = (
                pg_insert(Contato)
                .values(numero=str(sender or ""), nome=nome_lead or None)
                .on_conflict_do_update(
                    index_elements=["numero"],
                    set_={"nome": nome_lead} if nome_lead else {"numero": str(sender or "")},
                )
                .returning(Contato.id)
            )
            result_c = await db_session.execute(stmt_c)
            contato_id: int = result_c.scalar_one()
            await db_session.flush()

            # ---------------------------------------------------------------
            # 2. Upsert Ticket pelo chamado_id (criacao ou manutencao)
            # ---------------------------------------------------------------
            ticket_data_payload = first_msg.get("ticketData") or {}
            company_id = ticket_data_payload.get("companyId") if isinstance(ticket_data_payload, dict) else None
            queue_id = first_msg.get("queueId")

            stmt_t = (
                pg_insert(Ticket)
                .values(
                    chamado_id=chamado_id,
                    contato_id=contato_id,
                    company_id=company_id,
                    queue_id=queue_id,
                    status="aberto",
                )
                .on_conflict_do_update(
                    index_elements=["chamado_id"],
                    # Preservar status existente (nao sobrescrever handoff/encerrado)
                    set_={"contato_id": contato_id},
                )
                .returning(Ticket.status)
            )
            result_t = await db_session.execute(stmt_t)
            ticket_status_db: str = result_t.scalar_one()
            await db_session.flush()

            # Verificar status do ticket no banco (FR-024)
            if ticket_status_db in ("em_handoff", "encerrado"):
                logger.info(
                    "webhook: ticket DB status=%s chamado_id=%s — ignorado",
                    ticket_status_db,
                    chamado_id,
                )
                return

            # ---------------------------------------------------------------
            # 3. Montar integracoes e invocar FlowEngine
            # ---------------------------------------------------------------
            openai_client = OpenAIClient(
                api_key=cfg.openai_api_key,
                model_cheap=cfg.openai_model_cheap,
                model_reasoning=cfg.openai_model_reasoning,
            )
            memory_manager = MemoryManager(
                db_session=db_session,
                redis_client=_get_redis(),
                openai_client=openai_client,
            )
            intent_classifier = IntentClassifier(openai_client=openai_client)
            responder = GroundedResponder(openai_client=openai_client)
            engine = FlowEngine(
                db_session=db_session,
                intent_classifier=intent_classifier,
                memory_manager=memory_manager,
                responder=responder,
                nidia_phone=cfg.nidia_phone or _NIDIA_DEFAULT,
            )

            # Carregar contexto da sessao (DB + Redis)
            context = await memory_manager.load_context(chamado_id)

            # Processar com o motor conversacional
            flow_result = await engine.process(context.ticket_id, user_message, context)

            logger.info(
                "webhook: motor processou chamado_id=%s action=%s caminho=%s etapa=%s",
                chamado_id,
                flow_result.action,
                flow_result.caminho,
                flow_result.etapa,
            )

            # ---------------------------------------------------------------
            # 4. Persistir atualizacoes de estado
            # ---------------------------------------------------------------
            if flow_result.updates:
                await memory_manager.update_qualification_variables(
                    context.contato_id, flow_result.updates
                )
                await memory_manager.update_ticket_state(
                    context.ticket_id,
                    caminho=flow_result.updates.get("caminho_atual"),
                    etapa=flow_result.updates.get("etapa_mapa_mestre"),
                )

            # ---------------------------------------------------------------
            # 5. Persistir mensagens (inbound + outbound) na memoria
            # ---------------------------------------------------------------
            await memory_manager.save_message(context, {
                "direcao": "inbound",
                "tipo": "text",
                "conteudo": user_message,
            })
            if flow_result.response_text:
                await memory_manager.save_message(context, {
                    "direcao": "outbound",
                    "tipo": "text",
                    "conteudo": flow_result.response_text,
                })

            await db_session.commit()

            # ---------------------------------------------------------------
            # 6. Enviar resposta via ChatMaster (apos commit — nao transacional)
            # ---------------------------------------------------------------
            if sender and flow_result.response_text:
                if cfg.chatmaster_token:
                    async with make_chatmaster_client(cfg) as cm_client:
                        await cm_client.send_message_blocks(
                            str(sender), flow_result.response_text
                        )
                        if flow_result.action == "handoff":
                            try:
                                await cm_client.transfer_ticket(
                                    chamado_id=chamado_id,
                                    destination="consultores",
                                )
                            except Exception as exc_handoff:
                                logger.warning(
                                    "webhook: falha no handoff chamado_id=%s: %s",
                                    chamado_id,
                                    exc_handoff,
                                )
                else:
                    logger.warning(
                        "webhook: chatmaster_token nao configurado — resposta nao enviada "
                        "chamado_id=%s",
                        chamado_id,
                    )
            else:
                logger.debug(
                    "webhook: sem resposta para enviar chamado_id=%s action=%s",
                    chamado_id,
                    flow_result.action,
                )

        except Exception:
            logger.exception(
                "webhook: erro no pipeline chamado_id=%s — rollback", chamado_id
            )
            await db_session.rollback()


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
    3. Invocar motor conversacional (FlowEngine — F1)
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

        await _handle_engine(chamado_id, messages)


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
    token: Optional[str] = Query(default=None),
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
        # Aceita o token via header X-Webhook-Token OU via query param ?token=
        # (ChatMaster pode so permitir configurar URL, sem headers custom).
        expected = settings.webhook_token.encode("utf-8")
        provided_header = (x_webhook_token or "").encode("utf-8")
        provided_query = (token or "").encode("utf-8")
        # Comparacao em tempo constante (anti-timing — SEC-WH-1)
        ok = hmac.compare_digest(provided_header, expected) or hmac.compare_digest(
            provided_query, expected
        )
        if not ok:
            logger.warning(
                "webhook: token invalido (header/query) — descartando (sem retry trigger)"
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
    # Pre-check com o status do payload (verificacao definitiva via DB em motor)
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
        "nome": getattr(payload, "name", None) or "",
        "mensagem": [m.model_dump() for m in payload.mensagem],
        "ticketStatus": payload.ticket_status,
        "ticketData": payload.ticketData.model_dump() if payload.ticketData else None,
        "queueId": getattr(payload, "queueId", None),
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
