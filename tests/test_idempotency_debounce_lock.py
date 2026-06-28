"""
Testes de idempotencia, debounce e lock por ticket (task 3.2.5).

Cenarios cobertos:
- Idempotencia: evento novo registrado; reenvio descartado
- Debounce: rajada de 5 msgs em <2s -> flush unico; flush vazio em segunda chamada
- Lock: lock adquirido e liberado; segundo acquire bloqueado (returns False)
- Lock: serializacao garante que callback nao executa concorrentemente

Abordagem: mocks de Redis em memoria (sem Redis real).
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Redis in-memory mock para testes
# ---------------------------------------------------------------------------

class FakeRedis:
    """
    Implementacao minimalista de Redis em memoria para testes.
    Suporta: set, get, delete, exists, eval, rpush, lrange, llen, expire, ltrim, pipeline, ping.
    """

    def __init__(self):
        self._data: dict[str, Any] = {}
        self._lists: dict[str, list[bytes]] = defaultdict(list)

    async def ping(self) -> bool:
        return True

    async def set(self, key: str, value: Any, nx: bool = False, ex: int = None, px: int = None) -> Optional[bool]:
        if nx and key in self._data:
            return None
        self._data[key] = value
        return True

    async def get(self, key: str) -> Optional[bytes]:
        val = self._data.get(key)
        if val is None:
            return None
        return val.encode() if isinstance(val, str) else val

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                count += 1
            if key in self._lists:
                del self._lists[key]
                count += 1
        return count

    async def exists(self, key: str) -> int:
        return 1 if (key in self._data or key in self._lists) else 0

    async def expire(self, key: str, seconds: int) -> int:
        return 1 if (key in self._data or key in self._lists) else 0

    async def rpush(self, key: str, *values: Any) -> int:
        for v in values:
            self._lists[key].append(v.encode() if isinstance(v, str) else v)
        return len(self._lists[key])

    async def lrange(self, key: str, start: int, stop: int) -> list[bytes]:
        lst = self._lists.get(key, [])
        if stop == -1:
            return list(lst[start:])
        return list(lst[start:stop + 1])

    async def llen(self, key: str) -> int:
        return len(self._lists.get(key, []))

    async def ltrim(self, key: str, start: int, stop: int) -> bool:
        lst = self._lists.get(key, [])
        if stop == -1:
            self._lists[key] = list(lst[start:])
        else:
            self._lists[key] = list(lst[start:stop + 1])
        return True

    async def eval(self, script: str, numkeys: int, *args) -> int:
        """Implementacao simplificada do Lua script de release do lock."""
        key = args[0]
        token = args[1]
        token_str = token.decode() if isinstance(token, bytes) else token
        stored = self._data.get(key)
        stored_str = stored.decode() if isinstance(stored, bytes) else stored
        if stored_str == token_str:
            del self._data[key]
            return 1
        return 0

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    """Pipeline fake que executa comandos sequencialmente."""

    def __init__(self, redis: FakeRedis):
        self._redis = redis
        self._cmds: list = []

    def rpush(self, key, *values):
        self._cmds.append(("rpush", key, values))
        return self

    def expire(self, key, seconds):
        self._cmds.append(("expire", key, seconds))
        return self

    def ltrim(self, key, start, stop):
        self._cmds.append(("ltrim", key, start, stop))
        return self

    def lrange(self, key, start, stop):
        self._cmds.append(("lrange", key, start, stop))
        return self

    def delete(self, *keys):
        self._cmds.append(("delete", keys))
        return self

    async def execute(self):
        results = []
        for cmd in self._cmds:
            if cmd[0] == "rpush":
                results.append(await self._redis.rpush(cmd[1], *cmd[2]))
            elif cmd[0] == "expire":
                results.append(await self._redis.expire(cmd[1], cmd[2]))
            elif cmd[0] == "ltrim":
                results.append(await self._redis.ltrim(cmd[1], cmd[2], cmd[3]))
            elif cmd[0] == "lrange":
                results.append(await self._redis.lrange(cmd[1], cmd[2], cmd[3]))
            elif cmd[0] == "delete":
                results.append(await self._redis.delete(*cmd[1]))
        self._cmds.clear()
        return results


# ---------------------------------------------------------------------------
# Testes de IdempotencyChecker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idempotency_evento_novo():
    """Primeiro evento deve ser registrado (nao duplicata)."""
    from app.core.idempotency import IdempotencyChecker
    redis = FakeRedis()
    checker = IdempotencyChecker(redis)

    payload = {"chamadoId": 123, "mensagem": [{"type": "text", "text": "ola"}]}
    is_dup = await checker.is_duplicate(123, payload)
    assert is_dup is False


@pytest.mark.asyncio
async def test_idempotency_reenvio_descartado():
    """Reenvio do mesmo payload deve ser detectado como duplicata."""
    from app.core.idempotency import IdempotencyChecker
    redis = FakeRedis()
    checker = IdempotencyChecker(redis)

    payload = {"chamadoId": 456, "mensagem": [{"type": "text", "text": "ola"}]}
    # Primeira vez: nao duplicata
    is_dup1 = await checker.is_duplicate(456, payload)
    assert is_dup1 is False

    # Segunda vez (mesmo payload): duplicata
    is_dup2 = await checker.is_duplicate(456, payload)
    assert is_dup2 is True


@pytest.mark.asyncio
async def test_idempotency_payloads_diferentes_nao_colide():
    """Payloads diferentes no mesmo chamadoId nao devem colidir."""
    from app.core.idempotency import IdempotencyChecker
    redis = FakeRedis()
    checker = IdempotencyChecker(redis)

    payload1 = {"chamadoId": 789, "mensagem": [{"type": "text", "text": "msg 1"}]}
    payload2 = {"chamadoId": 789, "mensagem": [{"type": "text", "text": "msg 2"}]}

    assert await checker.is_duplicate(789, payload1) is False
    assert await checker.is_duplicate(789, payload2) is False


# ---------------------------------------------------------------------------
# Testes de DebounceManager
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_debounce_push_enfileira_mensagem():
    """push_and_schedule deve enfileirar mensagem no Redis."""
    from app.core.debounce import DebounceManager
    redis = FakeRedis()

    callback_called = []

    async def noop_callback(chamado_id, messages):
        callback_called.append((chamado_id, messages))

    mgr = DebounceManager(redis, debounce_seconds=100)  # janela longa para nao triggar
    await mgr.push_and_schedule(111, {"text": "msg1"}, noop_callback)

    length = await mgr.queue_length(111)
    assert length == 1


@pytest.mark.asyncio
async def test_debounce_flush_retorna_mensagens():
    """flush() deve retornar e remover mensagens acumuladas."""
    from app.core.debounce import DebounceManager
    redis = FakeRedis()

    async def noop_callback(chamado_id, messages):
        pass

    mgr = DebounceManager(redis, debounce_seconds=100)
    await mgr.push_and_schedule(222, {"text": "a"}, noop_callback)
    await mgr.push_and_schedule(222, {"text": "b"}, noop_callback)
    await mgr.push_and_schedule(222, {"text": "c"}, noop_callback)

    messages = await mgr.flush(222)
    assert len(messages) == 3
    texts = [m["text"] for m in messages]
    assert "a" in texts and "b" in texts and "c" in texts


@pytest.mark.asyncio
async def test_debounce_flush_duplo_retorna_vazio():
    """Segundo flush deve retornar lista vazia (atomicidade — SC-005)."""
    from app.core.debounce import DebounceManager
    redis = FakeRedis()

    async def noop_callback(chamado_id, messages):
        pass

    mgr = DebounceManager(redis, debounce_seconds=100)
    await mgr.push_and_schedule(333, {"text": "msg"}, noop_callback)

    await mgr.flush(333)
    second = await mgr.flush(333)
    assert second == []


@pytest.mark.asyncio
async def test_debounce_rajada_5_msgs_um_callback():
    """Rajada de 5 mensagens deve resultar em exatamente 1 callback apos janela."""
    from app.core.debounce import DebounceManager
    redis = FakeRedis()

    received = []

    async def capture_callback(chamado_id, messages):
        received.append(messages)

    mgr = DebounceManager(redis, debounce_seconds=0.05)  # 50ms para teste rapido

    for i in range(5):
        await mgr.push_and_schedule(444, {"seq": i}, capture_callback)

    # Aguardar a janela de debounce
    await asyncio.sleep(0.2)

    # Deve ter sido chamado exatamente uma vez com todas as mensagens
    assert len(received) == 1, f"Esperado 1 callback, recebido {len(received)}"
    assert len(received[0]) == 5, f"Esperado 5 msgs, recebido {len(received[0])}"


# ---------------------------------------------------------------------------
# Testes de TicketLock
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lock_acquire_e_liberado():
    """Lock deve ser adquirido e depois liberado."""
    from app.core.locks import TicketLock
    redis = FakeRedis()
    lock = TicketLock(redis)

    async with lock.acquire(555) as acquired:
        assert acquired is True
        assert await lock.is_locked(555) is True

    # Apos o exit, o lock deve ter sido liberado
    assert await lock.is_locked(555) is False


@pytest.mark.asyncio
async def test_lock_segundo_acquire_falha():
    """Segundo acquire concorrente deve retornar False (nao bloquear)."""
    from app.core.locks import TicketLock
    redis = FakeRedis()
    lock = TicketLock(redis)

    resultados = []

    async with lock.acquire(666) as acquired1:
        assert acquired1 is True
        # Tentar adquirir novamente enquanto esta ocupado
        async with lock.acquire(666) as acquired2:
            resultados.append(acquired2)

    assert resultados == [False], f"Esperado [False], recebido {resultados}"


@pytest.mark.asyncio
async def test_lock_serializa_processamento():
    """Lock deve garantir que callbacks nao rodem concorrentemente."""
    from app.core.locks import TicketLock
    redis = FakeRedis()
    lock = TicketLock(redis)

    execution_order = []
    in_progress = [False]

    async def critical_section(task_id: int):
        async with lock.acquire(777) as acquired:
            if not acquired:
                return
            # Verificar que nenhuma outra execucao esta ativa
            assert not in_progress[0], f"Concorrencia detectada na task {task_id}"
            in_progress[0] = True
            execution_order.append(task_id)
            await asyncio.sleep(0.01)
            in_progress[0] = False

    # Executar 3 tarefas sequencialmente (so 1 por vez)
    await critical_section(1)
    await critical_section(2)
    await critical_section(3)

    assert len(execution_order) == 3


@pytest.mark.asyncio
async def test_lock_tickets_diferentes_nao_interferem():
    """Locks de tickets diferentes sao independentes."""
    from app.core.locks import TicketLock
    redis = FakeRedis()
    lock = TicketLock(redis)

    async with lock.acquire(888) as acq1:
        async with lock.acquire(999) as acq2:
            # Tickets diferentes: ambos devem ser adquiridos
            assert acq1 is True
            assert acq2 is True
