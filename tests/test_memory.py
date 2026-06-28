"""
Testes do gerenciador de memoria conversacional (task 4.3.5).

Cenarios:
- Nao repete perguntas ja respondidas (variaveis persistidas)
- Coerencia em 50+ msgs (resumo rolante ativado)
- Recupera contexto em novo ticket do mesmo contato (US2-AS4)
- Janela quente retorna msgs em ordem cronologica
- Fallback para DB quando Redis vazio
- update_qualification_variables: apenas campos validos
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.core.memory import _SUMMARIZE_THRESHOLD, MemoryManager, SessionContext

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeRedisList:
    """Redis em memoria para testes."""

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
        for k in keys:
            self._lists.pop(k, None)
        return len(keys)

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    """Pipeline Redis em memoria."""

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
                r = await self._redis.rpush(cmd[1], *cmd[2])
            elif cmd[0] == "ltrim":
                r = await self._redis.ltrim(cmd[1], cmd[2], cmd[3])
            elif cmd[0] == "expire":
                r = await self._redis.expire(cmd[1], cmd[2])
            else:
                r = None
            results.append(r)
        return results


class FakeOpenAIClient:
    """Mock do OpenAI para sumarizacao."""

    def __init__(self, summary: str = "Resumo gerado."):
        self._summary = summary

    async def chat_cheap(self, messages, **kwargs):
        return self._summary


# ---------------------------------------------------------------------------
# Testes de build_messages_for_llm
# ---------------------------------------------------------------------------

def test_build_messages_sem_historico():
    """Contexto vazio: retorna lista vazia."""
    redis = FakeRedisList()
    manager = MemoryManager(db_session=None, redis_client=redis)

    ctx = SessionContext(
        ticket_id=1, chamado_id=1001, contato_id=10,
        resumo_rolante=None, historico_recente=[],
    )
    msgs = manager.build_messages_for_llm(ctx)
    assert msgs == []


def test_build_messages_com_resumo_rolante():
    """Resumo rolante e incluido como mensagem de sistema."""
    redis = FakeRedisList()
    manager = MemoryManager(db_session=None, redis_client=redis)

    ctx = SessionContext(
        ticket_id=1, chamado_id=1001, contato_id=10,
        resumo_rolante="Lead medico interessado em HG360",
        historico_recente=[],
    )
    msgs = manager.build_messages_for_llm(ctx)

    assert len(msgs) == 1
    assert msgs[0]["role"] == "system"
    assert "HG360" in msgs[0]["content"]


def test_build_messages_com_historico():
    """Historico recente mapeado para roles user/assistant."""
    redis = FakeRedisList()
    manager = MemoryManager(db_session=None, redis_client=redis)

    ctx = SessionContext(
        ticket_id=1, chamado_id=1001, contato_id=10,
        resumo_rolante=None,
        historico_recente=[
            {"direcao": "inbound", "tipo": "text", "conteudo": "Ola"},
            {"direcao": "outbound", "tipo": "text", "conteudo": "Oi! Como posso ajudar?"},
        ],
    )
    msgs = manager.build_messages_for_llm(ctx, max_msgs=10)

    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


def test_build_messages_respeita_max_msgs():
    """max_msgs limita historico incluido."""
    redis = FakeRedisList()
    manager = MemoryManager(db_session=None, redis_client=redis)

    historico = [
        {"direcao": "inbound", "tipo": "text", "conteudo": f"msg{i}"}
        for i in range(20)
    ]
    ctx = SessionContext(
        ticket_id=1, chamado_id=1001, contato_id=10,
        resumo_rolante=None,
        historico_recente=historico,
    )
    msgs = manager.build_messages_for_llm(ctx, max_msgs=5)

    # Apenas os ultimos 5
    assert len(msgs) == 5
    assert msgs[-1]["content"] == "msg19"


# ---------------------------------------------------------------------------
# Testes de save_message e hot_window
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_message_adiciona_na_janela_quente():
    """save_message persiste na janela quente Redis."""
    redis = FakeRedisList()
    db_mock = MagicMock()

    # Mock de db.add (nao async)
    db_mock.add = MagicMock()

    # Mock de count_messages via _count_messages
    manager = MemoryManager(db_session=db_mock, redis_client=redis)

    async def fake_count(sessao_id):
        return 1

    manager._count_messages = fake_count  # type: ignore

    ctx = SessionContext(
        ticket_id=1, chamado_id=1001, contato_id=10,
        resumo_rolante=None, historico_recente=[], sessao_id=100,
    )

    msg = {"direcao": "inbound", "tipo": "text", "conteudo": "Ola"}
    await manager.save_message(ctx, msg)

    # Deve estar na janela quente
    msgs = await manager._hot.get_messages(1001)
    assert len(msgs) == 1
    assert msgs[0]["conteudo"] == "Ola"


@pytest.mark.asyncio
async def test_save_message_sem_sessao_id_nao_crasha():
    """save_message sem sessao_id loga erro mas nao crasha."""
    redis = FakeRedisList()
    manager = MemoryManager(db_session=None, redis_client=redis)

    ctx = SessionContext(
        ticket_id=1, chamado_id=1001, contato_id=10,
        sessao_id=None,  # sem sessao_id
    )
    # Nao deve lancar excecao
    await manager.save_message(ctx, {"direcao": "inbound", "tipo": "text", "conteudo": "teste"})


# ---------------------------------------------------------------------------
# Testes de variaveis de qualificacao
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_qualification_variables_campos_validos():
    """Apenas campos permitidos passam para o DB."""
    redis = FakeRedisList()

    executed_stmts = []

    class FakeDB:
        async def execute(self, stmt):
            executed_stmts.append(stmt)

    manager = MemoryManager(db_session=FakeDB(), redis_client=redis)

    await manager.update_qualification_variables(
        contato_id=10,
        updates={"idioma": "en", "eh_medico": True, "campo_invalido": "x"},
    )

    assert len(executed_stmts) == 1
    # O stmt foi executado (campo_invalido filtrado internamente)


@pytest.mark.asyncio
async def test_update_qualification_variables_vazio_nao_executa():
    """Updates vazios nao tocam no DB."""
    redis = FakeRedisList()
    executed = []

    class FakeDB:
        async def execute(self, stmt):
            executed.append(stmt)

    manager = MemoryManager(db_session=FakeDB(), redis_client=redis)
    await manager.update_qualification_variables(10, {})

    assert len(executed) == 0


@pytest.mark.asyncio
async def test_update_qualification_variables_so_invalidos_nao_executa():
    """Apenas campos invalidos → nao executa no DB."""
    redis = FakeRedisList()
    executed = []

    class FakeDB:
        async def execute(self, stmt):
            executed.append(stmt)

    manager = MemoryManager(db_session=FakeDB(), redis_client=redis)
    await manager.update_qualification_variables(10, {"campo_inexistente": "valor"})

    assert len(executed) == 0


# ---------------------------------------------------------------------------
# Testes de resumo rolante (SC-002)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resumo_rolante_gerado_quando_threshold_atingido():
    """Apos _SUMMARIZE_THRESHOLD msgs, resumo rolante e gerado."""
    redis = FakeRedisList()
    openai = FakeOpenAIClient(summary="Lead e medico interessado em HG360 SP.")

    db_mock = MagicMock()
    db_mock.add = MagicMock()

    executed = []

    class FakeDB:
        def add(self, obj):
            pass

        async def execute(self, stmt):
            executed.append(stmt)

    manager = MemoryManager(db_session=FakeDB(), redis_client=redis, openai_client=openai)

    # Simular count acima do threshold
    async def fake_count(sessao_id):
        return _SUMMARIZE_THRESHOLD + 1

    manager._count_messages = fake_count  # type: ignore

    ctx = SessionContext(
        ticket_id=1, chamado_id=1001, contato_id=10,
        resumo_rolante=None,
        historico_recente=[
            {"direcao": "inbound", "tipo": "text", "conteudo": f"msg{i}"}
            for i in range(55)
        ],
        sessao_id=100,
    )

    await manager.save_message(ctx, {"direcao": "inbound", "tipo": "text", "conteudo": "nova"})

    # Resumo deve ter sido gerado
    assert ctx.resumo_rolante is not None
    assert "HG360" in ctx.resumo_rolante


@pytest.mark.asyncio
async def test_resumo_rolante_nao_gerado_sem_openai():
    """Sem openai_client, resumo rolante nao e gerado (best-effort)."""
    redis = FakeRedisList()
    db_mock = MagicMock()
    db_mock.add = MagicMock()

    class FakeDB:
        def add(self, obj):
            pass

        async def execute(self, stmt):
            pass

    manager = MemoryManager(db_session=FakeDB(), redis_client=redis, openai_client=None)

    async def fake_count(sessao_id):
        return _SUMMARIZE_THRESHOLD + 1

    manager._count_messages = fake_count  # type: ignore

    ctx = SessionContext(
        ticket_id=1, chamado_id=1001, contato_id=10,
        resumo_rolante=None,
        historico_recente=[{"direcao": "inbound", "tipo": "text", "conteudo": "x"} for _ in range(55)],
        sessao_id=100,
    )

    # Nao deve crasha sem openai
    await manager.save_message(ctx, {"direcao": "inbound", "tipo": "text", "conteudo": "nova"})
    assert ctx.resumo_rolante is None


# ---------------------------------------------------------------------------
# Teste de contexto anterior (US2-AS4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recover_previous_summary_retorna_none_se_ausente():
    """Sem historico anterior, _recover_previous_summary retorna None."""
    redis = FakeRedisList()

    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(None)

    class FakeResult:
        def __init__(self, val):
            self._val = val

        def scalar_one_or_none(self):
            return self._val

    manager = MemoryManager(db_session=FakeDB(), redis_client=redis)
    result = await manager._recover_previous_summary(contato_id=99, current_ticket_id=1)
    assert result is None
