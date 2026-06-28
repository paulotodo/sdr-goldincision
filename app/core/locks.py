"""
Lock por ticket (chamadoId) para serializacao de processamento.

Evita condicoes de corrida quando multiplas instancias processam o mesmo
ticket simultaneamente (FR-035-INFRA-MUTEX).
Implementa: data-model.md §Estruturas Redis `lock:ticket:{chamadoId}`.

Mecanismo:
- SET NX PX 30000 `lock:ticket:{chamadoId}` -> UUID do dono
- Liberar apenas se o dono ainda e o mesmo (Lua script atomico)

Implementacao completa: FASE 3, task 3.2.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

_LOCK_PX = 30_000  # 30s em milissegundos


class TicketLock:
    """
    Lock distribuido por ticket usando Redis SET NX PX.
    Chave: `lock:ticket:{chamadoId}`, TTL 30s.
    STUB: implementacao completa em FASE 3, task 3.2.
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    @asynccontextmanager
    async def acquire(self, chamado_id: int) -> AsyncGenerator[bool, None]:
        """
        Context manager: adquire lock, executa bloco, libera.
        Yields:
            True se lock adquirido, False se ja ocupado (skip processamento).
        """
        # TODO (FASE 3): SET NX PX 30000 com UUID como valor
        # TODO (FASE 3): liberar via Lua script atomico no __aexit__
        raise NotImplementedError("TicketLock implementado em FASE 3")
        yield  # pragma: no cover
