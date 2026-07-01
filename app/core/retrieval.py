"""
Recuperacao Hibrida Ancorada com Abstencao (RAG hibrido, Onda 3,
FR-001..FR-006, FR-013, FR-021).

`HybridRetriever.buscar()` e o unico ponto de entrada a ser consumido pelos
3 call-sites de `ETAPA_DUVIDAS` em `app/core/flow.py` (FASE 5,
`flow.py:1641/1830/2046`): pre-filtro por metadados (produto/idioma) SEMPRE
antes de qualquer ranqueamento (FR-002) -> busca vetorial (pgvector HNSW,
k=RAG_K_VETORIAL) + busca textual (tsvector GIN, k=RAG_K_TEXTUAL) -> fusao
Reciprocal Rank Fusion (RRF) -> score combinado
`0.6*sim_vetorial + 0.4*sim_textual_normalizada` -> top-N por score desc ->
abstencao (FR-005) quando nao ha candidato suficientemente relevante
(`data-model.md` §2, `research.md` Decision 4).

Fail-closed simetrico ao `FidelityGate` (Onda 2, `app/core/fidelity.py`):
timeout duro (`RAG_RETRIEVAL_TIMEOUT_SECONDS`) e QUALQUER excecao (extensao/
tabela `chunk` inexistente antes do swap de imagem do Postgres, erro de rede
a OpenAI, etc.) sao capturados e tratados como abstencao
(`motivo_abstencao="indisponivel"`) — NUNCA propagados ao chamador, NUNCA
cai de volta no comportamento antigo de despejar conteudo sem filtro
(`research.md` Decision 6).

Reserva de idioma (FR-002/FR-013, `research.md` Decision 10): o pre-filtro
usa SEMPRE o idioma exato do lead — sem fallback cross-idioma. Ausencia de
chunk equivalente no idioma do lead esgota os candidatos e produz abstencao
naturalmente (nunca um fallback silencioso para outro idioma, diferente do
`_load_faq` legado).

Fronteira mockavel (`ChunkRepository`, `Protocol`) permite testar TODA a
logica de pre-filtro/RRF/score/abstencao/reserva de idioma com fakes em
memoria, sem exigir Postgres/pgvector real (`tasks.md` FASE 4). A
implementacao real (`SqlAlchemyChunkRepository`) so roda contra
Postgres+pgvector em producao/integracao.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.integrations.openai_client import OpenAIClient

logger = logging.getLogger(__name__)

# Config por idioma do tsquery Postgres (mesma convencao da coluna gerada
# `search_vector` definida na migration Alembic, FASE 1).
_TSCONFIG_POR_IDIOMA = {"pt": "portuguese", "en": "english", "es": "spanish"}


@dataclass
class ChunkCandidato:
    """Candidato cru vindo de UMA busca (vetorial OU textual), ANTES da
    fusao RRF. `distancia_cosseno` (menor=melhor, distancia de cosseno 0..2)
    OU `ts_rank` (maior=melhor, sem teto fixo) — preenchido conforme a busca
    de origem; o outro fica `None` ate a fusao mesclar candidatos que
    aparecem nas duas listas."""

    chunk_id: int
    conteudo: str
    tipo: str  # objecao|faq|base
    curso_id: Optional[int]
    idioma: str
    distancia_cosseno: Optional[float] = None
    ts_rank: Optional[float] = None


@dataclass
class ChunkRecuperado:
    """Um chunk candidato apos fusao RRF + score combinado
    (`data-model.md` §2, `research.md` Decision 4)."""

    chunk_id: int
    conteudo: str
    tipo: str  # objecao|faq|base
    score_combinado: float  # 0.0..1.0 — usado contra o LIMIAR de abstencao


@dataclass
class ResultadoRecuperacao:
    """Resultado de UMA chamada de recuperacao (1 pergunta livre do lead)."""

    chunks: list[ChunkRecuperado] = field(default_factory=list)  # top-N, desc por score
    abster: bool = False  # FR-005: True == nao ha fonte suficiente
    # "sem_candidatos" | "abaixo_limiar" | "indisponivel"
    motivo_abstencao: Optional[str] = None


class ChunkRepository(Protocol):
    """Fronteira mockavel entre `HybridRetriever` e a fonte de dados. Cada
    implementacao DEVE aplicar o pre-filtro
    `(curso_id = :curso_id OR curso_id IS NULL) AND idioma = :idioma AND
    ativo` internamente, SEMPRE antes de qualquer ranqueamento (FR-002).
    `HybridRetriever` tambem revalida esse filtro em memoria como defesa em
    profundidade — ver `_aplicar_pre_filtro`."""

    async def buscar_vetorial(
        self,
        query_embedding: list[float],
        curso_id: Optional[int],
        idioma: str,
        k: int,
    ) -> list[ChunkCandidato]:
        ...

    async def buscar_textual(
        self,
        query: str,
        curso_id: Optional[int],
        idioma: str,
        k: int,
    ) -> list[ChunkCandidato]:
        ...


class SqlAlchemyChunkRepository:
    """Implementacao real contra Postgres+pgvector (indices HNSW/GIN,
    `app/repository/models.py::Chunk`, migration da FASE 1). So exercitada
    em producao/integracao — pgvector nao roda contra SQLite, por isso os
    testes unitarios de `HybridRetriever` (FASE 4) usam fakes que
    implementam o `Protocol` `ChunkRepository` acima
    (`research.md` Decision 4; `tasks.md` 4.2.5)."""

    def __init__(self, session: "AsyncSession") -> None:
        self._session = session

    async def buscar_vetorial(
        self,
        query_embedding: list[float],
        curso_id: Optional[int],
        idioma: str,
        k: int,
    ) -> list[ChunkCandidato]:
        from sqlalchemy import select

        from app.repository.models import Chunk

        distancia = Chunk.embedding.cosine_distance(query_embedding).label("distancia")
        stmt = (
            select(Chunk, distancia)
            .where(
                (Chunk.curso_id == curso_id) | (Chunk.curso_id.is_(None)),
                Chunk.idioma == idioma,
                Chunk.ativo.is_(True),
                Chunk.embedding.is_not(None),
            )
            .order_by(distancia)
            .limit(k)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            ChunkCandidato(
                chunk_id=chunk.id,
                conteudo=chunk.conteudo,
                tipo=chunk.tipo,
                curso_id=chunk.curso_id,
                idioma=chunk.idioma,
                distancia_cosseno=float(distancia_valor),
            )
            for chunk, distancia_valor in rows
        ]

    async def buscar_textual(
        self,
        query: str,
        curso_id: Optional[int],
        idioma: str,
        k: int,
    ) -> list[ChunkCandidato]:
        from sqlalchemy import func, select

        from app.repository.models import Chunk

        tsconfig = _TSCONFIG_POR_IDIOMA.get(idioma, "portuguese")
        tsquery = func.plainto_tsquery(tsconfig, query)
        rank = func.ts_rank(Chunk.search_vector, tsquery).label("rank")
        stmt = (
            select(Chunk, rank)
            .where(
                (Chunk.curso_id == curso_id) | (Chunk.curso_id.is_(None)),
                Chunk.idioma == idioma,
                Chunk.ativo.is_(True),
                Chunk.search_vector.op("@@")(tsquery),
            )
            .order_by(rank.desc())
            .limit(k)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            ChunkCandidato(
                chunk_id=chunk.id,
                conteudo=chunk.conteudo,
                tipo=chunk.tipo,
                curso_id=chunk.curso_id,
                idioma=chunk.idioma,
                ts_rank=float(rank_valor),
            )
            for chunk, rank_valor in rows
        ]


def _aplicar_pre_filtro(
    candidatos: list[ChunkCandidato],
    curso_id: Optional[int],
    idioma: str,
) -> list[ChunkCandidato]:
    """Revalida em memoria o pre-filtro produto+idioma (FR-002) — defesa em
    profundidade caso um `ChunkRepository` (real ou fake) tenha um bug e
    devolva candidatos fora do escopo. Nunca deve filtrar nada quando o
    repositorio ja aplicou o `WHERE` corretamente; existe apenas para
    garantir que chunk de outro produto/idioma jamais alcance o ranking,
    mesmo com score alto (`tasks.md` 4.1.2)."""
    filtrados = [
        c
        for c in candidatos
        if (c.curso_id == curso_id or c.curso_id is None) and c.idioma == idioma
    ]
    ignorados = len(candidatos) - len(filtrados)
    if ignorados:
        logger.warning(
            "retrieval: pre-filtro em memoria descartou %d candidato(s) fora "
            "de escopo (curso_id=%s idioma=%s) — possivel bug no repositorio",
            ignorados,
            curso_id,
            idioma,
        )
    return filtrados


def _fundir_por_rrf(
    vetorial: list[ChunkCandidato],
    textual: list[ChunkCandidato],
) -> dict[int, ChunkCandidato]:
    """Fusao RRF (`research.md` Decision 4): decide o CONJUNTO de candidatos
    que sobrevive a fusao, unindo as duas listas (dedup por `chunk_id`) —
    robusto a escalas incomparaveis entre distancia de cosseno e `ts_rank`.
    Quando o mesmo chunk aparece nas duas buscas, mescla os dois sinais
    (`distancia_cosseno` + `ts_rank`) num unico candidato para o rerank por
    score combinado (passo seguinte, `_rerankear`)."""
    fundidos: dict[int, ChunkCandidato] = {}
    for cand in vetorial:
        fundidos[cand.chunk_id] = cand
    for cand in textual:
        existente = fundidos.get(cand.chunk_id)
        if existente is None:
            fundidos[cand.chunk_id] = cand
        elif existente.ts_rank is None:
            existente.ts_rank = cand.ts_rank
    return fundidos


def _rerankear(
    fundidos: dict[int, ChunkCandidato],
    textual: list[ChunkCandidato],
    peso_vetorial: float,
    peso_textual: float,
) -> list[ChunkRecuperado]:
    """"Rerank" por score combinado (MVP, `research.md` Decision 4 passo 5):
    `score_combinado = peso_vetorial * sim_vetorial +
    peso_textual * sim_textual_normalizada`, onde `sim_vetorial =
    1 - distancia_cosseno` (0..1) e `sim_textual_normalizada = ts_rank /
    max(ts_rank do lote)` (0..1) — produz um score interpretavel 0..1
    comparavel a um limiar fixo."""
    max_ts_rank = max(
        (c.ts_rank for c in textual if c.ts_rank is not None), default=0.0
    )
    recuperados: list[ChunkRecuperado] = []
    for cand in fundidos.values():
        sim_vetorial = (
            1.0 - cand.distancia_cosseno if cand.distancia_cosseno is not None else 0.0
        )
        sim_textual = (
            cand.ts_rank / max_ts_rank
            if (cand.ts_rank is not None and max_ts_rank > 0)
            else 0.0
        )
        score = peso_vetorial * sim_vetorial + peso_textual * sim_textual
        recuperados.append(
            ChunkRecuperado(
                chunk_id=cand.chunk_id,
                conteudo=cand.conteudo,
                tipo=cand.tipo,
                score_combinado=score,
            )
        )
    recuperados.sort(key=lambda c: c.score_combinado, reverse=True)
    return recuperados


class HybridRetriever:
    """Pipeline de recuperacao hibrida ancorada com abstencao (Onda 3,
    FR-001..FR-006, FR-013, FR-021). Ver docstring do modulo para a
    sequencia completa."""

    def __init__(
        self,
        chunk_repository: ChunkRepository,
        openai_client: "OpenAIClient",
        *,
        limiar_abstencao: float = 0.45,
        k_vetorial: int = 20,
        k_textual: int = 20,
        top_k: int = 5,
        timeout_seconds: float = 3.0,
        peso_vetorial: float = 0.6,
        peso_textual: float = 0.4,
    ) -> None:
        self._repo = chunk_repository
        self._openai_client = openai_client
        self._limiar_abstencao = limiar_abstencao
        self._k_vetorial = k_vetorial
        self._k_textual = k_textual
        self._top_k = top_k
        self._timeout_seconds = timeout_seconds
        self._peso_vetorial = peso_vetorial
        self._peso_textual = peso_textual

    async def buscar(
        self,
        query: str,
        curso_id: Optional[int],
        idioma: str,
    ) -> ResultadoRecuperacao:
        """
        Recupera os `top_k` chunks mais relevantes para `query`, restritos a
        `curso_id` (ou globais, `curso_id IS NULL`) e ao `idioma` exato do
        lead — SEM fallback cross-idioma (FR-002, `research.md` Decision
        10).

        Fail-closed (FR-021, `research.md` Decision 6): timeout duro
        (`timeout_seconds`) ou QUALQUER excecao durante a busca (embedding
        da consulta, query vetorial, query textual — inclusive extensao/
        tabela `chunk` inexistente antes do swap de imagem do Postgres)
        retorna `ResultadoRecuperacao(abster=True,
        motivo_abstencao="indisponivel")`, nunca propaga a excecao ao
        chamador.
        """
        try:
            return await asyncio.wait_for(
                self._buscar_interno(query, curso_id, idioma),
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            logger.warning(
                "retrieval: busca falhou/expirou (fail-closed abstencao). "
                "timeout=%ss curso_id=%s idioma=%s err=%s: %s",
                self._timeout_seconds,
                curso_id,
                idioma,
                type(exc).__name__,
                exc,
            )
            return ResultadoRecuperacao(abster=True, motivo_abstencao="indisponivel")

    async def _buscar_interno(
        self,
        query: str,
        curso_id: Optional[int],
        idioma: str,
    ) -> ResultadoRecuperacao:
        query_embeddings = await self._openai_client.embed([query])
        query_embedding = query_embeddings[0]

        vetorial = await self._repo.buscar_vetorial(
            query_embedding, curso_id, idioma, self._k_vetorial
        )
        textual = await self._repo.buscar_textual(
            query, curso_id, idioma, self._k_textual
        )

        # FR-002: pre-filtro produto+idioma revalidado em memoria — defesa
        # em profundidade, alem do WHERE que o repositorio ja aplicou.
        vetorial = _aplicar_pre_filtro(vetorial, curso_id, idioma)
        textual = _aplicar_pre_filtro(textual, curso_id, idioma)

        if not vetorial and not textual:
            logger.info(
                "retrieval: nenhum candidato apos pre-filtro (curso_id=%s "
                "idioma=%s) -> abstencao",
                curso_id,
                idioma,
            )
            return ResultadoRecuperacao(chunks=[], abster=True, motivo_abstencao="sem_candidatos")

        fundidos = _fundir_por_rrf(vetorial, textual)
        recuperados = _rerankear(
            fundidos, textual, self._peso_vetorial, self._peso_textual
        )
        top = recuperados[: self._top_k]

        if not top:
            return ResultadoRecuperacao(chunks=[], abster=True, motivo_abstencao="sem_candidatos")

        if top[0].score_combinado < self._limiar_abstencao:
            logger.info(
                "retrieval: melhor score %.4f abaixo do limiar %.4f "
                "(curso_id=%s idioma=%s) -> abstencao",
                top[0].score_combinado,
                self._limiar_abstencao,
                curso_id,
                idioma,
            )
            return ResultadoRecuperacao(chunks=top, abster=True, motivo_abstencao="abaixo_limiar")

        return ResultadoRecuperacao(chunks=top, abster=False, motivo_abstencao=None)
