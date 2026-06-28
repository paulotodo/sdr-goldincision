"""
Debounce de rajada de mensagens por ticket (chamadoId).

Consolida rafagas de ate 5 mensagens em uma unica chamada ao motor (SC-005).
Implementa: FR-003, data-model.md §Estruturas Redis `debounce:{chamadoId}`.

Mecanismo:
- RPUSH `debounce:{chamadoId}` (cada mensagem nova)
- Agendar flush apos DEBOUNCE_SECONDS (default 8s) via asyncio.create_task
- No flush: LRANGE + DEL atomico (pipeline), processar lista consolidada
- Se flush ja ocorreu (lista vazia): nop

Implementacao: FASE 3, task 3.2.2
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from app.core.redis_keys import debounce_key

logger = logging.getLogger(__name__)

# Margem extra de TTL alem da janela de debounce (segundos)
_TTL_MARGIN_SECONDS = 30


class DebounceManager:
    """
    Gerencia janela de debounce por ticket usando Redis LIST.

    Chave Redis: `debounce:{chamadoId}` (LIST, TTL = debounce_seconds + margem)

    Uso tipico no handler de webhook:
        async def on_message(chamado_id, msg_data):
            await debounce_mgr.push_and_schedule(chamado_id, msg_data, process_msgs)

    O flush e idempotente: a lista e deletada atomicamente antes de processar,
    entao chamadas concorrentes ao flush resultam em exatamente uma execucao.
    """

    def __init__(
        self,
        redis_client: Any,
        debounce_seconds: int = 8,
    ) -> None:
        self._redis = redis_client
        self._debounce_seconds = debounce_seconds

    async def push_and_schedule(
        self,
        chamado_id: int,
        message_data: dict,
        flush_callback: Callable[[int, list[dict]], Awaitable[None]],
    ) -> int:
        """
        Enfileira mensagem e agenda flush apos janela de debounce.

        O flush e agendado como task asyncio independente. Se outra mensagem
        chegar antes do flush, ela e enfileirada e o flush ja agendado vai
        consumi-la tambem (o primeiro flush que rodar pega tudo e deleta).

        Args:
            chamado_id:     id do ticket
            message_data:   dict da mensagem (sera serializado em JSON)
            flush_callback: coroutine(chamado_id, messages) chamada no flush

        Returns:
            Comprimento da lista apos o push.
        """
        key = debounce_key(chamado_id)
        serialized = json.dumps(message_data, ensure_ascii=False, default=str)
        ttl = self._debounce_seconds + _TTL_MARGIN_SECONDS

        pipe = self._redis.pipeline()
        pipe.rpush(key, serialized)
        pipe.expire(key, ttl)
        results = await pipe.execute()
        length = int(results[0])

        logger.debug(
            "debounce: push chamado_id=%s list_len=%s janela=%ss",
            chamado_id, length, self._debounce_seconds,
        )

        # Agendar flush apenas na PRIMEIRA mensagem da rajada
        # (as demais chegam antes do flush acontecer e sao acumuladas)
        if length == 1:
            asyncio.create_task(
                self._delayed_flush(chamado_id, flush_callback),
                name=f"debounce-flush-{chamado_id}",
            )

        return length

    async def _delayed_flush(
        self,
        chamado_id: int,
        callback: Callable[[int, list[dict]], Awaitable[None]],
    ) -> None:
        """Aguarda a janela e executa o flush."""
        await asyncio.sleep(self._debounce_seconds)
        messages = await self.flush(chamado_id)
        if not messages:
            logger.debug("debounce: flush chamado_id=%s lista vazia (ja flushed)", chamado_id)
            return
        logger.info(
            "debounce: flush chamado_id=%s %d msgs consolidadas",
            chamado_id, len(messages),
        )
        try:
            await callback(chamado_id, messages)
        except Exception:
            logger.exception(
                "debounce: erro no callback pos-flush chamado_id=%s", chamado_id
            )

    async def flush(self, chamado_id: int) -> list[dict]:
        """
        Retorna e remove todas as mensagens acumuladas do ticket (atomico).

        Usa pipeline Redis (LRANGE + DEL) para garantir que cada mensagem
        seja processada exatamente uma vez, mesmo com concorrencia.

        Returns:
            Lista de dicts (pode estar vazia se ja foi flushed).
        """
        key = debounce_key(chamado_id)

        # Atomico: pegar tudo e apagar em um pipeline
        pipe = self._redis.pipeline()
        pipe.lrange(key, 0, -1)
        pipe.delete(key)
        results = await pipe.execute()

        raw_items: list[bytes] = results[0] or []
        messages: list[dict] = []
        for raw in raw_items:
            try:
                text = raw.decode() if isinstance(raw, bytes) else raw
                messages.append(json.loads(text))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.warning(
                    "debounce: item invalido ignorado chamado_id=%s err=%s",
                    chamado_id, exc,
                )
        return messages

    async def queue_length(self, chamado_id: int) -> int:
        """Retorna quantas mensagens estao na fila (para testes)."""
        key = debounce_key(chamado_id)
        result = await self._redis.llen(key)
        return int(result or 0)
