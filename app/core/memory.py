"""
Gerenciamento de memoria conversacional — historico duravel + janela quente.

Implementa Principio III (Memoria e Jornada Sem Atrito):
- Historico duravel em Postgres (tabelas mensagem/sessao_conversa)
- Janela quente em Redis (ultimas N mensagens, TTL por sessao)
- Resumo rolante para janelas longas (50+ msgs — SC-002)
- Variaveis de qualificacao persistidas por contato (idioma, eh_medico, etc)
- Nunca repete perguntas ja respondidas (FR-021)
- Recupera contexto em novo ticket do mesmo contato (US2-AS4)

Implementacao completa: FASE 4, task 4.3.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

_HOT_WINDOW_SIZE = 20     # ultimas N msgs na janela quente Redis
_SUMMARIZE_THRESHOLD = 50  # resumo rolante apos N msgs (SC-002)


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


class MemoryManager:
    """
    Gerencia memoria de sessao (Postgres duravel + Redis quente).
    STUB: implementacao completa em FASE 4, task 4.3.
    """

    def __init__(self, db_session, redis_client):
        self._db = db_session
        self._redis = redis_client

    async def load_context(self, chamado_id: int) -> SessionContext:
        """Carrega contexto completo da sessao (DB + Redis)."""
        # TODO (FASE 4): carregar ticket + sessao + variaveis do contato
        # TODO (FASE 4): carregar janela quente do Redis
        # TODO (FASE 4): carregar resumo rolante se janela > threshold
        raise NotImplementedError("MemoryManager implementado em FASE 4")

    async def save_message(self, context: SessionContext, message: dict) -> None:
        """Persiste mensagem no historico (Postgres + janela quente Redis)."""
        # TODO (FASE 4): INSERT em mensagem (append-only)
        # TODO (FASE 4): RPUSH na janela quente + LTRIM
        # TODO (FASE 4): verificar se precisa atualizar resumo rolante
        raise NotImplementedError("MemoryManager.save_message implementado em FASE 4")

    async def update_qualification_variables(
        self, contato_id: int, updates: dict
    ) -> None:
        """Atualiza variaveis de qualificacao do contato (nunca apaga por silencio)."""
        # TODO (FASE 4): UPDATE contato SET ... WHERE id = contato_id
        raise NotImplementedError("MemoryManager.update_qualification_variables implementado em FASE 4")
