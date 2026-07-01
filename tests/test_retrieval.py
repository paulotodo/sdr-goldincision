"""
Testes para app/core/retrieval.py (RAG hibrido, Onda 3 — FASE 4,
FR-001..FR-006, FR-013, FR-021).

Cobre (tasks.md FASE 4, com fakes/mocks do repositorio de chunks — sem
exigir Postgres/pgvector real):
- 4.1: pre-filtro produto+idioma — chunk de outro produto/idioma NUNCA
  aparece como candidato, mesmo com score alto.
- 4.2: busca vetorial + textual + fusao RRF + score combinado + top-k.
- 4.3: abstencao por limiar, timeout e erro (fail-closed).
- 4.4: reserva de idioma — SEM fallback cross-idioma.
"""
from __future__ import annotations

import asyncio
from typing import Optional
from unittest.mock import AsyncMock

import pytest

from app.core.retrieval import (
    ChunkCandidato,
    ChunkRecuperado,
    HybridRetriever,
    ResultadoRecuperacao,
    _aplicar_pre_filtro,
    _fundir_por_rrf,
    _rerankear,
)


class FakeChunkRepository:
    """Fake do `ChunkRepository` (Protocol) — devolve listas canned de
    `ChunkCandidato`, configuraveis por teste. Por padrao aplica o mesmo
    pre-filtro que a implementacao real faria via SQL (curso_id/idioma);
    pode ser instanciado em modo "leaky" (sem filtrar) para exercitar a
    defesa em profundidade do HybridRetriever (`_aplicar_pre_filtro`)."""

    def __init__(
        self,
        vetorial: Optional[list[ChunkCandidato]] = None,
        textual: Optional[list[ChunkCandidato]] = None,
        *,
        leaky: bool = False,
        sleep_seconds: float = 0.0,
        raise_exc: Optional[Exception] = None,
    ) -> None:
        self._vetorial = vetorial or []
        self._textual = textual or []
        self._leaky = leaky
        self._sleep_seconds = sleep_seconds
        self._raise_exc = raise_exc

    def _filtrar(self, candidatos, curso_id, idioma):
        if self._leaky:
            return list(candidatos)
        return [
            c
            for c in candidatos
            if (c.curso_id == curso_id or c.curso_id is None) and c.idioma == idioma
        ]

    async def buscar_vetorial(self, query_embedding, curso_id, idioma, k):
        if self._sleep_seconds:
            await asyncio.sleep(self._sleep_seconds)
        if self._raise_exc:
            raise self._raise_exc
        return self._filtrar(self._vetorial, curso_id, idioma)[:k]

    async def buscar_textual(self, query, curso_id, idioma, k):
        if self._raise_exc:
            raise self._raise_exc
        return self._filtrar(self._textual, curso_id, idioma)[:k]


def _fake_openai_client(vetor: Optional[list[float]] = None) -> AsyncMock:
    client = AsyncMock()
    client.embed = AsyncMock(return_value=[vetor or [0.1, 0.2, 0.3]])
    return client


# ---------------------------------------------------------------------------
# 4.1 — Pre-filtro produto + idioma
# ---------------------------------------------------------------------------


def test_pre_filtro_mantem_curso_correto_e_globais():
    candidatos = [
        ChunkCandidato(chunk_id=1, conteudo="c1", tipo="faq", curso_id=10, idioma="pt"),
        ChunkCandidato(chunk_id=2, conteudo="c2", tipo="faq", curso_id=None, idioma="pt"),
        ChunkCandidato(chunk_id=3, conteudo="c3", tipo="faq", curso_id=99, idioma="pt"),
    ]
    filtrados = _aplicar_pre_filtro(candidatos, curso_id=10, idioma="pt")
    assert {c.chunk_id for c in filtrados} == {1, 2}


def test_pre_filtro_descarta_idioma_diferente():
    candidatos = [
        ChunkCandidato(chunk_id=1, conteudo="c1", tipo="faq", curso_id=10, idioma="en"),
    ]
    filtrados = _aplicar_pre_filtro(candidatos, curso_id=10, idioma="pt")
    assert filtrados == []


@pytest.mark.asyncio
async def test_chunk_de_outro_produto_nunca_aparece_mesmo_com_score_alto():
    """tasks.md 4.1.2 — mesmo se o repositorio (fake "leaky", simulando bug)
    devolver um chunk de OUTRO produto com score altissimo (distancia de
    cosseno ~0), o HybridRetriever NUNCA deve deixa-lo alcancar o resultado
    final — defesa em profundidade do pre-filtro (FR-002)."""
    chunk_correto = ChunkCandidato(
        chunk_id=1, conteudo="conteudo do produto certo", tipo="objecao",
        curso_id=10, idioma="pt", distancia_cosseno=0.5,
    )
    chunk_produto_errado = ChunkCandidato(
        chunk_id=2, conteudo="objecao de OUTRO produto", tipo="objecao",
        curso_id=99, idioma="pt", distancia_cosseno=0.01,  # score altissimo
    )
    repo = FakeChunkRepository(
        vetorial=[chunk_produto_errado, chunk_correto], leaky=True,
    )
    retriever = HybridRetriever(repo, _fake_openai_client(), limiar_abstencao=0.0)

    resultado = await retriever.buscar("duvida sobre o curso", curso_id=10, idioma="pt")

    ids_retornados = {c.chunk_id for c in resultado.chunks}
    assert 2 not in ids_retornados
    assert 1 in ids_retornados


# ---------------------------------------------------------------------------
# 4.2 — Busca vetorial + textual + fusao RRF + score combinado
# ---------------------------------------------------------------------------


def test_fundir_por_rrf_deduplica_e_mescla_sinais():
    vetorial = [
        ChunkCandidato(chunk_id=1, conteudo="a", tipo="faq", curso_id=None, idioma="pt", distancia_cosseno=0.1),
    ]
    textual = [
        ChunkCandidato(chunk_id=1, conteudo="a", tipo="faq", curso_id=None, idioma="pt", ts_rank=0.8),
        ChunkCandidato(chunk_id=2, conteudo="b", tipo="faq", curso_id=None, idioma="pt", ts_rank=0.5),
    ]
    fundidos = _fundir_por_rrf(vetorial, textual)
    assert set(fundidos.keys()) == {1, 2}
    # chunk 1 aparece nas duas listas -> ambos os sinais mesclados no mesmo candidato
    assert fundidos[1].distancia_cosseno == 0.1
    assert fundidos[1].ts_rank == 0.8
    # chunk 2 so aparece na textual -> distancia_cosseno permanece None
    assert fundidos[2].distancia_cosseno is None


def test_rerankear_produz_ordem_esperada_com_overlap_parcial():
    """tasks.md 4.2.5 — cenario sintetico com overlap parcial vetorial/textual:
    a ordem final deve refletir score_combinado = 0.6*sim_vetorial +
    0.4*sim_textual_normalizada."""
    vetorial = [
        # sim_vetorial = 1 - 0.1 = 0.9 -> so aparece na vetorial
        ChunkCandidato(chunk_id=1, conteudo="a", tipo="faq", curso_id=None, idioma="pt", distancia_cosseno=0.1),
        # sim_vetorial = 1 - 0.6 = 0.4 -> aparece nas duas (overlap parcial)
        ChunkCandidato(chunk_id=2, conteudo="b", tipo="faq", curso_id=None, idioma="pt", distancia_cosseno=0.6),
    ]
    textual = [
        # chunk 2: ts_rank maximo do lote -> sim_textual_normalizada = 1.0
        ChunkCandidato(chunk_id=2, conteudo="b", tipo="faq", curso_id=None, idioma="pt", ts_rank=1.0),
        # chunk 3: so aparece na textual, sim_textual_normalizada = 0.2/1.0 = 0.2
        ChunkCandidato(chunk_id=3, conteudo="c", tipo="faq", curso_id=None, idioma="pt", ts_rank=0.2),
    ]
    fundidos = _fundir_por_rrf(vetorial, textual)
    recuperados = _rerankear(fundidos, textual, peso_vetorial=0.6, peso_textual=0.4)

    scores = {c.chunk_id: c.score_combinado for c in recuperados}
    # chunk1: 0.6*0.9 + 0.4*0 = 0.54
    assert scores[1] == pytest.approx(0.54)
    # chunk2: 0.6*0.4 + 0.4*1.0 = 0.64 (maior — beneficiado pelo overlap)
    assert scores[2] == pytest.approx(0.64)
    # chunk3: 0.6*0 + 0.4*0.2 = 0.08
    assert scores[3] == pytest.approx(0.08)

    ordem = [c.chunk_id for c in recuperados]
    assert ordem == [2, 1, 3]


@pytest.mark.asyncio
async def test_buscar_retorna_top_k_por_score_desc():
    vetorial = [
        ChunkCandidato(chunk_id=i, conteudo=f"c{i}", tipo="faq", curso_id=None, idioma="pt", distancia_cosseno=d)
        for i, d in enumerate([0.9, 0.1, 0.5, 0.3, 0.7, 0.2], start=1)
    ]
    repo = FakeChunkRepository(vetorial=vetorial, textual=[])
    retriever = HybridRetriever(repo, _fake_openai_client(), top_k=3, limiar_abstencao=0.0)

    resultado = await retriever.buscar("pergunta", curso_id=None, idioma="pt")

    assert len(resultado.chunks) == 3
    assert [c.chunk_id for c in resultado.chunks] == [2, 6, 4]  # distancias 0.1,0.2,0.3 -> scores mais altos
    assert resultado.abster is False


# ---------------------------------------------------------------------------
# 4.3 — Abstencao por limiar, timeout e erro
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abstencao_quando_sem_candidatos():
    repo = FakeChunkRepository(vetorial=[], textual=[])
    retriever = HybridRetriever(repo, _fake_openai_client())

    resultado = await retriever.buscar("pergunta sem nenhuma base", curso_id=1, idioma="pt")

    assert resultado.abster is True
    assert resultado.motivo_abstencao == "sem_candidatos"
    assert resultado.chunks == []


@pytest.mark.asyncio
async def test_abstencao_quando_abaixo_do_limiar():
    baixo_score = ChunkCandidato(
        chunk_id=1, conteudo="pouco relevante", tipo="faq", curso_id=None, idioma="pt",
        distancia_cosseno=0.9,  # sim_vetorial = 0.1 -> bem abaixo do limiar default 0.45
    )
    repo = FakeChunkRepository(vetorial=[baixo_score], textual=[])
    retriever = HybridRetriever(repo, _fake_openai_client(), limiar_abstencao=0.45)

    resultado = await retriever.buscar("pergunta vaga", curso_id=None, idioma="pt")

    assert resultado.abster is True
    assert resultado.motivo_abstencao == "abaixo_limiar"
    # chunks continua populado (informativo) mesmo em abstencao por limiar
    assert len(resultado.chunks) == 1


@pytest.mark.asyncio
async def test_timeout_e_tratado_como_abstencao_indisponivel():
    """tasks.md 4.3.2/4.3.4 — timeout == abstencao (fail-closed)."""
    repo = FakeChunkRepository(vetorial=[], textual=[], sleep_seconds=0.2)
    retriever = HybridRetriever(repo, _fake_openai_client(), timeout_seconds=0.02)

    resultado = await retriever.buscar("pergunta", curso_id=None, idioma="pt")

    assert resultado.abster is True
    assert resultado.motivo_abstencao == "indisponivel"


@pytest.mark.asyncio
async def test_erro_de_db_extensao_ausente_e_tratado_como_abstencao_indisponivel():
    """tasks.md 4.3.3/4.3.4 — simula cenario pre-swap pgvector: extensao/
    tabela `chunk` inexistente estoura excecao no repositorio; deve virar
    abstencao, nunca propagar."""
    repo = FakeChunkRepository(
        raise_exc=RuntimeError('relation "chunk" does not exist'),
    )
    retriever = HybridRetriever(repo, _fake_openai_client())

    resultado = await retriever.buscar("pergunta", curso_id=None, idioma="pt")

    assert resultado.abster is True
    assert resultado.motivo_abstencao == "indisponivel"


@pytest.mark.asyncio
async def test_erro_no_embedding_da_consulta_e_tratado_como_abstencao():
    client = AsyncMock()
    client.embed = AsyncMock(side_effect=ConnectionError("openai indisponivel"))
    repo = FakeChunkRepository(vetorial=[], textual=[])
    retriever = HybridRetriever(repo, client)

    resultado = await retriever.buscar("pergunta", curso_id=None, idioma="pt")

    assert resultado.abster is True
    assert resultado.motivo_abstencao == "indisponivel"


# ---------------------------------------------------------------------------
# 4.4 — Reserva de idioma sem fallback cross-idioma
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("idioma", ["pt", "en", "es"])
async def test_pre_filtro_por_idioma_aplicado_nos_3_idiomas(idioma):
    candidatos = [
        ChunkCandidato(chunk_id=1, conteudo="pt", tipo="faq", curso_id=None, idioma="pt", distancia_cosseno=0.1),
        ChunkCandidato(chunk_id=2, conteudo="en", tipo="faq", curso_id=None, idioma="en", distancia_cosseno=0.1),
        ChunkCandidato(chunk_id=3, conteudo="es", tipo="faq", curso_id=None, idioma="es", distancia_cosseno=0.1),
    ]
    repo = FakeChunkRepository(vetorial=candidatos, textual=[])
    retriever = HybridRetriever(repo, _fake_openai_client(), limiar_abstencao=0.0)

    resultado = await retriever.buscar("pergunta", curso_id=None, idioma=idioma)

    assert resultado.abster is False
    assert len(resultado.chunks) == 1
    esperado_id = {"pt": 1, "en": 2, "es": 3}[idioma]
    assert resultado.chunks[0].chunk_id == esperado_id


@pytest.mark.asyncio
async def test_ausencia_de_chunk_no_idioma_do_lead_gera_abstencao_sem_fallback():
    """tasks.md 4.4.2/4.4.3 — base so tem chunk em pt/en; lead fala es ->
    DEVE abster (nunca fallback cross-idioma, diferente do _load_faq legado)."""
    candidatos = [
        ChunkCandidato(chunk_id=1, conteudo="pt", tipo="faq", curso_id=None, idioma="pt", distancia_cosseno=0.05),
        ChunkCandidato(chunk_id=2, conteudo="en", tipo="faq", curso_id=None, idioma="en", distancia_cosseno=0.05),
    ]
    repo = FakeChunkRepository(vetorial=candidatos, textual=[])
    retriever = HybridRetriever(repo, _fake_openai_client(), limiar_abstencao=0.0)

    resultado = await retriever.buscar("pregunta en espanol", curso_id=None, idioma="es")

    assert resultado.abster is True
    assert resultado.motivo_abstencao == "sem_candidatos"
    assert resultado.chunks == []


# ---------------------------------------------------------------------------
# Dataclasses — defaults/shape (data-model.md §2)
# ---------------------------------------------------------------------------


def test_resultado_recuperacao_defaults():
    resultado = ResultadoRecuperacao()
    assert resultado.chunks == []
    assert resultado.abster is False
    assert resultado.motivo_abstencao is None


def test_chunk_recuperado_shape():
    c = ChunkRecuperado(chunk_id=1, conteudo="x", tipo="base", score_combinado=0.9)
    assert c.chunk_id == 1
    assert c.tipo == "base"
    assert c.score_combinado == 0.9
