"""
Testes do comando de reset de jornada (#reset).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import Settings
from app.core.reset import confirmacao_reset, is_numero_teste, reset_conversa


def test_confirmacao_reset_idiomas():
    assert "reiniciada" in confirmacao_reset("pt").lower()
    assert "reset" in confirmacao_reset("en").lower()
    assert "reiniciada" in confirmacao_reset("es").lower()
    # idioma desconhecido cai em pt
    assert confirmacao_reset("xx") == confirmacao_reset("pt")


def test_reset_test_numbers_list_parse():
    s = Settings(
        database_url="postgresql+asyncpg://x@y/z",
        reset_test_numbers="5511967296849, 5511941410998 ,",
    )
    assert s.reset_test_numbers_list == ["5511967296849", "5511941410998"]


@pytest.mark.asyncio
async def test_is_numero_teste_true_e_false():
    # presente
    sess = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = 1
    sess.execute.return_value = res
    assert await is_numero_teste(sess, "5511967296849") is True

    # ausente
    res.scalar_one_or_none.return_value = None
    assert await is_numero_teste(sess, "5511000000000") is False

    # numero vazio -> False sem consultar
    assert await is_numero_teste(sess, "") is False


@pytest.mark.asyncio
async def test_reset_conversa_limpa_redis_sem_session():
    """Sem session (DB indisponivel) ainda limpa Redis (best-effort)."""
    redis = AsyncMock()
    redis.scan.return_value = (0, [])
    ok = await reset_conversa(session=None, redis=redis, chamado_id=138901)
    assert ok is True
    # deletou as 4 chaves de sessao/ticket
    redis.delete.assert_awaited()
    args = redis.delete.await_args_list[0].args
    assert any("sessao:138901:hot" == a for a in args)
    assert any("debounce:138901" == a for a in args)
    assert any("lock:ticket:138901" == a for a in args)
    assert any("estado:138901" == a for a in args)
    # tentou limpar idempotencia via scan
    redis.scan.assert_awaited()
