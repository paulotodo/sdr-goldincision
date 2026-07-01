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
  2. Reconciliacao de `ativo` (tombstoning): para `curso_objecao`/`faq`,
     desativa (`ativo=False`) chunks orfaos — cuja fonte foi removida (ambas)
     ou marcada `ativo=False` (so `faq`, que tem essa coluna) — e reativa os
     vigentes. Sem isto, um upsert-only deixaria conteudo que saiu da Base
     Oficial ainda recuperavel (violacao anti-alucinacao).
  3. Candidatos a (re)embedding = linhas retornadas (novas OU alteradas)
     UNIAO TODO chunk ATIVO com `embedding IS NULL` — de QUALQUER fonte,
     inclusive `admin` (chunks `tipo='base'` curados via `/admin/chunks`,
     que gravam `embedding IS NULL` e delegam o calculo a este seed) e
     recovery de boot interrompido.
  4. Embeddings calculados em lotes de no maximo 100 textos por chamada a
     `OpenAIClient.embed()` (dec-020 finding #2, API4/LLM10 Unbounded
     Consumption, `checklists/requirements.md` CHK035) — representacao
     semantica calculada 1x, NUNCA recomputada a cada boot para conteudo
     inalterado (FR-009).

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


async def _reconciliar_ativo(
    session: "AsyncSession", fonte_tabela: str, ativos_fonte_ids: set[int]
) -> None:
    """
    Reconcilia `chunk.ativo` de uma fonte sincronizada (`curso_objecao`/`faq`)
    com o conjunto vigente de `fonte_id`s ATIVOS da origem: ativa os presentes
    e DESATIVA os orfaos (fonte removida ou marcada `ativo=False`). Sem esta
    reconciliacao, um upsert-only deixaria chunks de conteudo que saiu da Base
    Oficial ainda `ativo=True` e recuperaveis — violacao anti-alucinacao
    (servir apenas a Base Oficial vigente). NAO toca `fonte_tabela='admin'`
    (curadoria manual; tombstone via DELETE /admin/chunks).
    """
    stmt = (
        update(Chunk)
        .where(Chunk.fonte_tabela == fonte_tabela)
        .values(ativo=Chunk.fonte_id.in_(sorted(ativos_fonte_ids)))
    )
    result = await session.execute(stmt)
    logger.info(
        "rag_seed: reconciliacao ativo de %s (%d fonte(s) ativa(s), rows=%s)",
        fonte_tabela,
        len(ativos_fonte_ids),
        getattr(result, "rowcount", "?"),
    )


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

    # `CursoObjecao` nao tem coluna `ativo` (diferente de `Faq`): a unica
    # forma de saida da Base Oficial e a delecao da linha — coberta pela
    # reconciliacao de `ativo` abaixo (fonte_id ausente -> chunk orfao).
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
    ativos_objecao = {o.id for o in objecoes}
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
    ativos_faq = {f.id for f in faqs}
    changed |= await _upsert_chunks_from_rows(session, faq_rows)

    # Reconciliacao de `ativo` (tombstoning): fontes removidas/desativadas
    # nao podem continuar recuperaveis (anti-alucinacao — servir apenas a
    # Base Oficial vigente). Roda tambem no caminho sem openai_client.
    await _reconciliar_ativo(session, "curso_objecao", ativos_objecao)
    await _reconciliar_ativo(session, "faq", ativos_faq)

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

    # Candidatos a (re)embedding: alterados (embedding vigente ficou obsoleto)
    # UNIAO todo chunk ATIVO com `embedding IS NULL` (novos + `admin`/base
    # curados + recovery de boot interrompido).
    pendentes = changed.copy()
    pendentes |= await _pendentes_embedding_todos(session)

    if pendentes:
        n = await _embed_pendentes(session, openai_client, sorted(pendentes))
        logger.info("rag_seed: %d chunk(s) embedado(s)", n)
    else:
        logger.info("rag_seed: nenhum chunk pendente de embedding")

    await session.commit()
    logger.info("rag_seed: sincronizacao concluida")
