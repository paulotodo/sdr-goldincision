"""
Testes da janela quente de mensagens Redis (task 2.2.2).

Cenarios:
- push_message adiciona e renova TTL
- get_messages retorna lista na ordem cronologica
- LTRIM mantem apenas os ultimos max_msgs
- clear() remove a chave
- Itens invalidos (JSON corrompido) sao ignorados
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import pytest

from app.core.hot_window import HOT_WINDOW_MAX_MSGS, HotWindowManager

# ---------------------------------------------------------------------------
# Redis em memoria (reutiliza FakeRedis de test_idempotency_debounce_lock)
# ---------------------------------------------------------------------------

class FakeRedisList:
    """Fake Redis com suporte a LIST para testar HotWindowManager."""

    def __init__(self):
        self._lists: dict[str, list[bytes]] = defaultdict(list)
        self._ttls: dict[str, int] = {}

    async def rpush(self, key: str, *values: Any) -> int:
        for v in values:
            self._lists[key].append(v.encode() if isinstance(v, str) else v)
        return len(self._lists[key])

    async def ltrim(self, key: str, start: int, stop: int) -> bool:
        lst = self._lists.get(key, [])
        if stop == -1:
            self._lists[key] = list(lst[start:])
        else:
            self._lists[key] = list(lst[start:stop + 1])
        return True

    async def expire(self, key: str, seconds: int) -> int:
        self._ttls[key] = seconds
        return 1

    async def lrange(self, key: str, start: int, stop: int) -> list[bytes]:
        lst = self._lists.get(key, [])
        if stop == -1:
            return list(lst[start:])
        return list(lst[start:stop + 1])

    async def llen(self, key: str) -> int:
        return len(self._lists.get(key, []))

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if key in self._lists:
                del self._lists[key]
                count += 1
        return count

    def pipeline(self):
        return FakeListPipeline(self)


class FakeListPipeline:
    def __init__(self, redis: FakeRedisList):
        self._redis = redis
        self._cmds: list = []

    def rpush(self, key, *values):
        self._cmds.append(("rpush", key, values))
        return self

    def ltrim(self, key, start, stop):
        self._cmds.append(("ltrim", key, start, stop))
        return self

    def expire(self, key, seconds):
        self._cmds.append(("expire", key, seconds))
        return self

    async def execute(self):
        results = []
        for cmd in self._cmds:
            if cmd[0] == "rpush":
                results.append(await self._redis.rpush(cmd[1], *cmd[2]))
            elif cmd[0] == "ltrim":
                results.append(await self._redis.ltrim(cmd[1], cmd[2], cmd[3]))
            elif cmd[0] == "expire":
                results.append(await self._redis.expire(cmd[1], cmd[2]))
        self._cmds.clear()
        return results


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_push_message_adiciona():
    """push_message deve adicionar mensagem ao Redis."""
    redis = FakeRedisList()
    mgr = HotWindowManager(redis, max_msgs=5, ttl_seconds=7200)

    await mgr.push_message(1, {"role": "user", "content": "ola"})
    msgs = await mgr.get_messages(1)
    assert len(msgs) == 1
    assert msgs[0]["content"] == "ola"


@pytest.mark.asyncio
async def test_push_message_ordem_cronologica():
    """get_messages deve retornar mensagens na ordem de insercao."""
    redis = FakeRedisList()
    mgr = HotWindowManager(redis, max_msgs=10, ttl_seconds=7200)

    for i in range(5):
        await mgr.push_message(2, {"seq": i})

    msgs = await mgr.get_messages(2)
    assert len(msgs) == 5
    for i, m in enumerate(msgs):
        assert m["seq"] == i


@pytest.mark.asyncio
async def test_ltrim_mantem_max_msgs():
    """Janela deve manter apenas os ultimos max_msgs itens."""
    redis = FakeRedisList()
    max_msgs = 3
    mgr = HotWindowManager(redis, max_msgs=max_msgs, ttl_seconds=7200)

    for i in range(10):
        await mgr.push_message(3, {"seq": i})

    msgs = await mgr.get_messages(3)
    # Deve ter no maximo max_msgs mensagens
    assert len(msgs) <= max_msgs
    # As mensagens retidas devem ser as mais recentes
    seqs = [m["seq"] for m in msgs]
    assert seqs == [7, 8, 9]  # ultimas 3 de 0..9


@pytest.mark.asyncio
async def test_clear_remove_chave():
    """clear() deve remover a janela do Redis."""
    redis = FakeRedisList()
    mgr = HotWindowManager(redis, max_msgs=5, ttl_seconds=7200)

    await mgr.push_message(4, {"content": "msg1"})
    await mgr.push_message(4, {"content": "msg2"})

    assert await mgr.length(4) == 2

    await mgr.clear(4)
    msgs = await mgr.get_messages(4)
    assert msgs == []
    assert await mgr.length(4) == 0


@pytest.mark.asyncio
async def test_get_messages_chave_inexistente():
    """get_messages em chave inexistente deve retornar lista vazia."""
    redis = FakeRedisList()
    mgr = HotWindowManager(redis, max_msgs=5, ttl_seconds=7200)

    msgs = await mgr.get_messages(99999)
    assert msgs == []


@pytest.mark.asyncio
async def test_default_max_msgs():
    """HOT_WINDOW_MAX_MSGS deve ser 20 (valor padrao)."""
    assert HOT_WINDOW_MAX_MSGS == 20


@pytest.mark.asyncio
async def test_ttl_renovado_no_push():
    """TTL deve ser renovado em cada push (expire chamado)."""
    redis = FakeRedisList()
    mgr = HotWindowManager(redis, max_msgs=5, ttl_seconds=3600)

    chamado_id = 5
    await mgr.push_message(chamado_id, {"content": "msg"})

    # Verificar que expire foi chamado na chave correta
    from app.core.redis_keys import hot_window_key
    key = hot_window_key(chamado_id)
    assert key in redis._ttls
    assert redis._ttls[key] == 3600
