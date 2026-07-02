"""
Sincronizacao idempotente de `chunk` a partir de CursoObjecao/Faq +
calculo de embeddings pendentes (RAG hibrido, Onda 3 — FR-009, FR-010,
FR-023-INFRA-IDEMP; `research.md` Decision 9; `data-model.md` §1).

Fluxo de `run_rag_seed(db_session, openai_client)`:
  1. Upsert de `chunk` (tipo='objecao'/'faq') a partir de `CursoObjecao`/`Faq`,
     chaveado por CONTEUDO — `INSERT ... ON CONFLICT (fonte_tabela, idioma,
     conteudo_hash) DO UPDATE SET fonte_id, curso_id, ativo`. Conteudo
     INALTERADO => conflito => atualiza so a proveniencia (o `fonte_id` muda a
     cada re-seed, pois o `run_seed` faz delete+insert de faq/objecao), SEM
     tocar `conteudo`/`embedding`: mesmo chunk (mesmo id e embedding) entre
     boots, sem churn nem recomputo (FR-009). Conteudo NOVO => INSERT com
     `embedding IS NULL`.
  2. Purga de orfaos por conteudo: chunks de `curso_objecao`/`faq` cujo
     `conteudo_hash` saiu do conjunto vigente da origem (fonte deletada,
     editada, ou FAQ desativada) sao REMOVIDOS — mantem a tabela limitada e a
     recuperacao ancorada so na Base Oficial vigente (anti-alucinacao). `admin`
     nao e tocado (curadoria manual; remocao so via DELETE /admin/chunks).
  3. Candidatos a embedding = TODO chunk ATIVO com `embedding IS NULL` — de
     QUALQUER fonte, inclusive `admin` (`tipo='base'` curado via `/admin/chunks`,
     que grava `embedding IS NULL` e delega o calculo a este seed) e recovery de
     boot interrompido. Conteudo inalterado ja tem embedding => nao entra aqui.
  4. Embeddings calculados em lotes de no maximo 100 textos por chamada a
     `OpenAIClient.embed()` (dec-020 finding #2, API4/LLM10 Unbounded
     Consumption, `checklists/requirements.md` CHK035) — representacao
     semantica calculada 1x, NUNCA recomputada a cada boot (FR-009).

Curadoria do TEXTO de `tipo='base'` (Decision 2) e feita via `/admin/chunks`
(app/api/admin.py, FASE 3) — upsert direto, fora deste modulo; o EMBEDDING
desses chunks e calculado aqui (passo 3).

Chamado em `app/main.py:lifespan` DEPOIS de `app.seed.run_seed`, envolto em
try/except NAO-FATAL (mesmo padrao de `app/main.py:102-118`, Decision 0):
extensao `vector`/tabela `chunk` ainda ausente (pre-swap de imagem do
Postgres) nao deve derrubar o boot do app — a responsabilidade de tolerar
essa falha fica no CALL SITE (main.py), nao aqui.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.repository.models import Chunk, CursoObjecao, Faq, chunk_conteudo_hash

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.integrations.openai_client import OpenAIClient

logger = logging.getLogger(__name__)

# Teto de textos por chamada a OpenAIClient.embed() (dec-020 finding #2).
EMBED_BATCH_SIZE = 100


async def _upsert_chunks_from_rows(
    session: "AsyncSession", rows: list[dict]
) -> None:
    """
    Upsert em lote de `chunk`s de UMA fonte (curso_objecao|faq), chaveado por
    CONTEUDO (`uq_chunk_conteudo` = fonte_tabela+idioma+conteudo_hash), NAO pelo
    `fonte_id` volatil. Conflito == mesmo conteudo => DO UPDATE apenas da
    proveniencia (`fonte_id`/`curso_id`) e `ativo`; `conteudo` e `embedding`
    NAO sao tocados (embedding preservado => sem recomputo, FR-009). Conteudo
    novo => INSERT com `embedding IS NULL` (sera embedado no passo seguinte).

    Idempotencia real entre boots: como o run_seed re-cria faq/objecao com ids
    novos, o `fonte_id` muda mas o `conteudo_hash` nao — o chunk (e seu id e
    embedding) e preservado; nada de churn.
    """
    for row in rows:
        stmt = (
            pg_insert(Chunk)
            .values(
                curso_id=row["curso_id"],
                tipo=row["tipo"],
                idioma=row["idioma"],
                conteudo=row["conteudo"],
                conteudo_hash=row["conteudo_hash"],
                fonte_tabela=row["fonte_tabela"],
                fonte_id=row["fonte_id"],
                ativo=True,
            )
            .on_conflict_do_update(
                constraint="uq_chunk_conteudo",
                set_={
                    "curso_id": row["curso_id"],
                    "fonte_id": row["fonte_id"],
                    "ativo": True,
                },
            )
        )
        await session.execute(stmt)


async def _purge_orfaos(
    session: "AsyncSession", fonte_tabela: str, hashes_vigentes: set[str]
) -> int:
    """
    Remove (DELETE) chunks de uma fonte sincronizada (`curso_objecao`/`faq`)
    cujo `conteudo_hash` nao esta mais no conjunto vigente da origem — conteudo
    que saiu da Base Oficial (fonte deletada, editada, ou FAQ desativada).
    Mantem a tabela LIMITADA (sem acumulo de mortos) e a recuperacao ancorada
    apenas na base vigente (anti-alucinacao). NAO toca `fonte_tabela='admin'`
    (curadoria manual — removida so via DELETE /admin/chunks).
    """
    stmt = delete(Chunk).where(
        Chunk.fonte_tabela == fonte_tabela,
        Chunk.conteudo_hash.notin_(sorted(hashes_vigentes)),
    )
    result = await session.execute(stmt)
    n = getattr(result, "rowcount", 0) or 0
    if n:
        logger.info("rag_seed: %d chunk(s) orfao(s) removido(s) de %s", n, fonte_tabela)
    return n


async def _pendentes_embedding(session: "AsyncSession", fonte_tabela: str) -> set[int]:
    """Ids de chunk de `fonte_tabela` sem embedding calculado (recovery de crash)."""
    result = await session.execute(
        select(Chunk.id).where(
            Chunk.fonte_tabela == fonte_tabela, Chunk.embedding.is_(None)
        )
    )
    return {row_id for (row_id,) in result.all()}


async def _pendentes_embedding_todos(session: "AsyncSession") -> set[int]:
    """
    Ids de TODO chunk ATIVO ainda sem embedding — qualquer `fonte_tabela`,
    inclusive `admin` (curadoria de `tipo='base'` via `/admin/chunks`, que
    grava `embedding IS NULL` e delega o calculo a este seed). Sem isto, os
    chunks `base` nunca entrariam na perna VETORIAL da recuperacao (so na
    lexical), tornando a curadoria semanticamente invisivel.
    """
    result = await session.execute(
        select(Chunk.id).where(
            Chunk.embedding.is_(None), Chunk.ativo.is_(True)
        )
    )
    return {row_id for (row_id,) in result.all()}


async def _embed_pendentes(
    session: "AsyncSession", openai_client: "OpenAIClient", ids: list[int]
) -> int:
    """
    Calcula e persiste embeddings dos `ids` informados, em lotes de no
    maximo EMBED_BATCH_SIZE textos por chamada a `openai_client.embed()`
    (dec-020 finding #2). Retorna a quantidade de chunks embedados.
    """
    if not ids:
        return 0
    result = await session.execute(
        select(Chunk.id, Chunk.conteudo).where(Chunk.id.in_(ids))
    )
    linhas = result.all()
    total = 0
    for i in range(0, len(linhas), EMBED_BATCH_SIZE):
        lote = linhas[i : i + EMBED_BATCH_SIZE]
        textos = [conteudo for _, conteudo in lote]
        vetores = await openai_client.embed(textos)
        for (chunk_id, _conteudo), vetor in zip(lote, vetores):
            await session.execute(
                update(Chunk).where(Chunk.id == chunk_id).values(embedding=vetor)
            )
        total += len(lote)
    return total


def _objecao_conteudo(objecao: str, resposta: str) -> str:
    """Texto oficial verbatim do chunk de objecao: objecao + resposta (1 unidade)."""
    return f"{objecao}\n\n{resposta}"


def _faq_conteudo(pergunta: str, resposta: str) -> str:
    """Texto oficial verbatim do chunk de FAQ: pergunta + resposta (1 unidade)."""
    return f"{pergunta}\n\n{resposta}"


async def run_rag_seed(
    session: "AsyncSession", openai_client: Optional["OpenAIClient"]
) -> None:
    """
    Sincroniza `chunk` a partir de `CursoObjecao`/`Faq` e calcula
    embeddings pendentes. Reusa os parsers/estrutura ja curados por
    `app/seed.py` (a fonte-da-verdade continua sendo `curso_objecao`/`faq`
    via API de admin existente — `chunk` e um indice derivado/reconstruivel,
    research.md Decision 9).

    `openai_client=None` (ex.: OPENAI_API_KEY ausente em ambiente de teste)
    sincroniza apenas o TEXTO dos chunks; embeddings ficam pendentes
    (`embedding IS NULL`) ate uma proxima execucao com client disponivel.
    """
    logger.info("rag_seed: iniciando sincronizacao de chunks...")

    def _row(fonte_tabela, tipo, idioma, curso_id, fonte_id, conteudo):
        return {
            "curso_id": curso_id,
            "tipo": tipo,
            "idioma": idioma,
            "conteudo": conteudo,
            "conteudo_hash": chunk_conteudo_hash(conteudo),
            "fonte_tabela": fonte_tabela,
            "fonte_id": fonte_id,
        }

    objecoes = (await session.execute(select(CursoObjecao))).scalars().all()
    objecao_rows = [
        _row("curso_objecao", "objecao", o.idioma, o.curso_id, o.id,
             _objecao_conteudo(o.objecao, o.resposta))
        for o in objecoes
    ]
    await _upsert_chunks_from_rows(session, objecao_rows)

    faqs = (
        await session.execute(select(Faq).where(Faq.ativo.is_(True)))
    ).scalars().all()
    faq_rows = [
        _row("faq", "faq", f.idioma, None, f.id, _faq_conteudo(f.pergunta, f.resposta))
        for f in faqs
    ]
    await _upsert_chunks_from_rows(session, faq_rows)

    # Purga de orfaos por CONTEUDO: conteudo que saiu da Base Oficial (fonte
    # deletada/editada, FAQ desativada) e removido — mantem a tabela limitada e
    # a recuperacao ancorada so na base vigente (anti-alucinacao). NAO toca
    # `admin`/base. Roda tambem no caminho sem openai_client.
    await _purge_orfaos(
        session, "curso_objecao", {r["conteudo_hash"] for r in objecao_rows}
    )
    await _purge_orfaos(session, "faq", {r["conteudo_hash"] for r in faq_rows})

    logger.info(
        "rag_seed: sincronizacao de texto concluida (objecao=%d, faq=%d)",
        len(objecao_rows),
        len(faq_rows),
    )

    if openai_client is None:
        logger.warning(
            "rag_seed: openai_client indisponivel — sincronizando so texto, "
            "embeddings ficam pendentes"
        )
        await session.commit()
        return

    # Candidatos a embedding: todo chunk ATIVO com `embedding IS NULL` — apenas
    # conteudo NOVO (upsert por conteudo preserva o embedding do inalterado),
    # `admin`/base curados e recovery de boot interrompido. Conteudo inalterado
    # entre boots => nenhum embedding recomputado (FR-009).
    pendentes = await _pendentes_embedding_todos(session)

    if pendentes:
        n = await _embed_pendentes(session, openai_client, sorted(pendentes))
        logger.info("rag_seed: %d chunk(s) embedado(s)", n)
    else:
        logger.info("rag_seed: nenhum chunk pendente de embedding")

    await session.commit()
    logger.info("rag_seed: sincronizacao concluida")
