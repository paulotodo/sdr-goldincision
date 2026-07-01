"""
Sincronizacao idempotente de `chunk` a partir de CursoObjecao/Faq +
calculo de embeddings pendentes (RAG hibrido, Onda 3 — FR-009, FR-010,
FR-023-INFRA-IDEMP; `research.md` Decision 9; `data-model.md` §1).

Fluxo de `run_rag_seed(db_session, openai_client)`:
  1. Upsert de `chunk` (tipo='objecao'/'faq') a partir de `CursoObjecao`/
     `Faq` ativos, via `INSERT ... ON CONFLICT (fonte_tabela, fonte_id,
     idioma) DO UPDATE ... WHERE chunk.conteudo IS DISTINCT FROM
     EXCLUDED.conteudo RETURNING id` — so retorna as linhas cujo conteudo
     REALMENTE mudou (o Postgres nao inclui no RETURNING as linhas cujo
     WHERE do DO UPDATE avaliou falso, ou seja, conteudo inalterado = nop
     silencioso, sem invalidar o embedding existente).
  2. Candidatos a (re)embedding = linhas retornadas (novas OU alteradas)
     UNIAO linhas com `embedding IS NULL` (cobre recovery de boot
     interrompido entre o upsert de texto e o calculo do embedding).
  3. Embeddings calculados em lotes de no maximo 100 textos por chamada a
     `OpenAIClient.embed()` (dec-020 finding #2, API4/LLM10 Unbounded
     Consumption, `checklists/requirements.md` CHK035) — representacao
     semantica calculada 1x, NUNCA recomputada a cada boot para conteudo
     inalterado (FR-009).

Curadoria de `tipo='base'` (Decision 2) e feita via `/admin/chunks`
(app/api/admin.py, FASE 3) — upsert direto, fora deste modulo.

Chamado em `app/main.py:lifespan` DEPOIS de `app.seed.run_seed`, envolto em
try/except NAO-FATAL (mesmo padrao de `app/main.py:102-118`, Decision 0):
extensao `vector`/tabela `chunk` ainda ausente (pre-swap de imagem do
Postgres) nao deve derrubar o boot do app — a responsabilidade de tolerar
essa falha fica no CALL SITE (main.py), nao aqui.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.repository.models import Chunk, CursoObjecao, Faq

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.integrations.openai_client import OpenAIClient

logger = logging.getLogger(__name__)

# Teto de textos por chamada a OpenAIClient.embed() (dec-020 finding #2).
EMBED_BATCH_SIZE = 100


async def _upsert_chunks_from_rows(
    session: "AsyncSession", rows: list[dict]
) -> set[int]:
    """
    Upsert em lote de `chunk`s de UMA fonte (curso_objecao|faq).

    Retorna o conjunto de ids inseridos/atualizados de fato (candidatos a
    (re)embedding) — linhas cujo conteudo nao mudou nao aparecem aqui
    (RETURNING nao as inclui quando o WHERE do DO UPDATE avalia falso).
    """
    changed_ids: set[int] = set()
    for row in rows:
        stmt = (
            pg_insert(Chunk)
            .values(
                curso_id=row["curso_id"],
                tipo=row["tipo"],
                idioma=row["idioma"],
                conteudo=row["conteudo"],
                fonte_tabela=row["fonte_tabela"],
                fonte_id=row["fonte_id"],
                ativo=True,
            )
            .on_conflict_do_update(
                constraint="uq_chunk_fonte",
                set_={
                    "conteudo": row["conteudo"],
                    "curso_id": row["curso_id"],
                    "ativo": True,
                },
                where=(Chunk.conteudo != row["conteudo"]),
            )
            .returning(Chunk.id)
        )
        result = await session.execute(stmt)
        row_id = result.scalar_one_or_none()
        if row_id is not None:
            changed_ids.add(row_id)
    return changed_ids


async def _pendentes_embedding(session: "AsyncSession", fonte_tabela: str) -> set[int]:
    """Ids de chunk de `fonte_tabela` sem embedding calculado (recovery de crash)."""
    result = await session.execute(
        select(Chunk.id).where(
            Chunk.fonte_tabela == fonte_tabela, Chunk.embedding.is_(None)
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

    objecoes = (await session.execute(select(CursoObjecao))).scalars().all()
    objecao_rows = [
        {
            "curso_id": o.curso_id,
            "tipo": "objecao",
            "idioma": o.idioma,
            "conteudo": _objecao_conteudo(o.objecao, o.resposta),
            "fonte_tabela": "curso_objecao",
            "fonte_id": o.id,
        }
        for o in objecoes
    ]
    changed = await _upsert_chunks_from_rows(session, objecao_rows)

    faqs = (
        await session.execute(select(Faq).where(Faq.ativo.is_(True)))
    ).scalars().all()
    faq_rows = [
        {
            "curso_id": None,
            "tipo": "faq",
            "idioma": f.idioma,
            "conteudo": _faq_conteudo(f.pergunta, f.resposta),
            "fonte_tabela": "faq",
            "fonte_id": f.id,
        }
        for f in faqs
    ]
    changed |= await _upsert_chunks_from_rows(session, faq_rows)

    logger.info(
        "rag_seed: %d chunk(s) inserido(s)/atualizado(s) (objecao=%d, faq=%d candidatos)",
        len(changed),
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

    pendentes = changed.copy()
    pendentes |= await _pendentes_embedding(session, "curso_objecao")
    pendentes |= await _pendentes_embedding(session, "faq")

    if pendentes:
        n = await _embed_pendentes(session, openai_client, sorted(pendentes))
        logger.info("rag_seed: %d chunk(s) embedado(s)", n)
    else:
        logger.info("rag_seed: nenhum chunk pendente de embedding")

    await session.commit()
    logger.info("rag_seed: sincronizacao concluida")
