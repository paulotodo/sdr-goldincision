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
import time
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, Query, Request, status
from pydantic import ValidationError

from app.config import settings
from app.core import redis_keys
from app.core.debounce import DebounceManager
from app.core.idempotency import IdempotencyChecker
from app.core.locks import TicketLock
from app.observability.log import log_turno
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


async def _bump_turnos_sessao(chamado_id: int) -> int:
    """
    Incrementa `turnos_sessao` no hash `estado:{chamadoId}` (HINCRBY) e
    retorna o novo valor. Base do orcamento de turnos (US1, FASE 3) e do
    campo `turno_sessao` do evento de observabilidade (US5, FR-015).

    Chamada ANTES de `engine.process()` (nao mais em `finally`): o valor
    incrementado alimenta `context.turnos_sessao`, consumido pelo
    FlowEngine no MESMO turno para decidir escalonamento (FR-004) — o
    evento de observabilidade, ao final, reusa `context.turnos_sessao` em
    vez de incrementar de novo (evita double-count).

    Fail-open (Edge Case: perda de contadores em restart do Redis / erro de
    conexao) — nunca derruba o turno; retorna 0 (tratado como turno nao
    contabilizado, e nao como turno zero real).
    """
    try:
        redis = _get_redis()
        key = redis_keys.estado_key(chamado_id)
        val = await redis.hincrby(key, redis_keys.TURNOS_SESSAO_FIELD, 1)
        return int(val)
    except Exception:
        logger.warning(
            "webhook: falha ao incrementar turnos_sessao chamado_id=%s",
            chamado_id,
            exc_info=True,
        )
        return 0


async def _bump_turnos_no_no(chamado_id: int, etapa: Optional[str]) -> int:
    """
    Incrementa (ou reseta) `turnos_no_no` no hash `estado:{chamadoId}}` —
    contador de turnos consecutivos no MESMO no/etapa do mapa-mestre (US1,
    FASE 3, task 3.1.1/3.1.3).

    Reseta o contador para 1 sempre que a etapa marcada no hash (campo
    `turnos_no_no_etapa`) difere da etapa corrente — inclusive no 1o turno
    neste no (marca ausente). Isso implementa o "reset ao mudar
    etapa_mapa_mestre" (task 3.1.3) sem depender de limpeza explicita de
    campos antigos: o contador SEMPRE reflete quantos turnos seguidos o
    lead esta no no atual, nunca acumulando entre nos diferentes.

    Ortogonal ao contador anti-loop `_MAX_TENTATIVAS`/`etapa_funil`
    (FlowEngine): este conta TODO turno processado no no (reconhecido ou
    nao); aquele conta apenas respostas NAO reconhecidas — nao se fundem.

    Fail-open (Edge Case: perda de contadores em restart do Redis / erro de
    conexao) — nunca derruba o turno; retorna 0.
    """
    etapa_norm = etapa or ""
    try:
        redis = _get_redis()
        key = redis_keys.estado_key(chamado_id)
        etapa_anterior = await redis.hget(key, redis_keys.TURNOS_NO_NO_ETAPA_FIELD)
        if isinstance(etapa_anterior, bytes):
            etapa_anterior = etapa_anterior.decode("utf-8")
        if etapa_anterior != etapa_norm:
            await redis.hset(key, redis_keys.TURNOS_NO_NO_ETAPA_FIELD, etapa_norm)
            await redis.hset(key, redis_keys.TURNOS_NO_NO_FIELD, 1)
            return 1
        val = await redis.hincrby(key, redis_keys.TURNOS_NO_NO_FIELD, 1)
        return int(val)
    except Exception:
        logger.warning(
            "webhook: falha ao incrementar turnos_no_no chamado_id=%s etapa=%s",
            chamado_id,
            etapa_norm,
            exc_info=True,
        )
        return 0


async def _bump_ultima_interacao(chamado_id: int) -> Optional[float]:
    """
    Le a marca de `ultima_interacao` anterior (epoch seconds) do hash
    `estado:{chamadoId}`, grava a marca ATUAL (HSET) e retorna quantas horas
    se passaram desde a interacao anterior (US2, FASE 5, task 5.1.1).

    Fail-open (task 5.1.2/5.2.4 — Edge Case: perda do timestamp em restart
    do Redis, valor corrompido, ou 1o turno da sessao): retorna `None`,
    tratado por `FlowEngine._aplicar_reengajamento_pre` como "interacao
    recente" — NENHUMA retomada/expiracao e disparada. Mesmo padrao
    fail-open de `_bump_turnos_sessao`/`_bump_turnos_no_no`.

    Chamada ANTES de `engine.process()` (mesma ordem dos bumps de orcamento
    de turnos) para que o MESMO turno ja veja o gap calculado.
    """
    try:
        redis = _get_redis()
        key = redis_keys.estado_key(chamado_id)
        agora = time.time()
        anterior_raw = await redis.hget(key, redis_keys.ULTIMA_INTERACAO_FIELD)
        await redis.hset(key, redis_keys.ULTIMA_INTERACAO_FIELD, int(agora))

        if anterior_raw is None:
            return None
        if isinstance(anterior_raw, bytes):
            anterior_raw = anterior_raw.decode("utf-8")
        anterior = float(anterior_raw)
        if anterior <= 0:
            return None
        delta_horas = (agora - anterior) / 3600.0
        if delta_horas < 0:
            # Relogio retrocedeu ou timestamp futuro corrompido — fail-open.
            return None
        return delta_horas
    except (TypeError, ValueError):
        # Timestamp corrompido/nao numerico — fail-open (tratado como recente).
        return None
    except Exception:
        logger.warning(
            "webhook: falha ao ler/gravar ultima_interacao chamado_id=%s",
            chamado_id,
            exc_info=True,
        )
        return None


async def _handle_reset(chamado_id: int, sender: Optional[str]) -> None:
    """
    Processa o comando #reset: so para numeros de teste autorizados.
    Limpa a memoria do agente (Postgres + Redis) e confirma. Numero fora da
    allowlist e ignorado silenciosamente (recurso invisivel para leads reais).
    """
    from app.config import settings as cfg
    from app.core.reset import confirmacao_reset, is_numero_teste, reset_conversa
    from app.main import get_session_factory

    redis = _get_redis()
    session_factory = get_session_factory()
    autorizado = False
    if session_factory is not None:
        async with session_factory() as s:
            autorizado = await is_numero_teste(s, str(sender or ""))
            if autorizado:
                await reset_conversa(s, redis, chamado_id)
    if not autorizado:
        logger.info(
            "webhook: #reset de numero NAO autorizado chamado_id=%s — ignorado",
            chamado_id,
        )
        return

    logger.info("webhook: #reset executado chamado_id=%s", chamado_id)
    # Confirmacao (texto fixo, nao passa pelo LLM)
    if sender and cfg.chatmaster_token:
        try:
            from app.integrations.chatmaster import make_chatmaster_client
            async with make_chatmaster_client(cfg) as cm:
                await cm.send_message(str(sender), confirmacao_reset("pt"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("webhook: falha ao enviar confirmacao de reset: %s", exc)


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
    from app.core.fidelity import FidelityGate
    from app.core.flow import FlowEngine, _tent_count
    from app.core.intent import IntentClassifier
    from app.core.interpret import SlotExtractor
    from app.core.memory import MemoryManager
    from app.core.responder import GroundedResponder
    from app.core.retrieval import HybridRetriever, SqlAlchemyChunkRepository
    from app.integrations.chatmaster import make_chatmaster_client
    from app.integrations.openai_client import OpenAIClient
    from app.repository.models import Contato, Ticket

    # Observabilidade de turno (US5, FR-015/FR-016): 1 evento por turno
    # processado, inclusive em falha (via finally). "emit" so vira True apos
    # o contexto ser carregado — turnos filtrados antes disso (ticket ja em
    # handoff/encerrado) seguem o mesmo padrao dos demais filtros de webhook
    # (fromMe/isGroup/gate de fila), sem evento dedicado.
    _turno_evt: dict = {"emit": False}
    _turno_t0 = time.monotonic()

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
            fidelity_gate = FidelityGate(
                openai_client=openai_client,
                timeout_seconds=cfg.verify_timeout_seconds,
            )
            responder = GroundedResponder(
                openai_client=openai_client,
                max_tokens=cfg.reasoning_max_tokens,
                fidelity_gate=fidelity_gate,
            )
            slot_extractor = SlotExtractor(openai_client=openai_client)
            # RAG hibrido (Onda 3, FASE 5/FASE 7 — FR-001..FR-006, FR-020):
            # config via 7 envs novos (app/config.py Settings), sem hardcode.
            chunk_repository = SqlAlchemyChunkRepository(db_session)
            retriever = HybridRetriever(
                chunk_repository=chunk_repository,
                openai_client=openai_client,
                limiar_abstencao=cfg.rag_limiar_abstencao,
                k_vetorial=cfg.rag_k_vetorial,
                k_textual=cfg.rag_k_textual,
                top_k=cfg.rag_top_k,
                timeout_seconds=cfg.rag_retrieval_timeout_seconds,
                redis_client=_get_redis(),
                cache_enabled=cfg.rag_cache_enabled,
            )
            engine = FlowEngine(
                db_session=db_session,
                intent_classifier=intent_classifier,
                memory_manager=memory_manager,
                responder=responder,
                nidia_phone=cfg.nidia_phone or _NIDIA_DEFAULT,
                slot_extractor=slot_extractor,
                retriever=retriever,
            )

            # Carregar contexto da sessao (DB + Redis)
            context = await memory_manager.load_context(chamado_id)

            # A partir daqui o turno esta de fato sendo processado —
            # garantir 1 evento de observabilidade (sucesso ou erro).
            _turno_evt["emit"] = True
            _turno_evt["etapa_entrada"] = context.etapa or ""

            # Orcamento de turnos (US1, FASE 3, FR-001/FR-002): incrementar
            # os contadores ANTES de engine.process(), para que o MESMO
            # turno ja veja o valor atualizado ao decidir nudge/handoff
            # (FlowEngine._aplicar_orcamento_turnos le context.turnos_*).
            # etapa usada para turnos_no_no e a de ENTRADA (context.etapa,
            # ainda nao mutada pelo motor) — reflete o no em que o lead
            # estava ao enviar esta mensagem.
            context.turnos_sessao = await _bump_turnos_sessao(chamado_id)
            context.turnos_no_no = await _bump_turnos_no_no(chamado_id, context.etapa)

            # Timeout de inatividade e reengajamento (US2, FASE 5, FR-008):
            # marca a interacao ATUAL e calcula o gap desde a anterior —
            # tambem ANTES de engine.process() (mesmo motivo dos bumps
            # acima: o MESMO turno precisa decidir retomada/sessao-nova).
            context.horas_inatividade = await _bump_ultima_interacao(chamado_id)

            # Processar com o motor conversacional
            flow_result = await engine.process(context.ticket_id, user_message, context)

            logger.info(
                "webhook: motor processou chamado_id=%s action=%s caminho=%s etapa=%s",
                chamado_id,
                flow_result.action,
                flow_result.caminho,
                flow_result.etapa,
            )

            _turno_evt["etapa_saida"] = flow_result.etapa or _turno_evt["etapa_entrada"]
            _turno_evt["idioma"] = context.idioma
            _turno_evt["intencao"] = context.ultima_intencao
            # Orcamento de turnos (US1, FASE 3, FR-006): nudge/handoff por
            # teto de no/sessao marca acao+motivo via FlowResult.turno_acao/
            # .motivo (FlowEngine._aplicar_orcamento_turnos); demais handoffs
            # (pedido humano, anti-loop, elegibilidade) seguem "handoff" puro.
            if flow_result.action == "handoff":
                _turno_evt["acao"] = "handoff"
            elif flow_result.turno_acao:
                _turno_evt["acao"] = flow_result.turno_acao
            else:
                _turno_evt["acao"] = "resposta"
            _turno_evt["motivo"] = flow_result.motivo
            _turno_evt["handoff_destino"] = (
                flow_result.handoff_destino if flow_result.action == "handoff" else None
            )
            # Melhor esforco: contador anti-loop da etapa de saida (nao gateia nada).
            _turno_evt["tentativas"] = (
                _tent_count(context, flow_result.etapa) if flow_result.etapa else 0
            )
            # Observabilidade aditiva (FASE 4, task 4.3 — sdr-fidelidade-json):
            # confianca de slot-filling (Pilar 8) e veredito do Portao de
            # Fidelidade (Pilar 7) deste turno, quando acionados. None quando
            # o mecanismo correspondente nao rodou — log_turno OMITE o campo
            # nesse caso (contrato aditivo, nunca quebra o schema da Onda 1).
            _turno_evt["confianca_slot"] = flow_result.confianca_slot
            _turno_evt["fidelidade_fiel"] = flow_result.fidelidade_fiel
            _turno_evt["fidelidade_afirmacoes_nao_sustentadas"] = (
                flow_result.fidelidade_afirmacoes_nao_sustentadas
            )
            # Rastreabilidade aditiva (Onda 3, FASE 5 — US4/FR-018): ids dos
            # chunks recuperados (HybridRetriever) que embasaram a resposta.
            _turno_evt["fonte_ids"] = flow_result.fonte_ids

            # ---------------------------------------------------------------
            # 4. Persistir atualizacoes de estado
            # ---------------------------------------------------------------
            if flow_result.updates:
                await memory_manager.update_qualification_variables(
                    context.contato_id, flow_result.updates
                )
            # Persistir estado do ticket (caminho/etapa sempre; handoff quando aplicavel).
            _is_handoff = flow_result.action == "handoff"
            await memory_manager.update_ticket_state(
                context.ticket_id,
                caminho=flow_result.updates.get("caminho_atual"),
                etapa=flow_result.updates.get("etapa_mapa_mestre"),
                status="em_handoff" if _is_handoff else None,
                handoff_destino=flow_result.handoff_destino if _is_handoff else None,
                handoff_motivo=flow_result.handoff_motivo if _is_handoff else None,
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
                        _turno_evt["n_blocos_enviados"] = await cm_client.send_message_blocks(
                            str(sender),
                            flow_result.response_text,
                            idioma=context.idioma,
                        )
                        if flow_result.action == "handoff":
                            try:
                                # Destino LOGICO vem do fluxo (allowlist/config resolve
                                # o queueId; nunca do LLM — SEC-LLM-3). Fallback seguro.
                                destino = flow_result.handoff_destino or "consultores"
                                await cm_client.transfer_ticket(
                                    chamado_id=chamado_id,
                                    destination=destino,
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
            if _turno_evt.get("emit"):
                _turno_evt["acao"] = "erro"

        finally:
            # Garante exatamente 1 evento por turno processado (SC-007),
            # inclusive quando a excecao acima interrompeu o fluxo (FR-016).
            # `context.turnos_sessao` ja foi incrementado 1x ANTES de
            # engine.process() (nao ha novo HINCRBY aqui — evitaria
            # double-count do mesmo turno).
            if _turno_evt.get("emit"):
                _duracao_ms = int((time.monotonic() - _turno_t0) * 1000)
                log_turno(
                    chamado_id=chamado_id,
                    turno_sessao=context.turnos_sessao,
                    etapa_entrada=_turno_evt.get("etapa_entrada") or "",
                    etapa_saida=(
                        _turno_evt.get("etapa_saida")
                        or _turno_evt.get("etapa_entrada")
                        or ""
                    ),
                    idioma=_turno_evt.get("idioma") or "pt",
                    n_blocos_enviados=_turno_evt.get("n_blocos_enviados", 0),
                    acao=_turno_evt.get("acao", "erro"),
                    duracao_ms=_duracao_ms,
                    tentativas=_turno_evt.get("tentativas", 0),
                    intencao=_turno_evt.get("intencao"),
                    handoff_destino=_turno_evt.get("handoff_destino"),
                    motivo=_turno_evt.get("motivo"),
                    confianca_slot=_turno_evt.get("confianca_slot"),
                    fidelidade_fiel=_turno_evt.get("fidelidade_fiel"),
                    fidelidade_afirmacoes_nao_sustentadas=_turno_evt.get(
                        "fidelidade_afirmacoes_nao_sustentadas"
                    ),
                    fonte_ids=_turno_evt.get("fonte_ids"),
                )


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
            # Chamada de origem nao autorizada / evento que nao consumimos: descartar
            # silenciosamente (em debug, nao WARNING — e esperado e nao acionavel).
            logger.debug(
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
    # 5.bis Comando de reset de jornada (#reset) — apenas numeros de teste.
    # Executa ANTES de handoff/idempotencia/debounce (reset imediato).
    # -------------------------------------------------------------------------
    _texto_msg = ""
    for _m in payload.mensagem:
        if getattr(_m, "type", None) == "text":
            _texto_msg = (getattr(_m, "text", "") or "").strip()
            break
    if _texto_msg.lower() == (settings.reset_command or "#reset").strip().lower():
        await _handle_reset(payload.chamadoId, payload.contact_number)
        return {"ack": "ok"}

    # -------------------------------------------------------------------------
    # 5.ter Gate por fila: o agente SO atende na fila da IA (settings.ai_queue_id).
    # Mensagens de outra fila (ex.: 78 = atendimento humano) sao ignoradas — o humano
    # atende no mesmo numero, sem interferencia. Decisoes: fila ausente → processa
    # (compat); #reset (acima) funciona fora da fila; ao devolver o ticket a fila da
    # IA, o agente retoma automaticamente.
    # -------------------------------------------------------------------------
    fila_atual = payload.queueId
    if fila_atual is None and payload.ticketData:
        fila_atual = payload.ticketData.queueId
    if (
        settings.ai_queue_id is not None
        and fila_atual is not None
        and fila_atual != settings.ai_queue_id
    ):
        logger.info(
            "webhook: fila=%s != fila IA=%s chamado_id=%s — atendimento humano, "
            "agente silencioso",
            fila_atual,
            settings.ai_queue_id,
            payload.chamadoId,
        )
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
