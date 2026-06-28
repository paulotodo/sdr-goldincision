"""
Janela quente de mensagens por sessao (chamadoId) usando Redis LIST.

Implementa task 2.2.2 (FASE 2) — prerequisito para MemoryManager (FASE 4).

Redis key: `sessao:{chamadoId}:hot`
Tipo: LIST (RPUSH / LTRIM / LRANGE / EXPIRE)
TTL: HOT_WINDOW_TTL_SECONDS (configuravel, default 2h)
Tamanho: HOT_WINDOW_MAX_MSGS (configuravel, default 20)

Responsabilidades:
- push_message(): adicionar mensagem + LTRIM + EXPIRE
- get_messages(): ler janela atual (LRANGE 0 -1)
- clear(): limpar janela (DEL)
- Serializacao: JSON compacto sem unicode-escape (ensure_ascii=False)
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.core.redis_keys import HOT_WINDOW_TTL_SECONDS, hot_window_key

logger = logging.getLogger(__name__)

# Tamanho maximo da janela (ultimas N mensagens)
HOT_WINDOW_MAX_MSGS: int = 20


class HotWindowManager:
    """
    Gerencia a janela quente de mensagens recentes em Redis.

    Cada chamadoId tem sua propria lista (sessao:{chamadoId}:hot).
    A janela e aparada automaticamente para os ultimos HOT_WINDOW_MAX_MSGS
    itens a cada push.  O TTL e renovado em cada operacao de escrita.
    """

    def __init__(
        self,
        redis_client: Any,
        max_msgs: int = HOT_WINDOW_MAX_MSGS,
        ttl_seconds: int = HOT_WINDOW_TTL_SECONDS,
    ) -> None:
        self._redis = redis_client
        self._max_msgs = max_msgs
        self._ttl_seconds = ttl_seconds

    async def push_message(self, chamado_id: int, message: dict) -> int:
        """
        Adiciona mensagem a janela quente.

        Aplica LTRIM para manter somente os ultimos `max_msgs` itens.
        Renova o TTL da chave.

        Returns:
            Comprimento da lista apos o push (antes do trim).
        """
        key = hot_window_key(chamado_id)
        serialized = json.dumps(message, ensure_ascii=False)

        # Pipeline atomico: RPUSH + LTRIM + EXPIRE
        pipe = self._redis.pipeline()
        pipe.rpush(key, serialized)
        # Manter somente os ultimos max_msgs (offset do fim = -(max_msgs)-1 do inicio)
        pipe.ltrim(key, -self._max_msgs, -1)
        pipe.expire(key, self._ttl_seconds)
        results = await pipe.execute()

        length_after_push = results[0]
        logger.debug(
            "hot_window: push chamado_id=%s len=%s (apos push, antes trim)",
            chamado_id,
            length_after_push,
        )
        return int(length_after_push)

    async def get_messages(self, chamado_id: int) -> list[dict]:
        """
        Retorna a janela de mensagens recentes (ordem cronologica: mais antiga primeiro).

        Returns:
            Lista de dicts deserializados. Lista vazia se chave inexistente.
        """
        key = hot_window_key(chamado_id)
        raw_items = await self._redis.lrange(key, 0, -1)
        if not raw_items:
            return []

        messages = []
        for item in raw_items:
            try:
                text = item.decode() if isinstance(item, bytes) else item
                messages.append(json.loads(text))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.warning(
                    "hot_window: item invalido ignorado chamado_id=%s err=%s",
                    chamado_id,
                    exc,
                )
        return messages

    async def clear(self, chamado_id: int) -> None:
        """Remove a janela quente (DEL) — ex: apos handoff ou encerramento."""
        key = hot_window_key(chamado_id)
        await self._redis.delete(key)
        logger.debug("hot_window: cleared chamado_id=%s", chamado_id)

    async def length(self, chamado_id: int) -> int:
        """Retorna o numero de mensagens na janela atual."""
        key = hot_window_key(chamado_id)
        result = await self._redis.llen(key)
        return int(result or 0)
