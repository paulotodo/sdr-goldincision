"""
Testes de `app/rag_seed.py` (tasks 2.2.6, 2.2.7 — Onda 3, RAG hibrido) +
regressao das correcoes de review (follow-up pos-Onda-3):
  - Finding 1: chunks `tipo='base'`/`fonte_tabela='admin'` recebem embedding
    (antes: `rag_seed` so embedava curso_objecao/faq -> base ficava so na
    perna lexical, invisivel semanticamente).
  - Finding 2: reconciliacao de `ativo` (tombstoning) + `objecao` inativa
    excluida na origem (antes: upsert-only servia conteudo que saiu da Base
    Oficial — risco anti-alucinacao).

Cenarios:
- Idempotencia: 2a execucao com conteudo inalterado nao re-embeda nada
- Particionamento em lotes <=100 para conjunto grande de pendentes
- `openai_client=None` sincroniza so o texto (embeddings ficam pendentes)
- Helpers isolados: `_upsert_chunks_from_rows` / `_pendentes_embedding` /
  `_pendentes_embedding_todos` / `_embed_pendentes`

Nao exige Postgres/pgvector real: `AsyncMock(spec=AsyncSession)` — os
statements SQLAlchemy Core sao objetos validos independentemente de execucao
real; o mock controla o retorno de `session.execute()`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.selectable import Select

from app.rag_seed import (
    EMBED_BATCH_SIZE,
    _embed_pendentes,
    _pendentes_embedding,
    _pendentes_embedding_todos,
    _upsert_chunks_from_rows,
    run_rag_seed,
)


def _mock_result(scalar_one_or_none=None, all_rows=None, scalars_all=None):
    """MagicMock generico compativel com os 3 formatos de Result usados aqui."""
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=scalar_one_or_none)
    r.all = MagicMock(return_value=all_rows or [])
    r.rowcount = len(all_rows or [])
    scal = MagicMock()
    scal.all = MagicMock(return_value=scalars_all or [])
    r.scalars = MagicMock(return_value=scal)
    return r


# ---------------------------------------------------------------------------
# _upsert_chunks_from_rows
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_chunks_novo_retorna_id_para_reembed():
    """RETURNING traz o id quando a linha e nova ou o conteudo mudou."""
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=_mock_result(scalar_one_or_none=7))

    rows = [{
        "curso_id": 1, "tipo": "objecao", "idioma": "pt", "conteudo": "x",
        "fonte_tabela": "curso_objecao", "fonte_id": 1,
    }]
    changed = await _upsert_chunks_from_rows(session, rows)
    assert changed == {7}


@pytest.mark.asyncio
async def test_upsert_chunks_conteudo_inalterado_nao_retorna_id():
    """
    Postgres NAO inclui no RETURNING linhas cujo WHERE do DO UPDATE avalia
    falso — reexecucao com mesmo conteudo e nop silencioso (idempotencia).
    """
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=_mock_result(scalar_one_or_none=None))

    rows = [{
        "curso_id": 1, "tipo": "faq", "idioma": "pt", "conteudo": "y",
        "fonte_tabela": "faq", "fonte_id": 5,
    }]
    changed = await _upsert_chunks_from_rows(session, rows)
    assert changed == set()


@pytest.mark.asyncio
async def test_upsert_chunks_lista_vazia_nao_chama_execute():
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock()
    changed = await _upsert_chunks_from_rows(session, [])
    assert changed == set()
    session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# _pendentes_embedding / _pendentes_embedding_todos
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pendentes_embedding_retorna_set_de_ids():
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=_mock_result(all_rows=[(1,), (2,), (3,)]))
    pendentes = await _pendentes_embedding(session, "faq")
    assert pendentes == {1, 2, 3}


@pytest.mark.asyncio
async def test_pendentes_embedding_todos_inclui_qualquer_fonte():
    """
    `_pendentes_embedding_todos` retorna todo chunk ATIVO com embedding NULL,
    inclusive `admin`/base (Finding 1). Aqui: ids 1 (faq) e 999 (admin/base).
    """
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=_mock_result(all_rows=[(1,), (999,)]))
    pendentes = await _pendentes_embedding_todos(session)
    assert pendentes == {1, 999}
    # o SELECT filtra apenas embedding NULL + ativo (nao por fonte_tabela)
    stmt = session.execute.call_args_list[0].args[0]
    sql = str(stmt)
    assert "fonte_tabela" not in sql
    assert "embedding IS NULL" in sql
    assert "ativo" in sql


# ---------------------------------------------------------------------------
# _embed_pendentes — particionamento em lotes <=100 (dec-020 #2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embed_pendentes_particiona_em_lotes_de_no_maximo_100():
    """150 chunks pendentes -> 2 chamadas a embed() (100 + 50), nunca >100/lote."""
    ids = list(range(1, 151))
    linhas = [(i, f"conteudo {i}") for i in ids]

    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(side_effect=[
        _mock_result(all_rows=linhas),           # select id, conteudo
        *[MagicMock() for _ in ids],              # 150 updates de embedding
    ])

    embed_calls: list[int] = []

    async def fake_embed(textos):
        embed_calls.append(len(textos))
        return [[0.0] * 1536 for _ in textos]

    openai_client = MagicMock()
    openai_client.embed = AsyncMock(side_effect=fake_embed)

    total = await _embed_pendentes(session, openai_client, ids)

    assert total == 150
    assert embed_calls == [100, 50]
    assert all(n <= EMBED_BATCH_SIZE for n in embed_calls)


@pytest.mark.asyncio
async def test_embed_pendentes_lista_vazia_nao_chama_embed_nem_execute():
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock()
    openai_client = MagicMock()
    openai_client.embed = AsyncMock()

    total = await _embed_pendentes(session, openai_client, [])
    assert total == 0
    openai_client.embed.assert_not_called()
    session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# run_rag_seed
# ---------------------------------------------------------------------------

class _FakeObjecao:
    def __init__(self, id, curso_id, idioma, objecao, resposta):
        self.id = id
        self.curso_id = curso_id
        self.idioma = idioma
        self.objecao = objecao
        self.resposta = resposta


class _FakeFaq:
    def __init__(self, id, idioma, pergunta, resposta):
        self.id = id
        self.idioma = idioma
        self.pergunta = pergunta
        self.resposta = resposta


@pytest.mark.asyncio
async def test_run_rag_seed_sem_openai_client_so_sincroniza_texto():
    """
    openai_client=None: upsert de texto + reconciliacao de ativo ocorrem;
    embed() nunca e chamado. Sequencia de execute:
      select obj, upsert obj, select faq, upsert faq, reconcile obj, reconcile faq.
    """
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()

    objecoes = [_FakeObjecao(1, 10, "pt", "esta caro", "vale o investimento")]
    faqs = [_FakeFaq(1, "pt", "como funciona", "funciona assim")]

    session.execute = AsyncMock(side_effect=[
        _mock_result(scalars_all=objecoes),      # select CursoObjecao (WHERE ativo)
        _mock_result(scalar_one_or_none=100),     # upsert objecao -> novo
        _mock_result(scalars_all=faqs),           # select Faq (WHERE ativo)
        _mock_result(scalar_one_or_none=200),     # upsert faq -> novo
        _mock_result(),                            # reconcile ativo curso_objecao
        _mock_result(),                            # reconcile ativo faq
    ])

    await run_rag_seed(session, None)

    assert session.execute.call_count == 6
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_run_rag_seed_reconcilia_ativo_tombstone_orfaos():
    """
    Finding 2: sao emitidos 2 UPDATEs de reconciliacao de `ativo` em `chunk`
    (curso_objecao + faq) — fonte deletada/desativada vira chunk orfao e sai
    do RAG (anti-alucinacao). O FAQ ainda e filtrado por `ativo` na origem;
    `CursoObjecao` nao tem `ativo` (so delecao), coberto pela reconciliacao.
    """
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()

    objecoes = [_FakeObjecao(1, 10, "pt", "esta caro", "vale")]
    faqs = [_FakeFaq(1, "pt", "como funciona", "assim")]

    session.execute = AsyncMock(side_effect=[
        _mock_result(scalars_all=objecoes),
        _mock_result(scalar_one_or_none=None),
        _mock_result(scalars_all=faqs),
        _mock_result(scalar_one_or_none=None),
        _mock_result(),   # reconcile obj
        _mock_result(),   # reconcile faq
    ])

    await run_rag_seed(session, None)

    stmts = [c.args[0] for c in session.execute.call_args_list]
    assert isinstance(stmts[0], Select)          # select objecoes
    # SELECT de FAQ (3o) filtra ativo na origem
    assert "ativo" in str(stmts[2])
    # 5o e 6o: UPDATE de reconciliacao de ativo em chunk (tombstoning)
    assert isinstance(stmts[4], Update)
    assert isinstance(stmts[5], Update)
    for upd in (stmts[4], stmts[5]):
        sql = str(upd)
        assert "chunk" in sql
        assert "ativo" in sql


@pytest.mark.asyncio
async def test_run_rag_seed_primeira_execucao_embeda_chunks_novos():
    """1a execucao: conteudo novo -> embed() chamado com os textos corretos."""
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()

    objecoes = [_FakeObjecao(1, 10, "pt", "esta caro", "vale o investimento")]
    faqs: list[_FakeFaq] = []

    openai_client = MagicMock()
    openai_client.embed = AsyncMock(side_effect=lambda textos: [[0.0] * 1536 for _ in textos])

    session.execute = AsyncMock(side_effect=[
        _mock_result(scalars_all=objecoes),                 # select CursoObjecao
        _mock_result(scalar_one_or_none=42),                 # upsert objecao -> novo (id=42)
        _mock_result(scalars_all=faqs),                       # select Faq (vazio -> sem upsert)
        _mock_result(),                                        # reconcile obj
        _mock_result(),                                        # reconcile faq
        _mock_result(all_rows=[]),                             # pendentes_todos (42 ja em `changed`)
        _mock_result(all_rows=[(42, "esta caro\n\nvale o investimento")]),  # select p/ embed
        MagicMock(),                                            # update embedding
    ])

    await run_rag_seed(session, openai_client)

    openai_client.embed.assert_called_once_with(["esta caro\n\nvale o investimento"])
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_run_rag_seed_embeda_chunk_admin_base_pendente():
    """
    Finding 1: mesmo SEM mudanca de objecao/faq, um chunk `admin`/base com
    `embedding IS NULL` e embedado (antes ficava eternamente sem vetor).
    """
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()

    objecoes = [_FakeObjecao(1, 10, "pt", "esta caro", "vale")]  # inalterada
    faqs: list[_FakeFaq] = []

    openai_client = MagicMock()
    openai_client.embed = AsyncMock(side_effect=lambda textos: [[0.0] * 1536 for _ in textos])

    session.execute = AsyncMock(side_effect=[
        _mock_result(scalars_all=objecoes),                 # select CursoObjecao
        _mock_result(scalar_one_or_none=None),               # upsert objecao -> sem mudanca
        _mock_result(scalars_all=faqs),                       # select Faq (vazio)
        _mock_result(),                                        # reconcile obj
        _mock_result(),                                        # reconcile faq
        _mock_result(all_rows=[(999,)]),                       # pendentes_todos -> chunk admin/base 999
        _mock_result(all_rows=[(999, "secao base curada")]),   # select p/ embed
        MagicMock(),                                            # update embedding
    ])

    await run_rag_seed(session, openai_client)

    openai_client.embed.assert_called_once_with(["secao base curada"])
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_run_rag_seed_segunda_execucao_conteudo_igual_nao_reembeda():
    """
    2a execucao com o MESMO conteudo: upserts retornam None (inalterado) e
    nao ha chunk com embedding pendente -> embed() NAO e chamado (idempotencia).
    """
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()

    objecoes = [_FakeObjecao(1, 10, "pt", "esta caro", "vale o investimento")]
    faqs = [_FakeFaq(1, "pt", "como funciona", "funciona assim")]

    openai_client = MagicMock()
    openai_client.embed = AsyncMock(side_effect=lambda textos: [[0.0] * 1536 for _ in textos])

    session.execute = AsyncMock(side_effect=[
        _mock_result(scalars_all=objecoes),     # select CursoObjecao
        _mock_result(scalar_one_or_none=None),   # upsert objecao -> sem mudanca
        _mock_result(scalars_all=faqs),          # select Faq
        _mock_result(scalar_one_or_none=None),    # upsert faq -> sem mudanca
        _mock_result(),                            # reconcile obj
        _mock_result(),                            # reconcile faq
        _mock_result(all_rows=[]),                 # pendentes_todos -> nenhum
    ])

    await run_rag_seed(session, openai_client)

    openai_client.embed.assert_not_called()
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_run_rag_seed_nao_duplica_chunks_ja_sincronizados():
    """
    Reexecucao com as MESMAS fontes nao aumenta o numero de statements
    (mesma UNIQUE(fonte_tabela, fonte_id, idioma)). Sequencia estavel de
    execute entre 1a e 2a execucao (None path): 5 chamadas (obj sem faq).
    """
    objecoes = [_FakeObjecao(1, 10, "pt", "esta caro", "vale o investimento")]

    session1 = AsyncMock(spec=AsyncSession)
    session1.commit = AsyncMock()
    session1.execute = AsyncMock(side_effect=[
        _mock_result(scalars_all=objecoes),
        _mock_result(scalar_one_or_none=42),
        _mock_result(scalars_all=[]),
        _mock_result(),  # reconcile obj
        _mock_result(),  # reconcile faq
    ])
    await run_rag_seed(session1, None)
    primeira = session1.execute.call_count

    session2 = AsyncMock(spec=AsyncSession)
    session2.commit = AsyncMock()
    session2.execute = AsyncMock(side_effect=[
        _mock_result(scalars_all=objecoes),
        _mock_result(scalar_one_or_none=None),  # 2a vez: conteudo igual
        _mock_result(scalars_all=[]),
        _mock_result(),  # reconcile obj
        _mock_result(),  # reconcile faq
    ])
    await run_rag_seed(session2, None)
    segunda = session2.execute.call_count

    assert primeira == segunda == 5
