"""
Debounce de rajada de mensagens por ticket (chamadoId).

Consolida rafagas de ate 5 mensagens em uma unica chamada ao motor (SC-005).
Implementa: FR-003, data-model.md §Estruturas Redis `debounce:{chamadoId}`.

Mecanismo:
- RPUSH `debounce:{chamadoId}` (cada mensagem nova)
- Agendar flush apos DEBOUNCE_SECONDS (default 8s)
- No flush: ler todas as msgs da lista, processar consolidado, deletar lista

Implementacao completa: FASE 3, task 3.2.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class DebounceManager:
    """
    Gerencia janela de debounce por ticket usando Redis LIST.

    Chave Redis: `debounce:{chamadoId}`
    TTL: janela + margem (descartado automaticamente se flush nao rodar)

    STUB: implementacao completa em FASE 3, task 3.2.
    """

    def __init__(self, redis_client, debounce_seconds: int = 8):
        self._redis = redis_client
        self._debounce_seconds = debounce_seconds

    async def push_and_schedule(
        self, chamado_id: int, message_data: dict, flush_callback: Callable
    ) -> None:
        """
        Enfileira mensagem e agenda flush apos janela de debounce.

        Args:
            chamado_id: id do ticket (chamado)
            message_data: dados da mensagem
            flush_callback: coroutine chamada com lista consolidada apos janela
        """
        # TODO (FASE 3): RPUSH para Redis
        # TODO (FASE 3): Agendar asyncio.sleep + flush via BackgroundTasks
        raise NotImplementedError("DebounceManager implementado em FASE 3")

    async def flush(self, chamado_id: int) -> list[dict]:
        """
        Retorna e remove todas as mensagens acumuladas do ticket.

        Returns:
            Lista de mensagens consolidadas (pode estar vazia se ja foi flushed)
        """
        # TODO (FASE 3): LRANGE + DEL atomico (MULTI/EXEC)
        raise NotImplementedError("DebounceManager.flush implementado em FASE 3")
