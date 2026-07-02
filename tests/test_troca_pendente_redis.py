"""
Testes de escrita/leitura/limpeza do campo Redis `troca_pendente` (US1,
FASE 2, task 2.1.5, contracts/estado-troca-pendente.md).

Cobertura:
- 2.1.3: leitura fail-open (HGET ausente, JSON corrompido, Redis fora do
  ar) -> None ("sem pergunta pendente"), nunca bloqueia o turno.
- 2.1.4: limpeza explicita via HDEL (persist com valor None/vazio).
- Escrita/leitura cobrindo os 2 tipos: `confirmacao` (1 destino) e
  `desambiguacao` (exatamente 2 destinos).

Abordagem: Redis em memoria (hash) fake, sem Redis real — mesmo padrao de
`tests/test_turnos_contadores.py::FakeHashRedis`.
"""
from __future__ import annotations

from typing import Any, Optional
from unittest.mock import patch

import pytest


class FakeHashRedis:
    """Redis em memoria minimalista: hget/hset/hdel — decode_responses=False
    (bytes), fiel ao cliente real (app/main.py)."""

    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, bytes]] = {}

    async def hget(self, key: str, field: str) -> Optional[bytes]:
        return self._hashes.get(key, {}).get(field)

    async def hset(self, key: str, field: str, value: Any) -> int:
        h = self._hashes.setdefault(key, {})
        is_new = field not in h
        h[field] = str(value).encode("utf-8")
        return 1 if is_new else 0

    async def hdel(self, key: str, *fields: str) -> int:
        h = self._hashes.get(key, {})
        removed = 0
        for f in fields:
            if f in h:
                del h[f]
                removed += 1
        return removed


class BrokenRedis:
    """Redis que sempre falha (simula Redis reiniciado/indisponivel)."""

    async def hget(self, *a, **kw):
        raise ConnectionError("redis indisponivel")

    async def hset(self, *a, **kw):
        raise ConnectionError("redis indisponivel")

    async def hdel(self, *a, **kw):
        raise ConnectionError("redis indisponivel")


# ---------------------------------------------------------------------------
# Escrita + leitura — tipo "confirmacao" (1 destino)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_e_load_confirmacao_1_destino():
    from app.api.webhook import _load_troca_pendente, _persist_troca_pendente

    fake = FakeHashRedis()
    valor = {
        "destinos": [3], "origem": 1, "metodo": "deterministico",
        "confianca": None, "tipo": "confirmacao",
    }
    with patch("app.api.webhook._get_redis", return_value=fake):
        await _persist_troca_pendente(chamado_id=1, valor=valor)
        lido = await _load_troca_pendente(chamado_id=1)

    assert lido == valor
    assert lido["tipo"] == "confirmacao"
    assert len(lido["destinos"]) == 1


# ---------------------------------------------------------------------------
# Escrita + leitura — tipo "desambiguacao" (exatamente 2 destinos)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_e_load_desambiguacao_2_destinos():
    from app.api.webhook import _load_troca_pendente, _persist_troca_pendente

    fake = FakeHashRedis()
    valor = {
        "destinos": [1, 2], "origem": 3, "metodo": "deterministico",
        "confianca": None, "tipo": "desambiguacao",
    }
    with patch("app.api.webhook._get_redis", return_value=fake):
        await _persist_troca_pendente(chamado_id=42, valor=valor)
        lido = await _load_troca_pendente(chamado_id=42)

    assert lido == valor
    assert lido["tipo"] == "desambiguacao"
    assert len(lido["destinos"]) == 2


@pytest.mark.asyncio
async def test_persist_e_por_chamado_id_isolado():
    from app.api.webhook import _load_troca_pendente, _persist_troca_pendente

    fake = FakeHashRedis()
    valor_a = {"destinos": [1], "origem": 2, "metodo": "deterministico",
               "confianca": None, "tipo": "confirmacao"}
    valor_b = {"destinos": [3, 4], "origem": 5, "metodo": "assistido",
               "confianca": 0.7, "tipo": "desambiguacao"}
    with patch("app.api.webhook._get_redis", return_value=fake):
        await _persist_troca_pendente(chamado_id=100, valor=valor_a)
        await _persist_troca_pendente(chamado_id=200, valor=valor_b)
        lido_a = await _load_troca_pendente(chamado_id=100)
        lido_b = await _load_troca_pendente(chamado_id=200)

    assert lido_a == valor_a
    assert lido_b == valor_b


# ---------------------------------------------------------------------------
# Limpeza explicita (P-3, task 2.1.4) — HDEL tanto no sucesso quanto na falha
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_com_none_limpa_via_hdel():
    from app.api.webhook import _load_troca_pendente, _persist_troca_pendente

    fake = FakeHashRedis()
    valor = {"destinos": [3], "origem": 1, "metodo": "deterministico",
             "confianca": None, "tipo": "confirmacao"}
    with patch("app.api.webhook._get_redis", return_value=fake):
        await _persist_troca_pendente(chamado_id=1, valor=valor)
        assert await _load_troca_pendente(chamado_id=1) is not None

        # Caminho de sucesso (confirmado) OU de falha (negado/nao reconhecido)
        # -- ambos limpam da MESMA forma: persist(None).
        await _persist_troca_pendente(chamado_id=1, valor=None)
        lido = await _load_troca_pendente(chamado_id=1)

    assert lido is None
    assert redis_keys_field_absent(fake, 1)


def redis_keys_field_absent(fake: FakeHashRedis, chamado_id: int) -> bool:
    from app.core import redis_keys

    key = redis_keys.estado_key(chamado_id)
    return redis_keys.TROCA_PENDENTE_FIELD not in fake._hashes.get(key, {})


@pytest.mark.asyncio
async def test_persist_com_dict_vazio_tambem_limpa():
    """Dict vazio e falsy -- mesmo tratamento de None (limpeza, nao grava '{}')."""
    from app.api.webhook import _load_troca_pendente, _persist_troca_pendente

    fake = FakeHashRedis()
    with patch("app.api.webhook._get_redis", return_value=fake):
        await _persist_troca_pendente(chamado_id=7, valor={"destinos": [1]})
        await _persist_troca_pendente(chamado_id=7, valor={})
        lido = await _load_troca_pendente(chamado_id=7)

    assert lido is None


# ---------------------------------------------------------------------------
# Leitura fail-open (P-4, task 2.1.3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_hget_ausente_retorna_none():
    from app.api.webhook import _load_troca_pendente

    fake = FakeHashRedis()
    with patch("app.api.webhook._get_redis", return_value=fake):
        lido = await _load_troca_pendente(chamado_id=999)

    assert lido is None


@pytest.mark.asyncio
async def test_load_json_corrompido_fail_open():
    from app.api.webhook import _load_troca_pendente
    from app.core import redis_keys

    fake = FakeHashRedis()
    key = redis_keys.estado_key(1)
    fake._hashes[key] = {redis_keys.TROCA_PENDENTE_FIELD: b"{nao e json valido"}
    with patch("app.api.webhook._get_redis", return_value=fake):
        lido = await _load_troca_pendente(chamado_id=1)

    assert lido is None


@pytest.mark.asyncio
async def test_load_json_nao_dict_fail_open():
    """JSON valido mas nao-dict (ex.: lista/numero) -- tratado como corrompido."""
    from app.api.webhook import _load_troca_pendente
    from app.core import redis_keys

    fake = FakeHashRedis()
    key = redis_keys.estado_key(1)
    fake._hashes[key] = {redis_keys.TROCA_PENDENTE_FIELD: b"[1, 2, 3]"}
    with patch("app.api.webhook._get_redis", return_value=fake):
        lido = await _load_troca_pendente(chamado_id=1)

    assert lido is None


@pytest.mark.asyncio
async def test_load_redis_indisponivel_fail_open():
    from app.api.webhook import _load_troca_pendente

    with patch("app.api.webhook._get_redis", return_value=BrokenRedis()):
        lido = await _load_troca_pendente(chamado_id=1)

    assert lido is None


@pytest.mark.asyncio
async def test_persist_redis_indisponivel_fail_open_nao_levanta():
    """Fail-open tambem na escrita: erro de Redis nunca gateia o turno."""
    from app.api.webhook import _persist_troca_pendente

    with patch("app.api.webhook._get_redis", return_value=BrokenRedis()):
        # Nao deve levantar excecao.
        await _persist_troca_pendente(chamado_id=1, valor={"destinos": [1]})
