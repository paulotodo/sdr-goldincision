# Data Model — Recuperação Híbrida Ancorada com Abstenção (Onda 3)

Feature: `sdr-rag-hibrido` · Stack: Python 3.12 · SQLAlchemy 2.0 (async) · Postgres 16
+ **pgvector** (pré-condição de deploy — ver `research.md` Decision 0) · Pydantic v2.
Uma tabela nova (`chunk`) + dois schemas em memória. Mapeia as Key Entities da
`spec.md`.

Princípios transversais (herdados das Ondas 1/2, ver `research.md` Decision 10):
- A mensagem do lead é dado não-confiável (SEC-LLM-1).
- Pré-filtro por produto/idioma SEMPRE antes de qualquer ranqueamento (FR-002).
- Ausência de fonte suficientemente relevante == abstenção, nunca resposta
  improvisada (FR-005, Princípio II).

---

## 1. `chunk` — Unidade de Conhecimento (tabela nova, FR-007..FR-010)

Arquivo: `app/repository/models.py` (classe `Chunk`, ao lado de `Faq`/
`CursoObjecao`). Migration: `migrations/versions/<rev>_add_chunk_pgvector.py`.

```python
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index,
    Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

class Chunk(Base):
    """Unidade de conhecimento recuperável (FR-007): uma objecao, uma entrada
    de FAQ, ou uma secao de base curada pelo admin. NUNCA fatiada por tamanho
    fixo — sempre 1 unidade de significado completo."""
    __tablename__ = "chunk"
    __table_args__ = (
        UniqueConstraint("fonte_tabela", "fonte_id", "idioma", name="uq_chunk_fonte"),
        CheckConstraint("tipo IN ('objecao','faq','base')", name="ck_chunk_tipo"),
        CheckConstraint("idioma IN ('pt','en','es')", name="ck_chunk_idioma"),
        Index(
            "ix_chunk_embedding_hnsw", "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunk_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_chunk_curso_idioma_ativo", "curso_id", "idioma", "ativo"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # NULL = aplica a todos os cursos (FAQ global, mesmo escopo do `Faq` hoje).
    curso_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("curso.id", ondelete="CASCADE"), nullable=True,
    )
    tipo: Mapped[str] = mapped_column(Text, nullable=False)       # objecao|faq|base
    idioma: Mapped[str] = mapped_column(Text, nullable=False)     # pt|en|es
    conteudo: Mapped[str] = mapped_column(Text, nullable=False)   # texto oficial verbatim
    # Provenancia deterministica (FR-008): de qual tabela/linha de origem este
    # chunk foi sincronizado (curso_objecao|faq) ou "admin" para tipo=base.
    fonte_tabela: Mapped[str] = mapped_column(Text, nullable=False)
    fonte_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Representacao semantica (FR-009): calculada 1x, nunca a cada boot.
    # NULL ate o rag_seed calcular o embedding.
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536), nullable=True)
    # Coluna GERADA (STORED) — config por idioma via CASE (imutavel, permitido
    # em coluna gerada). Nao mapeada em Python; definida na migration Alembic.
    search_vector: Mapped[Optional[str]] = mapped_column(TSVECTOR, nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), onupdate=func.now())
```

- **FR-008**: `fonte_tabela + fonte_id` aponta sempre para `curso_objecao.id`,
  `faq.id`, ou o registro de admin que originou o chunk `base` — nunca uma segunda
  fonte de conteúdo divergente.
- **FR-009/FR-010/FR-023-INFRA-IDEMP**: `UNIQUE(fonte_tabela, fonte_id, idioma)` +
  upsert condicional (`research.md` Decision 9) garante que reprocessar não duplica
  e só recalcula `embedding` quando `conteudo` muda de fato.
- **Migration** (Alembic, ver `plan.md` mapeamento): `op.execute("CREATE EXTENSION
  IF NOT EXISTS vector")` (tolerante a falha antes do swap de imagem —
  `research.md` Decision 0) + criação da tabela + os 3 índices acima + coluna gerada
  `search_vector AS (to_tsvector(CASE idioma WHEN 'en' THEN 'english'::regconfig
  WHEN 'es' THEN 'spanish'::regconfig ELSE 'portuguese'::regconfig END, conteudo))
  STORED`.

---

## 2. `ResultadoRecuperacao` — Resultado da busca híbrida (em memória, FR-001..FR-006)

Produzido por `HybridRetriever.buscar()`. Arquivo proposto: `app/core/retrieval.py`.
NÃO é enviado à OpenAI (schema interno, sem `response_format`).

```python
from dataclasses import dataclass, field

@dataclass
class ChunkRecuperado:
    """Um chunk candidato apos fusao RRF + score combinado (research.md Decision 4)."""
    chunk_id: int
    conteudo: str
    tipo: str                 # objecao|faq|base
    score_combinado: float    # 0.0..1.0 — usado contra o LIMIAR de abstencao

@dataclass
class ResultadoRecuperacao:
    """Resultado de UMA chamada de recuperacao (1 pergunta livre do lead)."""
    chunks: list[ChunkRecuperado] = field(default_factory=list)  # top-5, desc por score
    abster: bool = False       # FR-005: True == nao ha fonte suficiente
    motivo_abstencao: Optional[str] = None  # "sem_candidatos" | "abaixo_limiar" | "indisponivel"
```

- **FR-005**: `abster=True` quando `chunks` vazio OU `chunks[0].score_combinado <
  settings.rag_limiar_abstencao` (default `0.45`).
- **FR-021**: `motivo_abstencao="indisponivel"` quando a busca falhou/expirou (timeout,
  erro de DB/embedding) — tratado EXATAMENTE como ausência de fonte relevante.
- **Consumo**: `_load_knowledge_by_slug` (`app/core/flow.py`) monta o
  `knowledge_context` concatenando as seções verbatim (Apresentação/Turmas/Link,
  fora do RAG) com `"\n\n".join(c.conteudo for c in resultado.chunks)` quando
  `abster=False`; quando `abster=True`, o handler retorna direto
  `_fallback_indisponivel_response(idioma), True` sem chamar `generate()`
  (`research.md` Decision 7).

---

## 3. `RespostaEstruturada.last_fonte_ids` — Rastreabilidade (FR-011, FR-012, US4)

**Sem mudança de schema** em `app/core/contracts.py` (`RespostaEstruturada` continua
`{texto, fontes, precisa_handoff, confianca, idioma}`, inalterado — `research.md`
Decision 8). Nova exposição ADITIVA em `GroundedResponder`
(`app/core/responder.py`), mesmo padrão de `last_fidelidade_fiel`:

```python
class GroundedResponder:
    def __init__(self, ...) -> None:
        ...
        self.last_fonte_ids: Optional[list[str]] = None  # NOVO (Onda 3)

    async def generate(self, ..., chunks_recuperados: Optional[list["ChunkRecuperado"]] = None, ...):
        self.last_fonte_ids = (
            [str(c.chunk_id) for c in chunks_recuperados] if chunks_recuperados else None
        )
        ...
```

- **FR-011**: `last_fonte_ids` fica disponível ao `FlowEngine` (que já lê
  `last_fidelidade_fiel`/`last_fidelidade_afirmacoes_nao_sustentadas` para
  `log_turno`) sem alterar a 2-tupla `(texto, handoff)` retornada (FR-006 da Onda 2
  preservado).
- **FR-012**: como `chunks_recuperados` é a MESMA lista usada para montar
  `knowledge_context` (que por sua vez alimenta `FidelityGate.verificar()`), o
  portão de fidelidade automaticamente valida contra as MESMAS unidades — sem
  qualquer alteração na assinatura de `FidelityGate`.
- **Observabilidade (FR-018, US4)**: `log_turno` (Onda 1,
  `app/observability/log.py`) passa a registrar `fonte_ids` (lista de ids de chunk)
  de forma aditiva, junto de `veredito_fidelidade` (Onda 2) — permite a US4
  ("revisar, para uma amostra de turnos passados, quais unidades foram
  recuperadas") via consulta direta aos registros (Q4/resolvida — sem endpoint novo).

---

## 4. Configuração (envs novos)

Adicionados a `app/config.py` (`Settings`), `.env.example` e `stack.yml`:

| Env | Campo Settings | Tipo | Default | Uso |
|-----|-----------------|------|---------|-----|
| `RAG_EMBEDDING_MODEL` | `rag_embedding_model` | str | `text-embedding-3-small` | FR-020, modelo de embedding (1536 dims) |
| `RAG_LIMIAR_ABSTENCAO` | `rag_limiar_abstencao` | float | `0.45` | FR-005, patamar mínimo calibrável |
| `RAG_K_VETORIAL` | `rag_k_vetorial` | int | `20` | FR-004, candidatos avaliados (busca vetorial) |
| `RAG_K_TEXTUAL` | `rag_k_textual` | int | `20` | FR-004, candidatos avaliados (busca textual) |
| `RAG_TOP_K` | `rag_top_k` | int | `5` | FR-004, tamanho do conjunto final usado no grounding |
| `RAG_RETRIEVAL_TIMEOUT_SECONDS` | `rag_retrieval_timeout_seconds` | float | `3.0` | FR-021, timeout duro (mesmo padrão de `VERIFY_TIMEOUT_SECONDS`) |
| `RAG_CACHE_ENABLED` | `rag_cache_enabled` | bool | `false` | FR-019 (SHOULD), cache semântico opcional, desligado por padrão |

Modelo de embedding é NOVO (nenhum dos dois modelos existentes —
`openai_model_reasoning`/`openai_model_cheap` — faz embeddings); os demais modelos
(gpt-4o/gpt-4o-mini para redação/fidelidade) permanecem inalterados.

---

## 5. Fluxo de dados (resumo)

```
pergunta livre do lead (DUVIDAS/objecao, mesmos 3 call-sites de hoje)
   │
   ▼
HybridRetriever.buscar(query, curso_id, idioma)
   ├─ pre-filtro (curso_id OR NULL) AND idioma AND ativo   [FR-002 — SEMPRE antes]
   ├─ busca vetorial (k=20) + busca textual (k=20)
   ├─ fusao RRF -> score combinado (0.6*vetorial + 0.4*textual_normalizado)
   └─ top-5 por score_combinado desc
        │
        ├─ abster=True (sem candidatos / score<LIMIAR / erro / timeout)
        │      └─▶ _fallback_indisponivel_response(idioma), True   [SEM chamar LLM]
        │
        └─ abster=False
               knowledge_context = verbatim(apresentacao+turmas+link) + chunks.conteudo
               fonte_ids = [chunk.id, ...]
                    │
                    ▼
               GroundedResponder.generate(..., chunks_recuperados=chunks)
                    │  RespostaEstruturada (contrato JSON, Onda 2, inalterado)
                    │  self.last_fonte_ids = fonte_ids   (NOVO — aditivo)
                    │
                    ├─ toca condicao comercial? (Onda 2, dec-010)
                    │     sim -> FidelityGate.verificar(texto, knowledge_context)
                    │             fiel=True  -> envia
                    │             fiel=False -> _fallback_indisponivel_response(...), True
                    │     nao -> envia
                    │
                    └─ log_turno: + fonte_ids (aditivo, US4/FR-018)
```

Verbatim (Apresentação/Turmas/Link) permanece fora deste diagrama, sem RAG, sem
mudança de comportamento (`research.md` Decision 1).
