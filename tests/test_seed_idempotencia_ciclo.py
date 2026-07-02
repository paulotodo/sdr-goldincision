"""
Teste de IDEMPOTENCIA REAL de ciclo-duplo do `run_rag_seed` contra um Postgres
de verdade (com pgvector) — prova o fix do "seed-churn" que os testes mockados
NAO pegavam: o ON CONFLICT (fonte_tabela, idioma, conteudo_hash) so vale com o
motor real.

Cenario simulado: o `run_seed` faz delete+insert de faq a cada boot, gerando
`fonte_id`s seriais NOVOS. Este teste roda `run_rag_seed` DUAS vezes com a fonte
"churnada" (mesmos textos, ids novos) entre os ciclos e verifica:
  - a contagem de chunks ATIVOS nao cresce (sem duplicacao);
  - conteudo inalterado NAO e re-embedado (contagem de itens embedados no 2o
    ciclo == 0);
  - o chunk.id do conteudo inalterado e PRESERVADO (mesmo embedding);
  - editar um FAQ substitui so aquele chunk (purga do orfao + 1 embedding novo).

Guardado por TEST_DATABASE_URL (asyncpg). Sem ela, e SKIPPED — o gate padrao de
CI (sem Postgres/pgvector) continua verde; rode localmente com, ex.:
  TEST_DATABASE_URL=postgresql+asyncpg://sdr:sdr@127.0.0.1:5544/sdr \
    pytest tests/test_seed_idempotencia_ciclo.py -q
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.rag_seed import run_rag_seed
from app.repository.models import Base, Chunk, Faq

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL nao definido (Postgres+pgvector real)"
)


def _fake_openai():
    """Client cujo embed() conta itens embedados e devolve vetores dummy 1536-d."""
    oc = MagicMock()
    contador = {"itens": 0}

    async def _embed(textos):
        contador["itens"] += len(textos)
        return [[0.0] * 1536 for _ in textos]

    oc.embed = AsyncMock(side_effect=_embed)
    oc._contador = contador
    return oc


async def _reseed_faqs(session, pares):
    """Simula o run_seed: DELETE + re-INSERT de faq (ids seriais NOVOS)."""
    await session.execute(delete(Faq))
    for secao, pergunta, resposta in pares:
        session.add(Faq(idioma="pt", secao=secao, pergunta=pergunta, resposta=resposta, ativo=True))
    await session.commit()


async def _chunk_stats(session):
    total = (await session.execute(select(func.count()).select_from(Chunk))).scalar_one()
    ativos = (
        await session.execute(
            select(func.count()).select_from(Chunk).where(Chunk.ativo.is_(True))
        )
    ).scalar_one()
    com_emb = (
        await session.execute(
            select(func.count()).select_from(Chunk).where(Chunk.embedding.is_not(None))
        )
    ).scalar_one()
    return total, ativos, com_emb


@pytest.mark.asyncio
async def test_ciclo_duplo_idempotente_sem_churn():
    engine = create_async_engine(TEST_DB)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    faqs = [("s", "Como funciona?", "Assim."), ("s", "Qual o valor?", "Sob consulta.")]
    try:
        # ---- ciclo 1: seed inicial ----
        async with Session() as s:
            await _reseed_faqs(s, faqs)
        oc1 = _fake_openai()
        async with Session() as s:
            await run_rag_seed(s, oc1)
        async with Session() as s:
            total, ativos, com_emb = await _chunk_stats(s)
            ids_ciclo1 = {
                c.conteudo_hash: c.id
                for c in (await s.execute(select(Chunk))).scalars().all()
            }
        assert (total, ativos, com_emb) == (2, 2, 2)
        assert oc1._contador["itens"] == 2  # 2 chunks novos embedados

        # ---- churn: run_seed re-cria os MESMOS faqs com ids novos ----
        async with Session() as s:
            await _reseed_faqs(s, faqs)

        # ---- ciclo 2: conteudo inalterado ----
        oc2 = _fake_openai()
        async with Session() as s:
            await run_rag_seed(s, oc2)
        async with Session() as s:
            total, ativos, com_emb = await _chunk_stats(s)
            ids_ciclo2 = {
                c.conteudo_hash: c.id
                for c in (await s.execute(select(Chunk))).scalars().all()
            }
        assert (total, ativos, com_emb) == (2, 2, 2), "chunk duplicou/cresceu no 2o ciclo"
        assert oc2._contador["itens"] == 0, "conteudo inalterado foi re-embedado (churn)"
        assert ids_ciclo1 == ids_ciclo2, "chunk.id do conteudo inalterado nao foi preservado"

        # ---- ciclo 3: editar 1 FAQ -> so aquele chunk e substituido ----
        faqs_editado = [("s", "Como funciona?", "Assim, em detalhe."), ("s", "Qual o valor?", "Sob consulta.")]
        async with Session() as s:
            await _reseed_faqs(s, faqs_editado)
        oc3 = _fake_openai()
        async with Session() as s:
            await run_rag_seed(s, oc3)
        async with Session() as s:
            total, ativos, com_emb = await _chunk_stats(s)
        assert (total, ativos, com_emb) == (2, 2, 2), "editar 1 FAQ nao manteve 2 chunks"
        assert oc3._contador["itens"] == 1, "so o FAQ editado deveria re-embedar"
    finally:
        await engine.dispose()
