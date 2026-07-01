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
from typing import Any, Awaitable, Callable, Optional

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
        delay_seconds: Optional[float] = None,
    ) -> None:
        """
        Aguarda a janela (ou `delay_seconds` custom — usado pelo recovery de
        startup, task 4.1.2) e executa o flush.

        `delay_seconds=None` preserva o comportamento original (janela
        cheia `self._debounce_seconds`, fluxo normal de `push_and_schedule`).
        `delay_seconds=0` dispara flush imediato (janela ja expirada).
        """
        await asyncio.sleep(
            self._debounce_seconds if delay_seconds is None else delay_seconds
        )
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

    async def recover_pending(
        self,
        flush_callback: Callable[[int, list[dict]], Awaitable[None]],
    ) -> int:
        """
        Recovery de startup (US3, FASE 4, task 4.1.1/4.1.2): escaneia todas
        as chaves `debounce:*` remanescentes de um restart/deploy anterior e
        reagenda (ou dispara imediatamente) o flush de cada rajada pendente,
        sem exigir nova mensagem do lead (Acceptance Scenario 1, US3).

        Estrategia conservadora (task 4.1.2): a chave so recebe TTL no push
        (`push_and_schedule`: `ttl = debounce_seconds + _TTL_MARGIN_SECONDS`).
        O TTL restante (`TTL key`) revela quanto tempo decorreu desde o
        ultimo push:

            janela_restante = ttl_restante - _TTL_MARGIN_SECONDS
              > 0  -> ainda dentro da janela de debounce: reagenda o flush
                      para daqui a `janela_restante` segundos (equivalente a
                      completar a janela original interrompida pelo restart).
              <= 0 -> janela ja tinha expirado no momento do restart
                      (Acceptance Scenario 2, US3): flush imediato
                      (`delay_seconds=0`).

        TTL ausente/expirado (`-2`) ou sem expiracao (`-1`, nao deveria
        ocorrer pois toda chave e criada com `EXPIRE`) tambem cai no ramo de
        flush imediato — postura conservadora: nunca deixar uma rajada
        "presa" esperando um agendamento que nunca vai disparar.

        Idempotente (task 4.1.3/4.1.7): o flush em si (`flush()`) e um
        LRANGE+DEL atomico via pipeline — se `recover_pending` rodar mais de
        uma vez (ex.: retries de lifespan, multiplos workers), a segunda
        chamada encontra a lista ja vazia e o callback simplesmente nao e
        invocado de novo (mesma garantia do fluxo normal de
        `_delayed_flush`). O gate de fila (IA=77) e o filtro de estado do
        ticket (em_handoff/encerrado) sao reavaliados no proprio
        `flush_callback` (task 4.1.4) — nao precisam ser duplicados aqui.

        Nao bloqueia o startup: cada rajada e agendada como task asyncio
        independente (mesmo padrao de `push_and_schedule`); esta corrotina
        so faz o SCAN + TTL (leitura rapida) e retorna.

        Returns:
            Quantidade de chamados com rajada pendente recuperada (agendada
            ou flushed imediatamente).
        """
        recovered = 0
        cursor = 0
        pattern = "debounce:*"
        while True:
            cursor, keys = await self._redis.scan(cursor=cursor, match=pattern, count=100)
            for raw_key in keys or []:
                key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
                try:
                    chamado_id = int(key.split(":", 1)[1])
                except (IndexError, ValueError):
                    logger.warning(
                        "debounce: recovery chave invalida ignorada key=%s", key
                    )
                    continue

                ttl = await self._redis.ttl(key)
                janela_restante = (
                    ttl - _TTL_MARGIN_SECONDS if ttl is not None and ttl > 0 else -1
                )

                if janela_restante > 0:
                    logger.info(
                        "debounce: recovery chamado_id=%s reagendando flush em %ss",
                        chamado_id, janela_restante,
                    )
                    delay = janela_restante
                else:
                    logger.info(
                        "debounce: recovery chamado_id=%s janela expirada/ausente "
                        "(ttl=%s) — flush imediato",
                        chamado_id, ttl,
                    )
                    delay = 0

                asyncio.create_task(
                    self._delayed_flush(
                        chamado_id, flush_callback, delay_seconds=delay
                    ),
                    name=f"debounce-recovery-flush-{chamado_id}",
                )
                recovered += 1

            if not cursor:
                break

        return recovered
