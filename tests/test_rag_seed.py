"""
Testes de `app/rag_seed.py` (Onda 3, RAG hibrido) + regressao do fix de raiz
do "seed-churn" (identidade do chunk por CONTEUDO, nao por `fonte_id` volatil).

Idempotencia entre boots: o `run_seed` faz delete+insert de faq/objecao (ids
seriais novos a cada boot); como o chunk agora e chaveado por
(fonte_tabela, idioma, conteudo_hash), conteudo INALTERADO => mesmo chunk
(mesmo embedding) => nenhum re-embed nem crescimento da tabela.

Nao exige Postgres/pgvector real: `AsyncMock(spec=AsyncSession)` — os statements
SQLAlchemy Core sao objetos validos; o mock controla o retorno de
`session.execute()`. O teste de ciclo-duplo REAL (que prova a idempotencia com
o ON CONFLICT de verdade) esta em `tests/test_seed_idempotencia_ciclo.py`
(guardado por TEST_DATABASE_URL).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Delete
from sqlalchemy.sql.selectable import Select

from app.rag_seed import (
    EMBED_BATCH_SIZE,
    _embed_pendentes,
    _pendentes_embedding,
    _pendentes_embedding_todos,
    _purge_orfaos,
    _upsert_chunks_from_rows,
    run_rag_seed,
)


def _mock_result(scalar_one_or_none=None, all_rows=None, scalars_all=None, rowcount=0):
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=scalar_one_or_none)
    r.all = MagicMock(return_value=all_rows or [])
    r.rowcount = rowcount
    scal = MagicMock()
    scal.all = MagicMock(return_value=scalars_all or [])
    r.scalars = MagicMock(return_value=scal)
    return r


def _row(fonte_tabela, tipo, idioma, curso_id, fonte_id, conteudo, conteudo_hash="h"):
    return {
        "curso_id": curso_id, "tipo": tipo, "idioma": idioma, "conteudo": conteudo,
        "conteudo_hash": conteudo_hash, "fonte_tabela": fonte_tabela, "fonte_id": fonte_id,
    }


# ---------------------------------------------------------------------------
# _upsert_chunks_from_rows — upsert por conteudo (uq_chunk_conteudo)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_chunks_um_execute_por_linha():
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=_mock_result())
    rows = [
        _row("faq", "faq", "pt", None, 1, "a", "hA"),
        _row("faq", "faq", "pt", None, 2, "b", "hB"),
    ]
    ret = await _upsert_chunks_from_rows(session, rows)
    assert ret is None
    assert session.execute.call_count == 2
    # conflito por conteudo (uq_chunk_conteudo), nao por fonte_id
    stmt = session.execute.call_args_list[0].args[0]
    assert "uq_chunk_conteudo" in str(stmt.compile())


@pytest.mark.asyncio
async def test_upsert_chunks_lista_vazia_nao_chama_execute():
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock()
    await _upsert_chunks_from_rows(session, [])
    session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# _purge_orfaos — remove chunks cujo conteudo saiu da Base Oficial
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_purge_orfaos_emite_delete_por_conteudo_hash():
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=_mock_result(rowcount=2))
    n = await _purge_orfaos(session, "faq", {"h1", "h2"})
    assert n == 2
    stmt = session.execute.call_args_list[0].args[0]
    assert isinstance(stmt, Delete)
    sql = str(stmt)
    assert "chunk" in sql and "conteudo_hash" in sql


# ---------------------------------------------------------------------------
# _pendentes_embedding / _pendentes_embedding_todos
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pendentes_embedding_retorna_set_de_ids():
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=_mock_result(all_rows=[(1,), (2,)]))
    assert await _pendentes_embedding(session, "faq") == {1, 2}


@pytest.mark.asyncio
async def test_pendentes_embedding_todos_inclui_qualquer_fonte():
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=_mock_result(all_rows=[(1,), (999,)]))
    assert await _pendentes_embedding_todos(session) == {1, 999}
    sql = str(session.execute.call_args_list[0].args[0])
    assert "fonte_tabela" not in sql and "embedding IS NULL" in sql and "ativo" in sql


# ---------------------------------------------------------------------------
# _embed_pendentes — lotes <=100 (dec-020 #2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embed_pendentes_particiona_em_lotes_de_no_maximo_100():
    ids = list(range(1, 151))
    linhas = [(i, f"c{i}") for i in ids]
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(side_effect=[_mock_result(all_rows=linhas), *[MagicMock() for _ in ids]])
    calls: list[int] = []

    async def fake_embed(textos):
        calls.append(len(textos))
        return [[0.0] * 1536 for _ in textos]

    oc = MagicMock()
    oc.embed = AsyncMock(side_effect=fake_embed)
    total = await _embed_pendentes(session, oc, ids)
    assert total == 150
    assert calls == [100, 50]
    assert all(n <= EMBED_BATCH_SIZE for n in calls)


@pytest.mark.asyncio
async def test_embed_pendentes_lista_vazia_noop():
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock()
    oc = MagicMock()
    oc.embed = AsyncMock()
    assert await _embed_pendentes(session, oc, []) == 0
    oc.embed.assert_not_called()
    session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# run_rag_seed
# ---------------------------------------------------------------------------

class _FakeObjecao:
    def __init__(self, id, curso_id, idioma, objecao, resposta):
        self.id, self.curso_id, self.idioma = id, curso_id, idioma
        self.objecao, self.resposta = objecao, resposta


class _FakeFaq:
    def __init__(self, id, idioma, pergunta, resposta):
        self.id, self.idioma, self.pergunta, self.resposta = id, idioma, pergunta, resposta


@pytest.mark.asyncio
async def test_run_rag_seed_sem_openai_client_so_sincroniza_texto():
    """None path: select obj, upsert obj, select faq, upsert faq, purge obj, purge faq = 6."""
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()
    objecoes = [_FakeObjecao(1, 10, "pt", "esta caro", "vale")]
    faqs = [_FakeFaq(1, "pt", "como funciona", "assim")]
    session.execute = AsyncMock(side_effect=[
        _mock_result(scalars_all=objecoes),  # select CursoObjecao
        _mock_result(),                       # upsert obj
        _mock_result(scalars_all=faqs),       # select Faq
        _mock_result(),                       # upsert faq
        _mock_result(),                       # purge obj
        _mock_result(),                       # purge faq
    ])
    await run_rag_seed(session, None)
    assert session.execute.call_count == 6
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_run_rag_seed_purga_orfaos_por_conteudo():
    """Finding: 2 DELETEs de purga (curso_objecao + faq) por conteudo_hash."""
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()
    objecoes = [_FakeObjecao(1, 10, "pt", "x", "y")]
    faqs = [_FakeFaq(1, "pt", "p", "r")]
    session.execute = AsyncMock(side_effect=[
        _mock_result(scalars_all=objecoes), _mock_result(),
        _mock_result(scalars_all=faqs), _mock_result(),
        _mock_result(), _mock_result(),
    ])
    await run_rag_seed(session, None)
    stmts = [c.args[0] for c in session.execute.call_args_list]
    assert isinstance(stmts[0], Select)                 # select objecoes
    assert isinstance(stmts[4], Delete) and isinstance(stmts[5], Delete)  # purgas
    for d in (stmts[4], stmts[5]):
        assert "conteudo_hash" in str(d)


@pytest.mark.asyncio
async def test_run_rag_seed_primeira_execucao_embeda_chunks_novos():
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()
    objecoes = [_FakeObjecao(1, 10, "pt", "esta caro", "vale o investimento")]
    oc = MagicMock()
    oc.embed = AsyncMock(side_effect=lambda t: [[0.0] * 1536 for _ in t])
    session.execute = AsyncMock(side_effect=[
        _mock_result(scalars_all=objecoes),   # select CursoObjecao
        _mock_result(),                        # upsert obj
        _mock_result(scalars_all=[]),          # select Faq (vazio -> sem upsert)
        _mock_result(),                        # purge obj
        _mock_result(),                        # purge faq
        _mock_result(all_rows=[(42,)]),        # pendentes_todos -> chunk novo 42
        _mock_result(all_rows=[(42, "esta caro\n\nvale o investimento")]),  # select embed
        MagicMock(),                            # update embedding
    ])
    await run_rag_seed(session, oc)
    oc.embed.assert_called_once_with(["esta caro\n\nvale o investimento"])
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_run_rag_seed_embeda_chunk_admin_base_pendente():
    """Finding 1: chunk admin/base com embedding NULL e embedado (qualquer fonte)."""
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()
    objecoes = [_FakeObjecao(1, 10, "pt", "x", "y")]
    oc = MagicMock()
    oc.embed = AsyncMock(side_effect=lambda t: [[0.0] * 1536 for _ in t])
    session.execute = AsyncMock(side_effect=[
        _mock_result(scalars_all=objecoes), _mock_result(),
        _mock_result(scalars_all=[]),
        _mock_result(), _mock_result(),
        _mock_result(all_rows=[(999,)]),                       # pendentes -> admin/base 999
        _mock_result(all_rows=[(999, "secao base curada")]),   # select embed
        MagicMock(),
    ])
    await run_rag_seed(session, oc)
    oc.embed.assert_called_once_with(["secao base curada"])


@pytest.mark.asyncio
async def test_run_rag_seed_idempotente_conteudo_igual_nao_reembeda():
    """Conteudo inalterado (upsert por conteudo preserva embedding): pendentes vazio
    => embed() NAO chamado (FR-009)."""
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()
    objecoes = [_FakeObjecao(1, 10, "pt", "esta caro", "vale")]
    faqs = [_FakeFaq(1, "pt", "como funciona", "assim")]
    oc = MagicMock()
    oc.embed = AsyncMock(side_effect=lambda t: [[0.0] * 1536 for _ in t])
    session.execute = AsyncMock(side_effect=[
        _mock_result(scalars_all=objecoes), _mock_result(),
        _mock_result(scalars_all=faqs), _mock_result(),
        _mock_result(), _mock_result(),
        _mock_result(all_rows=[]),   # pendentes_todos -> nenhum (tudo ja embedado)
    ])
    await run_rag_seed(session, oc)
    oc.embed.assert_not_called()
    session.commit.assert_called_once()
