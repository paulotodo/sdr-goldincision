"""
Lock distribuido por ticket (chamadoId) para serializacao de processamento.

Evita condicoes de corrida quando multiplas coroutines processam o mesmo
ticket simultaneamente (FR-035-INFRA-MUTEX).
Implementa: data-model.md §Estruturas Redis `lock:ticket:{chamadoId}`.

Mecanismo:
- SET NX PX <settings.lock_ttl_ms> `lock:ticket:{chamadoId}` -> UUID do dono
- Liberar SOMENTE se o dono ainda e o mesmo (Lua script atomico)
- Liberacao erronea (outro dono) e detectada silenciosamente

Implementacao: FASE 3, task 3.2.3. TTL elevado para 90s (default
`settings.lock_ttl_ms`) na FASE 6, task 6.1.1 — ver research.md Decision 4:
pior caso de turno = LLM + ate 4 envios paced + retries (~30-50s), 90s
cobre com folga. Pacing distribuido entre multiplas replicas (FR-014)
fica documentado como decisao consciente adiada — ver research.md
Decision 4/plano-controle-turnos.md §Fase 4 (so necessario com >1 replica;
hoje a stack roda com 1).
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional

from app.config import settings
from app.core.redis_keys import lock_key

logger = logging.getLogger(__name__)

# Fallback documental — o TTL EFETIVO vem de `settings.lock_ttl_ms`
# (env-driven, default 90_000 desde a task 1.1.3/6.1.1). So usado se o
# caller explicitamente passar `lock_ttl_ms=None` E `settings` falhar ao
# carregar (nao deve ocorrer em uso normal).
_LOCK_PX_FALLBACK = 30_000  # 30s em milissegundos (valor pre-FASE 6)

# Script Lua atomico para liberacao segura do lock
# Libera SOMENTE se o valor atual e o token do dono
_LUA_RELEASE = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""


class TicketLock:
    """
    Lock distribuido por ticket usando Redis SET NX PX.

    Chave: `lock:ticket:{chamadoId}`, TTL default `settings.lock_ttl_ms`
    (90s — task 6.1.1; env-driven via `LOCK_TTL_MS`).
    Valor: UUID gerado no acquire — garante que somente o dono libera.

    Uso como context manager:
        async with ticket_lock.acquire(chamado_id) as acquired:
            if not acquired:
                return  # outro processador esta tratando este ticket
            # ... processar ...
    """

    def __init__(self, redis_client: Any, lock_ttl_ms: Optional[int] = None) -> None:
        self._redis = redis_client
        # TTL efetivo: parametro explicito > settings.lock_ttl_ms (env-driven,
        # default 90_000) > fallback documental (task 6.1.1).
        if lock_ttl_ms is not None:
            self._ttl_ms = lock_ttl_ms
        else:
            self._ttl_ms = getattr(settings, "lock_ttl_ms", None) or _LOCK_PX_FALLBACK

    @asynccontextmanager
    async def acquire(self, chamado_id: int) -> AsyncGenerator[bool, None]:
        """
        Context manager: tenta adquirir o lock e libera no exit.

        Yields:
            True  -> lock adquirido com sucesso (proceder ao processamento)
            False -> lock ja esta ocupado (outro processador esta no ticket)

        O lock e SEMPRE liberado no __aexit__ se foi adquirido, mesmo em excecao.
        Se o lock nao foi adquirido, o __aexit__ e nop.
        """
        key = lock_key(chamado_id)
        token = str(uuid.uuid4())

        # SET NX PX: retorna True se criou, None se ja existia
        acquired_result = await self._redis.set(key, token, nx=True, px=self._ttl_ms)
        acquired = acquired_result is not None

        if acquired:
            logger.debug("lock: acquired chamado_id=%s token=%s", chamado_id, token[:8])
        else:
            logger.debug("lock: busy chamado_id=%s (outro processador ativo)", chamado_id)

        try:
            yield acquired
        finally:
            if acquired:
                await self._release(key, token, chamado_id)

    async def _release(self, key: str, token: str, chamado_id: int) -> None:
        """Libera o lock via Lua script atomico (so libera se ainda e dono)."""
        try:
            result = await self._redis.eval(_LUA_RELEASE, 1, key, token)
            if result == 1:
                logger.debug("lock: released chamado_id=%s", chamado_id)
            else:
                # Lock ja expirou ou foi tomado — nao e problema (TTL cuidou)
                logger.debug(
                    "lock: release nop chamado_id=%s (expirou ou outro dono)", chamado_id
                )
        except Exception:
            logger.exception("lock: erro ao liberar chamado_id=%s", chamado_id)

    async def is_locked(self, chamado_id: int) -> bool:
        """Verifica se o lock esta ativo (para testes/diagnostico)."""
        key = lock_key(chamado_id)
        result = await self._redis.exists(key)
        return bool(result)
