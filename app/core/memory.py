"""
Gerenciamento de memoria conversacional — historico duravel + janela quente.

Implementa Principio III (Memoria e Jornada Sem Atrito):
- Historico duravel em Postgres (tabela mensagem via SessaoConversa)
- Janela quente em Redis (ultimas N mensagens, TTL por sessao)
- Resumo rolante para janelas longas (50+ msgs — SC-002)
- Variaveis de qualificacao persistidas por contato (idioma, eh_medico, etc)
- Nunca repete perguntas ja respondidas (FR-021)
- Recupera contexto em novo ticket do mesmo contato (US2-AS4)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.hot_window import HotWindowManager
from app.repository.models import (
    Contato,
    Mensagem,
    SessaoConversa,
    Ticket,
)

logger = logging.getLogger(__name__)

_HOT_WINDOW_SIZE = 20      # ultimas N msgs na janela quente Redis
_SUMMARIZE_THRESHOLD = 50  # resumo rolante apos N msgs (SC-002)

# Prompt de sumarizacao — modelo barato (evitar estouro de contexto)
_SYSTEM_SUMMARIZE = """\
Você é um assistente que cria resumos de conversas comerciais.
Crie um resumo CONCISO (máximo 200 palavras) da conversa abaixo,
preservando APENAS as informações de qualificação do lead:
- Interesse declarado (curso, produto)
- Se é médico ou não
- Especialidade e experiência corporal
- Idioma preferido
- Dúvidas ou objeções expressas
- Estado atual da negociação

Não inclua saudações ou mensagens de cortesia.
Retorne apenas o texto do resumo, sem marcadores ou títulos.
"""


@dataclass
class SessionContext:
    """Contexto completo de uma sessao de conversa."""
    ticket_id: int
    chamado_id: int
    contato_id: int
    caminho: Optional[int] = None
    etapa: Optional[str] = None
    idioma: str = "pt"
    eh_medico: Optional[bool] = None
    especialidade: Optional[str] = None
    experiencia_corporal: Optional[bool] = None
    produto_interesse: Optional[str] = None
    resumo_rolante: Optional[str] = None
    historico_recente: list[dict] = field(default_factory=list)
    sessao_id: Optional[int] = None
    # Nome do lead (para humanizacao — "Dr(a). <nome>")
    nome: Optional[str] = None
    # Estado efemero do funil (JSON serializado em Contato.etapa_funil):
    # contador de tentativas por etapa para evitar loops (sem migration).
    etapa_funil: Optional[str] = None
    # Perfil livre/incremental do lead (Contato.perfil — JSONB): caracteristicas e
    # preferencias arbitrarias alem da qualificacao fixa, acumuladas entre tickets.
    perfil: dict = field(default_factory=dict)
    # Intencao classificada no turno corrente (ClassificacaoIntencao.value),
    # mutada por FlowEngine.process (US5/FR-015 — observabilidade de turno).
    # Transiente: nao persistida, apenas para o evento log_turno do turno atual.
    ultima_intencao: Optional[str] = None
    # Orcamento de turnos (US1, FASE 3, FR-001/FR-002): contadores efemeros em
    # Redis (hash estado:{chamadoId}), incrementados 1x por turno pelo CALLER
    # (webhook.py `_handle_engine`, ANTES de FlowEngine.process) e consumidos
    # por FlowEngine._aplicar_orcamento_turnos para decidir nudge/handoff.
    # Transientes: nunca persistidos em Postgres, apenas plumbing do turno
    # atual — a fonte da verdade e sempre o Redis (fail-open ⇒ 0).
    turnos_sessao: int = 0
    turnos_no_no: int = 0
    # Timeout de inatividade e reengajamento (US2, FASE 5, FR-008/FR-009):
    # horas decorridas desde a `ultima_interacao` anterior (hash Redis
    # estado:{chamadoId}), calculadas pelo CALLER (webhook.py
    # `_bump_ultima_interacao`, fail-open) ANTES de FlowEngine.process().
    # None ⇒ primeiro turno da sessao OU leitura ausente/corrompida —
    # tratado como interacao recente (nenhuma retomada/expiracao disparada).
    # Transiente: nunca persistido, apenas plumbing do turno atual (mesmo
    # padrao de turnos_sessao/turnos_no_no).
    horas_inatividade: Optional[float] = None
    # Overflow de turno (anti-rajada): blocos de conteudo verbatim ainda NAO
    # entregues quando a resposta excedeu max_msgs_per_turn. Bufferizados em Redis
    # (hash estado:{chamadoId}) pelo CALLER (webhook.py) e hidratados aqui ANTES de
    # FlowEngine.process, para que o motor RETOME ("pode continuar") em vez de
    # descartar o restante e cair em abstencao/handoff. Transiente/fail-open.
    overflow_blocos: list[str] = field(default_factory=list)
    overflow_idioma: Optional[str] = None


class MemoryManager:
    """
    Gerencia memoria de sessao (Postgres duravel + Redis quente).

    Responsabilidades:
    - Carregar contexto completo ao inicio do processamento
    - Persistir mensagens (Postgres + Redis)
    - Atualizar variaveis de qualificacao do contato
    - Gerar resumo rolante quando a janela ultrapassa o threshold (SC-002)
    - Recuperar contexto anterior em novo ticket do mesmo contato (US2-AS4)
    """

    def __init__(
        self,
        db_session: AsyncSession,
        redis_client: Any,
        openai_client: Optional[Any] = None,
    ) -> None:
        self._db = db_session
        self._hot = HotWindowManager(redis_client, max_msgs=_HOT_WINDOW_SIZE)
        self._openai = openai_client

    # ------------------------------------------------------------------
    # Carregamento de contexto
    # ------------------------------------------------------------------

    async def load_context(self, chamado_id: int) -> SessionContext:
        """
        Carrega contexto completo da sessao (DB + Redis).

        Sequencia:
        1. Busca ticket pelo chamado_id
        2. Busca contato + variaveis de qualificacao
        3. Busca ou cria SessaoConversa
        4. Carrega janela quente do Redis
        5. Se janela vazia, carrega historico recente do DB
        6. Recupera resumo rolante se existir

        Returns:
            SessionContext populado com dados da sessao atual
        """
        # 1. Buscar ticket
        stmt_ticket = select(Ticket).where(Ticket.chamado_id == chamado_id)
        result = await self._db.execute(stmt_ticket)
        ticket = result.scalar_one_or_none()
        if ticket is None:
            raise ValueError(f"Ticket nao encontrado para chamado_id={chamado_id}")

        # 2. Buscar contato
        stmt_contato = select(Contato).where(Contato.id == ticket.contato_id)
        result = await self._db.execute(stmt_contato)
        contato = result.scalar_one_or_none()
        if contato is None:
            raise ValueError(f"Contato nao encontrado id={ticket.contato_id}")

        # 3. Buscar ou criar SessaoConversa
        stmt_sessao = select(SessaoConversa).where(
            SessaoConversa.ticket_id == ticket.id
        )
        result = await self._db.execute(stmt_sessao)
        sessao = result.scalar_one_or_none()

        if sessao is None:
            # Novo ticket: criar sessao e tentar recuperar contexto anterior (US2-AS4)
            resumo_anterior = await self._recover_previous_summary(contato.id, ticket.id)
            sessao = SessaoConversa(
                ticket_id=ticket.id,
                contato_id=contato.id,
                resumo_rolante=resumo_anterior,
            )
            self._db.add(sessao)
            await self._db.flush()  # obter o id
            logger.info(
                "memory: nova sessao criada ticket_id=%s contato_id=%s",
                ticket.id,
                contato.id,
            )

        # 4. Carregar janela quente do Redis
        historico_recente = await self._hot.get_messages(chamado_id)

        # 5. Se janela vazia (ex: Redis reiniciado), carregar do DB
        if not historico_recente:
            historico_recente = await self._load_recent_from_db(
                sessao.id, limit=_HOT_WINDOW_SIZE
            )

        ctx = SessionContext(
            ticket_id=ticket.id,
            chamado_id=chamado_id,
            contato_id=contato.id,
            caminho=ticket.caminho_atual,
            etapa=ticket.etapa_mapa_mestre,
            idioma=contato.idioma or "pt",
            eh_medico=contato.eh_medico,
            especialidade=contato.especialidade,
            experiencia_corporal=contato.experiencia_corporal,
            produto_interesse=contato.produto_interesse,
            resumo_rolante=sessao.resumo_rolante,
            historico_recente=historico_recente,
            sessao_id=sessao.id,
            nome=contato.nome,
            etapa_funil=contato.etapa_funil,
            perfil=dict(contato.perfil or {}),
        )

        logger.debug(
            "memory: contexto carregado ticket_id=%s idioma=%s caminho=%s msgs=%s",
            ticket.id,
            ctx.idioma,
            ctx.caminho,
            len(historico_recente),
        )
        return ctx

    # ------------------------------------------------------------------
    # Persistencia de mensagens
    # ------------------------------------------------------------------

    async def save_message(self, context: SessionContext, message: dict) -> None:
        """
        Persiste mensagem no historico (Postgres + janela quente Redis).

        Verifica se e necessario gerar resumo rolante (SC-002).

        Args:
            context: contexto da sessao atual (com sessao_id)
            message: dict com keys: direcao, tipo, conteudo, media_url (opcionais)
        """
        if context.sessao_id is None:
            logger.error("memory: sessao_id nao definido no contexto — mensagem nao salva")
            return

        # Persistir no Postgres (append-only)
        msg_row = Mensagem(
            sessao_id=context.sessao_id,
            direcao=message.get("direcao", "inbound"),
            tipo=message.get("tipo", "text"),
            conteudo=message.get("conteudo"),
            media_url=message.get("media_url"),
            wid=message.get("wid"),
            transcrito=message.get("transcrito", False),
        )
        self._db.add(msg_row)

        # Adicionar na janela quente Redis
        await self._hot.push_message(
            context.chamado_id,
            {
                "direcao": message.get("direcao", "inbound"),
                "tipo": message.get("tipo", "text"),
                "conteudo": message.get("conteudo", ""),
            },
        )

        # Atualizar historico_recente do contexto em memoria
        context.historico_recente.append(
            {
                "direcao": message.get("direcao", "inbound"),
                "tipo": message.get("tipo", "text"),
                "conteudo": message.get("conteudo", ""),
            }
        )

        # Verificar se precisa de resumo rolante
        total_msgs = await self._count_messages(context.sessao_id)
        if total_msgs >= _SUMMARIZE_THRESHOLD and self._openai is not None:
            await self._update_rolling_summary(context)

    # ------------------------------------------------------------------
    # Variaveis de qualificacao
    # ------------------------------------------------------------------

    async def update_qualification_variables(
        self, contato_id: int, updates: dict
    ) -> None:
        """
        Atualiza variaveis de qualificacao do contato (nunca apaga por silencio).

        Campos suportados:
        - idioma: 'pt'|'en'|'es'
        - eh_medico: bool
        - especialidade: str
        - experiencia_corporal: bool
        - produto_interesse: str
        - etapa_funil: str
        - perfil: dict (perfil livre/incremental; grava o dict COMPLETO ja mesclado
          pelo chamador — ver merge_perfil. Substitui o JSONB inteiro).

        Apenas campos presentes em `updates` sao modificados.
        """
        if not updates:
            return

        # Filtrar apenas campos validos
        _allowed = {
            "idioma", "eh_medico", "especialidade",
            "experiencia_corporal", "produto_interesse", "etapa_funil", "perfil",
        }
        safe_updates = {k: v for k, v in updates.items() if k in _allowed}
        if not safe_updates:
            return

        stmt = (
            update(Contato)
            .where(Contato.id == contato_id)
            .values(**safe_updates)
        )
        await self._db.execute(stmt)
        logger.debug(
            "memory: variaveis atualizadas contato_id=%s fields=%s",
            contato_id,
            list(safe_updates.keys()),
        )

    async def update_ticket_state(
        self,
        ticket_id: int,
        caminho: Optional[int] = None,
        etapa: Optional[str] = None,
        status: Optional[str] = None,
        handoff_destino: Optional[str] = None,
        handoff_motivo: Optional[str] = None,
    ) -> None:
        """Atualiza caminho/etapa do Mapa Mestre e (opcionalmente) o estado de
        handoff no Ticket (status, destino logico da fila e motivo)."""
        values: dict = {}
        if caminho is not None:
            values["caminho_atual"] = caminho
        if etapa is not None:
            values["etapa_mapa_mestre"] = etapa
        if status is not None:
            values["status"] = status
        if handoff_destino is not None:
            values["handoff_destino"] = handoff_destino
        if handoff_motivo is not None:
            values["handoff_motivo"] = handoff_motivo
        if not values:
            return

        stmt = update(Ticket).where(Ticket.id == ticket_id).values(**values)
        await self._db.execute(stmt)

    # ------------------------------------------------------------------
    # Historico para contexto LLM
    # ------------------------------------------------------------------

    def build_messages_for_llm(
        self, context: SessionContext, max_msgs: int = 10
    ) -> list[dict]:
        """
        Constroi lista de mensagens no formato OpenAI para o historico recente.

        Inclui resumo rolante como primeira mensagem de sistema se disponivel,
        seguido das ultimas `max_msgs` mensagens da janela quente.

        Returns:
            Lista de dicts {"role": "user"|"assistant", "content": str}
        """
        msgs = []

        # Incluir resumo rolante como contexto
        if context.resumo_rolante:
            msgs.append(
                {
                    "role": "system",
                    "content": (
                        f"Resumo da conversa anterior:\n{context.resumo_rolante}"
                    ),
                }
            )

        # Adicionar historico recente (ultimas max_msgs)
        recentes = context.historico_recente[-max_msgs:]
        for m in recentes:
            role = "assistant" if m.get("direcao") == "outbound" else "user"
            content = m.get("conteudo") or ""
            if content:
                msgs.append({"role": role, "content": content})

        return msgs

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    async def _load_recent_from_db(
        self, sessao_id: int, limit: int = 20
    ) -> list[dict]:
        """Carrega as ultimas `limit` mensagens do DB (fallback do Redis)."""
        stmt = (
            select(Mensagem)
            .where(Mensagem.sessao_id == sessao_id)
            .order_by(Mensagem.created_at.desc())
            .limit(limit)
        )
        result = await self._db.execute(stmt)
        rows = result.scalars().all()
        # Retornar em ordem cronologica (mais antigo primeiro)
        return [
            {
                "direcao": m.direcao,
                "tipo": m.tipo,
                "conteudo": m.conteudo or "",
            }
            for m in reversed(rows)
        ]

    async def _count_messages(self, sessao_id: int) -> int:
        """Conta total de mensagens de uma sessao."""
        from sqlalchemy import func
        from sqlalchemy import select as sa_select

        stmt = sa_select(func.count()).select_from(Mensagem).where(
            Mensagem.sessao_id == sessao_id
        )
        result = await self._db.execute(stmt)
        return result.scalar_one() or 0

    async def _update_rolling_summary(self, context: SessionContext) -> None:
        """
        Gera ou atualiza o resumo rolante usando o modelo barato (SC-002).
        Atualiza SessaoConversa.resumo_rolante no DB e o contexto em memoria.
        """
        if self._openai is None:
            return

        # Montar texto das mensagens recentes para sumarizacao
        linhas = []
        for m in context.historico_recente[-_SUMMARIZE_THRESHOLD:]:
            prefixo = "Lead" if m.get("direcao") == "inbound" else "Agente"
            conteudo = m.get("conteudo") or ""
            if conteudo:
                linhas.append(f"{prefixo}: {conteudo}")

        if not linhas:
            return

        conversa_texto = "\n".join(linhas)

        # Incluir resumo anterior como contexto base
        base = ""
        if context.resumo_rolante:
            base = f"Resumo anterior:\n{context.resumo_rolante}\n\nNovas mensagens:\n"

        messages = [
            {"role": "system", "content": _SYSTEM_SUMMARIZE},
            {"role": "user", "content": base + conversa_texto},
        ]

        try:
            novo_resumo = await self._openai.chat_cheap(
                messages, max_tokens=300, temperature=0.0
            )
            context.resumo_rolante = novo_resumo

            # Persistir no DB
            if context.sessao_id is not None:
                stmt = (
                    update(SessaoConversa)
                    .where(SessaoConversa.id == context.sessao_id)
                    .values(
                        resumo_rolante=novo_resumo,
                        ultima_atualizacao_resumo=datetime.utcnow(),
                    )
                )
                await self._db.execute(stmt)
                logger.info(
                    "memory: resumo rolante atualizado sessao_id=%s chars=%s",
                    context.sessao_id,
                    len(novo_resumo),
                )
        except Exception as exc:
            logger.warning("memory: falha ao gerar resumo rolante. err=%s", exc)

    async def _recover_previous_summary(
        self, contato_id: int, current_ticket_id: int
    ) -> Optional[str]:
        """
        Recupera resumo rolante do ticket mais recente do mesmo contato (US2-AS4).
        Ignorando o ticket atual (recém-criado).
        """
        stmt = (
            select(SessaoConversa)
            .join(Ticket, Ticket.id == SessaoConversa.ticket_id)
            .where(
                SessaoConversa.contato_id == contato_id,
                SessaoConversa.ticket_id != current_ticket_id,
                SessaoConversa.resumo_rolante.isnot(None),
            )
            .order_by(SessaoConversa.created_at.desc())
            .limit(1)
        )
        result = await self._db.execute(stmt)
        sessao = result.scalar_one_or_none()
        if sessao and sessao.resumo_rolante:
            logger.info(
                "memory: contexto anterior recuperado contato_id=%s sessao_id=%s",
                contato_id,
                sessao.id,
            )
            return f"[Contexto de atendimento anterior]\n{sessao.resumo_rolante}"
        return None
