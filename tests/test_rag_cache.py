"""
Testes do cache semantico opcional do HybridRetriever (Onda 3, FASE 8,
FR-019 — SHOULD; task 8.1.2).

Cobre:
- Desligado por padrao (`cache_enabled=False`): comportamento IDENTICO ao
  pre-FASE8 — nenhuma chamada ao Redis, resultado sempre recomputado.
- Ligado: 2a consulta identica (mesma query normalizada + curso_id + idioma)
  reaproveita o resultado sem tocar o repositorio/OpenAI de novo.
- Falha de Redis (GET/SET) e sempre tratada como cache MISS — NUNCA vira
  abstencao (a busca completa prossegue normalmente).
"""
from __future__ import annotations

from typing import Optional
from unittest.mock import AsyncMock

import pytest

from app.core.retrieval import ChunkCandidato, HybridRetriever


class FakeChunkRepository:
    """Fake do `ChunkRepository` (Protocol) que CONTA quantas vezes foi
    chamado — usado para provar que o cache HIT evita a busca completa."""

    def __init__(self, vetorial: Optional[list[ChunkCandidato]] = None) -> None:
        self._vetorial = vetorial or []
        self.chamadas_vetorial = 0
        self.chamadas_textual = 0

    async def buscar_vetorial(self, query_embedding, curso_id, idioma, k):
        self.chamadas_vetorial += 1
        return [
            c for c in self._vetorial
            if (c.curso_id == curso_id or c.curso_id is None) and c.idioma == idioma
        ][:k]

    async def buscar_textual(self, query, curso_id, idioma, k):
        self.chamadas_textual += 1
        return []


class FakeRedis:
    """Fake minimalista de cliente Redis async (get/set), em memoria."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> Optional[str]:
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int = 0) -> None:
        self._store[key] = value


class BrokenRedis:
    """Fake que sempre falha (Redis indisponivel) — falha de cache NUNCA
    pode virar abstencao."""

    async def get(self, key: str):
        raise ConnectionError("redis indisponivel")

    async def set(self, key: str, value: str, ex: int = 0):
        raise ConnectionError("redis indisponivel")


def _make_openai_mock() -> AsyncMock:
    client = AsyncMock()
    client.embed = AsyncMock(return_value=[[0.1] * 1536])
    return client


@pytest.mark.asyncio
async def test_cache_desligado_por_padrao_nao_toca_redis():
    """`cache_enabled=False` (default) — comportamento IDENTICO ao
    pre-FASE8: repositorio chamado em toda consulta, Redis nunca tocado."""
    repo = FakeChunkRepository(
        vetorial=[
            ChunkCandidato(
                chunk_id=1, conteudo="Objecao.", tipo="objecao",
                curso_id=1, idioma="pt", distancia_cosseno=0.05,
            )
        ]
    )
    redis = FakeRedis()
    retriever = HybridRetriever(
        chunk_repository=repo, openai_client=_make_openai_mock(),
        redis_client=redis, cache_enabled=False,
    )

    await retriever.buscar("qual o preco?", 1, "pt")
    await retriever.buscar("qual o preco?", 1, "pt")

    assert repo.chamadas_vetorial == 2  # SEM cache: recomputa sempre
    assert redis._store == {}


@pytest.mark.asyncio
async def test_cache_ligado_reaproveita_consulta_identica():
    """Ligado: 2a consulta identica (mesma query normalizada + curso_id +
    idioma) reaproveita o resultado do cache — repositorio chamado 1x so."""
    repo = FakeChunkRepository(
        vetorial=[
            ChunkCandidato(
                chunk_id=9, conteudo="Parcelamos em 12x.", tipo="objecao",
                curso_id=1, idioma="pt", distancia_cosseno=0.05,
            )
        ]
    )
    redis = FakeRedis()
    retriever = HybridRetriever(
        chunk_repository=repo, openai_client=_make_openai_mock(),
        redis_client=redis, cache_enabled=True,
    )

    r1 = await retriever.buscar("  Tem PARCELAMENTO?  ", 1, "pt")
    r2 = await retriever.buscar("tem parcelamento?", 1, "pt")  # normalizado igual

    assert repo.chamadas_vetorial == 1  # 2a veio do cache
    assert r1.abster == r2.abster
    assert [c.chunk_id for c in r1.chunks] == [c.chunk_id for c in r2.chunks]


@pytest.mark.asyncio
async def test_cache_ligado_nao_reaproveita_consulta_diferente_ou_outro_curso():
    """Chave do cache inclui curso_id/idioma — consulta para outro curso
    (ou idioma) NAO reaproveita o resultado (sem vazamento entre produtos)."""
    repo = FakeChunkRepository(
        vetorial=[
            ChunkCandidato(
                chunk_id=1, conteudo="X.", tipo="objecao",
                curso_id=None, idioma="pt", distancia_cosseno=0.05,
            )
        ]
    )
    redis = FakeRedis()
    retriever = HybridRetriever(
        chunk_repository=repo, openai_client=_make_openai_mock(),
        redis_client=redis, cache_enabled=True,
    )

    await retriever.buscar("tem parcelamento?", 1, "pt")
    await retriever.buscar("tem parcelamento?", 2, "pt")  # outro curso_id

    assert repo.chamadas_vetorial == 2


@pytest.mark.asyncio
async def test_cache_get_falho_e_tratado_como_miss_nunca_abstencao():
    """Falha no GET do Redis (indisponivel) NUNCA vira abstencao — a busca
    completa prossegue normalmente (fail-open do cache, fail-closed so no
    mecanismo principal)."""
    repo = FakeChunkRepository(
        vetorial=[
            ChunkCandidato(
                chunk_id=5, conteudo="Y.", tipo="faq",
                curso_id=1, idioma="pt", distancia_cosseno=0.05,
            )
        ]
    )
    retriever = HybridRetriever(
        chunk_repository=repo, openai_client=_make_openai_mock(),
        redis_client=BrokenRedis(), cache_enabled=True,
    )

    resultado = await retriever.buscar("duvida qualquer", 1, "pt")

    assert resultado.abster is False
    assert repo.chamadas_vetorial == 1


@pytest.mark.asyncio
async def test_cache_set_falho_nao_afeta_resultado_retornado():
    """Falha no SET do Redis (indisponivel) e ignorada — o resultado ja
    computado e retornado normalmente ao chamador."""
    repo = FakeChunkRepository(
        vetorial=[
            ChunkCandidato(
                chunk_id=8, conteudo="Z.", tipo="faq",
                curso_id=1, idioma="pt", distancia_cosseno=0.05,
            )
        ]
    )
    retriever = HybridRetriever(
        chunk_repository=repo, openai_client=_make_openai_mock(),
        redis_client=BrokenRedis(), cache_enabled=True,
    )

    resultado = await retriever.buscar("duvida qualquer", 1, "pt")

    assert resultado.abster is False
    assert [c.chunk_id for c in resultado.chunks] == [8]


@pytest.mark.asyncio
async def test_cache_hit_com_payload_corrompido_e_tratado_como_miss():
    """Payload corrompido no Redis (JSON invalido) e tratado como MISS —
    nunca propaga excecao, a busca completa prossegue."""
    repo = FakeChunkRepository(
        vetorial=[
            ChunkCandidato(
                chunk_id=3, conteudo="W.", tipo="faq",
                curso_id=1, idioma="pt", distancia_cosseno=0.05,
            )
        ]
    )
    redis = FakeRedis()
    retriever = HybridRetriever(
        chunk_repository=repo, openai_client=_make_openai_mock(),
        redis_client=redis, cache_enabled=True,
    )
    # Popular o cache com JSON invalido diretamente (simula corrupcao).
    from app.core.retrieval import _cache_key

    key = _cache_key("duvida qualquer", 1, "pt")
    redis._store[key] = "{nao-e-json-valido"

    resultado = await retriever.buscar("duvida qualquer", 1, "pt")

    assert resultado.abster is False
    assert repo.chamadas_vetorial == 1  # nao usou o cache corrompido


@pytest.mark.asyncio
async def test_cache_serializa_e_desserializa_resultado_de_abstencao():
    """Resultados de ABSTENCAO tambem sao cacheaveis (evita reprocessar a
    mesma consulta sem fonte suficiente repetidamente)."""
    repo = FakeChunkRepository(vetorial=[])  # sem candidatos -> abster=True
    redis = FakeRedis()
    retriever = HybridRetriever(
        chunk_repository=repo, openai_client=_make_openai_mock(),
        redis_client=redis, cache_enabled=True,
    )

    r1 = await retriever.buscar("pergunta fora da base", 1, "pt")
    r2 = await retriever.buscar("pergunta fora da base", 1, "pt")

    assert r1.abster is True
    assert r2.abster is True
    assert r2.motivo_abstencao == r1.motivo_abstencao
    assert repo.chamadas_vetorial == 1


def test_cache_key_normaliza_espacos_e_case():
    from app.core.retrieval import _cache_key

    assert _cache_key("  Tem Parcelamento?  ", 1, "pt") == _cache_key(
        "tem   parcelamento?", 1, "pt"
    )
    assert _cache_key("x", 1, "pt") != _cache_key("x", 2, "pt")
    assert _cache_key("x", 1, "pt") != _cache_key("x", 1, "en")
