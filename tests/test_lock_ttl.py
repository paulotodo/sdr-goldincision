"""
Testes de robustez do lock de exclusividade (US4, FASE 6, task 6.1).

Cobertura:
- 6.1.1: `TicketLock` usa `settings.lock_ttl_ms` (default 90_000) como TTL
  efetivo do `SET NX PX`, nao mais o antigo default fixo de 30_000.
- Override explicito de `lock_ttl_ms` no construtor tem precedencia sobre
  `settings` (flexibilidade para testes/cenarios especiais).
- 6.1.4/SC-006: simula turno de duracao proxima ao pior caso conhecido
  (LLM + ate 4 envios paced + retries, ~30-50s, com folga ate ~85s) e
  confirma que o lock permanece VALIDO (nao expira sozinho) ate o fim do
  processamento com o TTL elevado — contrastado com o TTL antigo (30s),
  que expiraria no mesmo cenario (motivo da elevacao — research.md
  Decision 4).

Abordagem: FakeExpiringRedis com relogio simulado controlavel pelo teste
(sem `asyncio.sleep` real nem Redis real).
"""
from __future__ import annotations

from typing import Any, Optional

import pytest


class FakeExpiringRedis:
    """
    Redis fake com TTL/expiracao real, controlada por um relogio simulado
    (`now_ms_fn`) — permite testar "o lock ainda esta valido apos N ms de
    processamento" sem `asyncio.sleep` real.
    """

    def __init__(self, now_ms_fn):
        self._data: dict[str, tuple[Any, Optional[int]]] = {}
        self._now_ms = now_ms_fn
        self.last_set_px: Optional[int] = None

    def _is_expired(self, key: str) -> bool:
        entry = self._data.get(key)
        if entry is None:
            return True
        _, expire_at = entry
        if expire_at is not None and self._now_ms() >= expire_at:
            del self._data[key]
            return True
        return False

    async def set(self, key: str, value: Any, nx: bool = False, ex=None, px=None):
        if nx and not self._is_expired(key):
            return None
        self.last_set_px = px
        expire_at: Optional[int] = None
        if px is not None:
            expire_at = self._now_ms() + int(px)
        elif ex is not None:
            expire_at = self._now_ms() + int(ex) * 1000
        self._data[key] = (value, expire_at)
        return True

    async def get(self, key: str):
        if self._is_expired(key):
            return None
        return self._data[key][0]

    async def exists(self, key: str) -> int:
        return 0 if self._is_expired(key) else 1

    async def eval(self, script: str, numkeys: int, *args) -> int:
        """Simula o Lua script de release: DEL somente se o token bate."""
        key, token = args[0], args[1]
        if self._is_expired(key):
            return 0
        stored_value, _ = self._data[key]
        stored_str = stored_value.decode() if isinstance(stored_value, bytes) else stored_value
        token_str = token.decode() if isinstance(token, bytes) else token
        if stored_str == token_str:
            del self._data[key]
            return 1
        return 0


# ---------------------------------------------------------------------------
# 6.1.1 — TTL efetivo vem de settings.lock_ttl_ms (90s default)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lock_default_ttl_usa_settings_lock_ttl_ms():
    from app.config import settings
    from app.core.locks import TicketLock

    assert settings.lock_ttl_ms == 90_000  # confirmacao empirica do default (1.1.3/6.1.1)

    redis = FakeExpiringRedis(lambda: 0)
    lock = TicketLock(redis)

    async with lock.acquire(1) as acquired:
        assert acquired is True

    assert redis.last_set_px == 90_000


@pytest.mark.asyncio
async def test_lock_ttl_explicito_sobrescreve_settings():
    from app.core.locks import TicketLock

    redis = FakeExpiringRedis(lambda: 0)
    lock = TicketLock(redis, lock_ttl_ms=12_345)

    async with lock.acquire(2) as acquired:
        assert acquired is True

    assert redis.last_set_px == 12_345


# ---------------------------------------------------------------------------
# 6.1.4/SC-006 — lock permanece valido ate o fim do pior caso de turno
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lock_permanece_valido_ate_o_fim_do_pior_caso_de_turno():
    """
    Simula turno de duracao proxima ao pior caso conhecido (LLM +
    ate 4 envios paced + retries — research.md Decision 4 estima
    ~30-50s). Com o TTL elevado (90s, settings.lock_ttl_ms via default),
    o lock permanece valido (nao expira sozinho) ate o processamento
    terminar e ser liberado explicitamente (Acceptance Scenario 1, US4).
    """
    from app.core.locks import TicketLock

    clock = [0]
    redis = FakeExpiringRedis(lambda: clock[0])
    lock = TicketLock(redis)  # usa settings.lock_ttl_ms (90_000 default)

    async with lock.acquire(4242) as acquired:
        assert acquired is True
        # Avanca o relogio simulando ~85s de processamento -- ainda dentro
        # do TTL de 90s (pior caso conhecido tem folga ate ~85-90s).
        clock[0] += 85_000
        assert await lock.is_locked(4242) is True  # NAO expirou sozinho

    # Apos o `async with`, o release explicito ja rodou -- sucesso, pois o
    # token ainda era o dono (a chave nao havia expirado sozinha).
    assert await lock.is_locked(4242) is False


@pytest.mark.asyncio
async def test_lock_ttl_antigo_30s_teria_expirado_no_mesmo_cenario():
    """
    Contraste regressivo: com o TTL PRE-FASE 6 (30s, explicito aqui para
    nao depender do default corrente de settings), o MESMO cenario de
    ~85s de processamento teria o lock expirado ANTES do fim do turno --
    risco real de reprocessamento concorrente (G4) -- motivo da elevacao
    documentada em research.md Decision 4.
    """
    from app.core.locks import TicketLock

    clock = [0]
    redis = FakeExpiringRedis(lambda: clock[0])
    lock = TicketLock(redis, lock_ttl_ms=30_000)  # TTL antigo, explicito

    async with lock.acquire(4243) as acquired:
        assert acquired is True
        clock[0] += 85_000  # mesmo cenario de duracao do teste acima
        assert await lock.is_locked(4243) is False  # EXPIROU (30s < 85s)


@pytest.mark.asyncio
async def test_lock_expira_permite_reaquisicao_por_outro_processador():
    """Uma vez expirado (TTL curto), outro processador PODE adquirir o
    mesmo lock -- comportamento esperado do TTL (nao e um bug)."""
    from app.core.locks import TicketLock

    clock = [0]
    redis = FakeExpiringRedis(lambda: clock[0])
    lock = TicketLock(redis, lock_ttl_ms=1_000)

    async with lock.acquire(4244) as acquired1:
        assert acquired1 is True
        clock[0] += 2_000  # ultrapassa o TTL de 1s

        # Segundo processador tenta durante o "vazamento" pos-expiracao —
        # como o TTL ja passou, o SET NX deve suceder (chave livre).
        async with lock.acquire(4244) as acquired2:
            assert acquired2 is True
