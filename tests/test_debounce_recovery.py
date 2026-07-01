"""
Testes de recovery de debounce em restart (US3, FASE 4, task 4.1.5/4.1.6/4.1.7).

Cenarios cobertos:
- Rajada pendente (janela ainda nao expirada) e reagendada e processada
  automaticamente no restart, sem exigir nova mensagem do lead (AS1).
- Janela ja expirada no momento do restart -> processamento imediato (AS2).
- `recover_pending` executado mais de uma vez processa a rajada exatamente
  uma vez (idempotencia via flush() atomico — AS3, FR-012).
- Nenhuma chave `debounce:*` pendente -> no-op (0 recuperados).
- Chave com TTL ausente (-1/-2, defensivo) -> flush imediato (postura
  conservadora, nunca "presa").

Abordagem: FakeRedis em memoria com suporte a scan/ttl (sem Redis real).
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any, Optional

import pytest


class FakeRedis:
    """
    Redis fake minimalista com suporte a scan/ttl/rpush/lrange/delete/pipeline —
    o suficiente para exercitar `DebounceManager.recover_pending`.
    """

    def __init__(self):
        self._lists: dict[str, list[bytes]] = defaultdict(list)
        self._ttl: dict[str, int] = {}

    async def rpush(self, key: str, *values: Any) -> int:
        for v in values:
            self._lists[key].append(v.encode() if isinstance(v, str) else v)
        return len(self._lists[key])

    async def lrange(self, key: str, start: int, stop: int) -> list[bytes]:
        lst = self._lists.get(key, [])
        if stop == -1:
            return list(lst[start:])
        return list(lst[start:stop + 1])

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if key in self._lists:
                del self._lists[key]
                count += 1
            self._ttl.pop(key, None)
        return count

    async def expire(self, key: str, seconds: int) -> int:
        self._ttl[key] = seconds
        return 1 if key in self._lists else 0

    async def ttl(self, key: str) -> int:
        """Retorna o TTL simulado (segundos). -2 se a chave nao existe."""
        if key not in self._lists:
            return -2
        return self._ttl.get(key, -1)

    async def scan(self, cursor: int = 0, match: Optional[str] = None, count: int = 10):
        """Scan simplificado: retorna TODAS as chaves casando o padrao em 1 pagina."""
        prefix = (match or "*").rstrip("*")
        keys = [k for k in self._lists.keys() if k.startswith(prefix)]
        return 0, [k.encode() for k in keys]

    def pipeline(self):
        return FakePipeline(self)

    def set_ttl(self, key: str, seconds: int) -> None:
        """Helper de teste: forca o TTL restante de uma chave (simula tempo decorrido)."""
        self._ttl[key] = seconds


class FakePipeline:
    def __init__(self, redis: FakeRedis):
        self._redis = redis
        self._cmds: list = []

    def rpush(self, key, *values):
        self._cmds.append(("rpush", key, values))
        return self

    def expire(self, key, seconds):
        self._cmds.append(("expire", key, seconds))
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
            elif cmd[0] == "lrange":
                results.append(await self._redis.lrange(cmd[1], cmd[2], cmd[3]))
            elif cmd[0] == "delete":
                results.append(await self._redis.delete(*cmd[1]))
        self._cmds.clear()
        return results


async def _seed_debounce_list(redis: FakeRedis, chamado_id: int, n_msgs: int, ttl_seconds: int) -> None:
    """Simula rajada ja empurrada para o Redis por um processo anterior (pre-restart)."""
    key = f"debounce:{chamado_id}"
    for i in range(n_msgs):
        await redis.rpush(key, json.dumps({"seq": i}))
    redis.set_ttl(key, ttl_seconds)


# ---------------------------------------------------------------------------
# AS1: janela ainda nao expirada -> reagendada e processada sem nova mensagem
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recovery_janela_pendente_processa_automaticamente():
    from app.core.debounce import _TTL_MARGIN_SECONDS, DebounceManager

    redis = FakeRedis()
    # ttl restante = margem + 0.15s -> janela_restante = 0.15s (ainda dentro
    # da janela de debounce original, interrompida pelo restart). Valor
    # fracionario e apenas para o teste rodar rapido — o TTL real do Redis
    # e sempre inteiro, mas a formula (ttl_restante - margem) e a mesma.
    await _seed_debounce_list(redis, 1001, n_msgs=3, ttl_seconds=_TTL_MARGIN_SECONDS + 0.15)

    received: list[tuple[int, list[dict]]] = []

    async def capture_callback(chamado_id, messages):
        received.append((chamado_id, messages))

    mgr = DebounceManager(redis, debounce_seconds=8)
    n = await mgr.recover_pending(capture_callback)
    assert n == 1

    # Callback ainda nao rodou (aguardando os ~0.15s restantes da janela)
    assert received == []

    await asyncio.sleep(0.3)

    assert len(received) == 1
    chamado_id, messages = received[0]
    assert chamado_id == 1001
    assert len(messages) == 3


# ---------------------------------------------------------------------------
# AS2: janela ja expirada no momento do restart -> processamento imediato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recovery_janela_expirada_flush_imediato():
    from app.core.debounce import DebounceManager

    redis = FakeRedis()
    # ttl restante <= margem (30) => janela ja tinha expirado antes do restart.
    await _seed_debounce_list(redis, 1002, n_msgs=2, ttl_seconds=10)

    received: list[tuple[int, list[dict]]] = []

    async def capture_callback(chamado_id, messages):
        received.append((chamado_id, messages))

    mgr = DebounceManager(redis, debounce_seconds=8)
    n = await mgr.recover_pending(capture_callback)
    assert n == 1

    # delay_seconds=0 -> asyncio.sleep(0) ainda cede o loop; um yield basta.
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0][0] == 1002
    assert len(received[0][1]) == 2


# ---------------------------------------------------------------------------
# AS3 / FR-012: recovery executado mais de uma vez processa exatamente 1x
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recovery_idempotente_executado_duas_vezes():
    from app.core.debounce import DebounceManager

    redis = FakeRedis()
    await _seed_debounce_list(redis, 1003, n_msgs=4, ttl_seconds=10)  # janela expirada

    received: list[tuple[int, list[dict]]] = []

    async def capture_callback(chamado_id, messages):
        received.append((chamado_id, messages))

    mgr = DebounceManager(redis, debounce_seconds=8)

    # Primeira chamada de recovery (ex.: lifespan do processo A)
    n1 = await mgr.recover_pending(capture_callback)
    assert n1 == 1
    await asyncio.sleep(0.05)
    assert len(received) == 1

    # Segunda chamada de recovery (ex.: retry de lifespan / outro worker) —
    # a lista ja foi deletada pelo primeiro flush (LRANGE+DEL atomico), entao
    # a chave nao aparece mais no SCAN.
    n2 = await mgr.recover_pending(capture_callback)
    assert n2 == 0
    await asyncio.sleep(0.05)

    # Callback continua tendo sido chamado exatamente 1 vez no total.
    assert len(received) == 1


@pytest.mark.asyncio
async def test_recovery_ja_flushed_antes_do_scan_nao_aparece_e_nao_reprocessa():
    """
    O flush() e um LRANGE+DEL atomico: uma vez flushed, a chave deixa de
    existir no keyspace (igual ao Redis real apos DEL) — portanto um SCAN
    posterior nunca a encontra. `recover_pending` deve simplesmente ignorar
    o chamado ja resolvido (0 recuperados), sem chamar o callback de novo.
    """
    from app.core.debounce import DebounceManager

    redis = FakeRedis()
    key = "debounce:1004"
    await redis.rpush(key, json.dumps({"seq": 0}))
    redis.set_ttl(key, 10)

    called = []

    async def capture_callback(chamado_id, messages):
        called.append((chamado_id, messages))

    mgr = DebounceManager(redis, debounce_seconds=8)

    # Flush ja aconteceu (ex.: outro worker) ANTES do recovery escanear.
    flushed_msgs = await mgr.flush(1004)
    assert len(flushed_msgs) == 1

    n = await mgr.recover_pending(capture_callback)
    assert n == 0  # a chave nao existe mais — SCAN nao a encontra

    await asyncio.sleep(0.05)
    assert called == []


@pytest.mark.asyncio
async def test_recovery_concorre_com_delayed_flush_normal_processa_uma_vez():
    """
    Corrida real (task 4.1.3): o recovery de startup agenda um flush para o
    MESMO chamado_id que ja tinha um `_delayed_flush` normal em curso (ex.:
    processo reiniciou entre o push e o flush agendado, mas o novo processo
    tambem recebeu uma mensagem nova quase simultaneamente). Como `flush()`
    e atomico (LRANGE+DEL via pipeline), apenas UMA das duas tasks concorrentes
    encontra mensagens — a outra encontra lista vazia e nao chama o callback.
    """
    from app.core.debounce import DebounceManager

    redis = FakeRedis()
    key = "debounce:1006"
    await redis.rpush(key, json.dumps({"seq": 0}))
    redis.set_ttl(key, 10)  # janela expirada -> recovery agenda delay=0

    called = []

    async def capture_callback(chamado_id, messages):
        called.append((chamado_id, messages))

    mgr = DebounceManager(redis, debounce_seconds=8)

    # Dispara o flush "normal" concorrente e o flush de recovery quase juntos.
    task_normal = asyncio.create_task(mgr._delayed_flush(1006, capture_callback, delay_seconds=0))
    n = await mgr.recover_pending(capture_callback)
    assert n == 1

    await task_normal
    await asyncio.sleep(0.05)

    # Exatamente 1 chamada ao callback, com a mensagem unica — nunca 2x nem 0x.
    assert len(called) == 1
    assert len(called[0][1]) == 1


# ---------------------------------------------------------------------------
# Sem chaves pendentes -> no-op
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recovery_sem_chaves_pendentes_retorna_zero():
    from app.core.debounce import DebounceManager

    redis = FakeRedis()

    async def noop_callback(chamado_id, messages):
        pass

    mgr = DebounceManager(redis, debounce_seconds=8)
    n = await mgr.recover_pending(noop_callback)
    assert n == 0


# ---------------------------------------------------------------------------
# TTL ausente/corrompido (-2, defensivo) -> postura conservadora: flush imediato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recovery_ttl_ausente_flush_imediato_conservador():
    from app.core.debounce import DebounceManager

    redis = FakeRedis()
    key = "debounce:1005"
    await redis.rpush(key, json.dumps({"seq": 0}))
    # NAO chama set_ttl -> ttl() default do FakeRedis retorna -1 (sem expiracao
    # setada), caindo no ramo conservador (janela_restante <= 0 -> flush imediato).

    received: list[tuple[int, list[dict]]] = []

    async def capture_callback(chamado_id, messages):
        received.append((chamado_id, messages))

    mgr = DebounceManager(redis, debounce_seconds=8)
    n = await mgr.recover_pending(capture_callback)
    assert n == 1

    await asyncio.sleep(0.05)
    assert len(received) == 1
    assert received[0][0] == 1005


# ---------------------------------------------------------------------------
# Chave invalida (nao casa com o padrao debounce:{int}) -> ignorada sem erro
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recovery_chave_invalida_ignorada():
    from app.core.debounce import DebounceManager

    redis = FakeRedis()
    await redis.rpush("debounce:nao-e-um-int", json.dumps({"seq": 0}))
    redis.set_ttl("debounce:nao-e-um-int", 10)

    async def noop_callback(chamado_id, messages):
        pass

    mgr = DebounceManager(redis, debounce_seconds=8)
    n = await mgr.recover_pending(noop_callback)
    assert n == 0


# ---------------------------------------------------------------------------
# Regressao: _delayed_flush sem delay_seconds continua usando debounce_seconds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delayed_flush_default_ainda_usa_janela_normal():
    from app.core.debounce import DebounceManager

    redis = FakeRedis()
    received = []

    async def capture_callback(chamado_id, messages):
        received.append(messages)

    mgr = DebounceManager(redis, debounce_seconds=0.05)
    await mgr.push_and_schedule(2001, {"seq": 0}, capture_callback)

    # Ainda dentro da janela: nada deve ter sido processado
    await asyncio.sleep(0.01)
    assert received == []

    await asyncio.sleep(0.15)
    assert len(received) == 1
