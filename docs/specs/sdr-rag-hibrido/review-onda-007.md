# Relatorio de Status das Tarefas

**Data:** 2026-07-01
**Projeto:** sdr-goldincision (Agente SDR WhatsApp GoldIncision)
**Tipo:** Codigo (Python/FastAPI)
**Arquivo de Tarefas:** `docs/specs/sdr-rag-hibrido/tasks.md`
**Feature:** sdr-rag-hibrido (Onda 3) — feature-00c
**PR:** #14 (`66bcb95`, branch `feature/sdr-rag-hibrido` -> `master`, **OPEN**, `mergeable=MERGEABLE`, CI `Lint + Testes (pytest)` = SUCCESS) — **NAO mergeado nesta revisao**

---

## Resumo Executivo

| Metrica | Valor |
|---------|-------|
| Fases | 11 |
| Tarefas | 23 |
| Subtarefas | 80 |
| Concluidas | 80 (100%) |
| Em andamento | 0 |
| Pendentes | 0 |
| Bloqueadas | 0 |
| Suite padrao | 588 passed / 62 deselected (`-m "not golden"`) — confirmado por execucao real nesta revisao |
| Golden set | 62 passed (`-m golden`) — confirmado por execucao real nesta revisao |
| Ruff | `All checks passed!` — confirmado por execucao real nesta revisao |
| Reconciliacao `.tasks[]` vs `tasks.md` | 0 divergencias (`reconcile-tasks --dry-run` sem stdout) |
| Half-records model-routing | 0 (`state-decisions-reconcile.sh check` exit=0) |
| Working tree / branch | limpo, sincronizado com `origin/feature/sdr-rag-hibrido`, HEAD=`66bcb95` |

**Veredito: APROVADO.** Os 4 pilares do RAG hibrido (tabela `chunk`, seed idempotente, `HybridRetriever`, integracao com o contrato JSON/FidelityGate da Onda 2) foram entregues, validados por evidencia empirica (execucao de testes + leitura direta de codigo), e nenhum achado bloqueante foi identificado. A pre-condicao de infraestrutura pgvector esta documentada e o boot permanece tolerante ate o operador aplicar o swap de imagem. Pronto para revisao humana e merge do PR #14 — **merge NAO executado por este orquestrador**.

---

## Auditoria dos 4 pilares do RAG hibrido (evidencia de codigo)

### Pilar 1 — Tabela `chunk` (`app/repository/models.py:420-460`)
- `Chunk`: `embedding: Vector(1536)` (nullable ate o `rag_seed` calcular), `search_vector` (coluna GERADA `TSVECTOR`, definida na migration).
- Indices: `ix_chunk_embedding_hnsw` (HNSW, `m=16 ef_construction=64`, `vector_cosine_ops`) + `ix_chunk_search_vector` (GIN) + `ix_chunk_curso_idioma_ativo` (btree composto).
- `UniqueConstraint("fonte_tabela","fonte_id","idioma")` — evita duplicidade de sincronizacao; `CheckConstraint` restringe `tipo IN (objecao,faq,base)` e `idioma IN (pt,en,es)`.
- Migration `e5f6a7b8c9d0`: `CREATE EXTENSION IF NOT EXISTS vector` envolvida em `try/except` -> `RuntimeError` **nao-fatal**, capturado pelo mesmo padrao ja usado em `app/main.py:_run_alembic_upgrade` (loga e continua o boot). Confirmado por leitura direta da migration.

### Pilar 2 — Seed idempotente de embeddings (`app/rag_seed.py`)
- Sincronizacao `CursoObjecao`/`Faq` -> `chunk` via `INSERT ... ON CONFLICT (fonte_tabela, fonte_id, idioma) DO UPDATE` parcial — **nunca invalida** embedding ja calculado em execucoes repetidas.
- `embedding` calculado 1x (`text-embedding-3-small`), nunca recalculado a cada boot.

### Pilar 3 — `HybridRetriever` (`app/core/retrieval.py`)
- **Pre-filtro produto/idioma ANTES da etapa vetorial**: `SqlAlchemyChunkRepository.buscar_vetorial` (linha ~145) e `buscar_textual` (linha ~180) aplicam `.where((curso_id==X)|(curso_id IS NULL), idioma==Y, ativo==True, ...)` **na propria clausula SQL**, antes do `.order_by(distancia)`/`.order_by(rank.desc())` e do `.limit(k)` — nao e um filtro pos-hoc em memoria. `_aplicar_pre_filtro()` revalida o mesmo criterio em memoria como defesa em profundidade (comentario explicito: "revalidado em memoria... defesa em profundidade").
- Vetorial (pgvector HNSW, `k=RAG_K_VETORIAL`) + textual (tsvector GIN, `k=RAG_K_TEXTUAL`) -> fusao RRF (`_fundir_por_rrf`) -> `score_combinado = 0.6*sim_vetorial + 0.4*sim_textual_normalizada` -> top-N desc -> `top_k=5` (`RAG_TOP_K`).
- **Abstencao fail-closed** (`ResultadoRecuperacao(abster=True, ...)`) em 3 caminhos: (a) `motivo_abstencao="sem_candidatos"` quando o pre-filtro esgota os candidatos; (b) `motivo_abstencao="abaixo_limiar"` quando `top[0].score_combinado < limiar_abstencao` (default `0.45`); (c) `motivo_abstencao="indisponivel"` quando `buscar()` captura QUALQUER excecao/timeout (`RAG_RETRIEVAL_TIMEOUT_SECONDS`) — nunca propaga a excecao ao chamador, simetrico ao `FidelityGate` da Onda 2.
- Reserva de idioma (sem fallback cross-idioma): ausencia de chunk equivalente no idioma do lead esgota candidatos e produz abstencao naturalmente.
- Cache semantico opcional (FASE 8, `RAG_CACHE_ENABLED`): fail-open — falha de GET/SET do Redis vira miss, nunca abstencao (`dec-041`, evidencia `pytest tests/test_rag_cache.py -q: 8 passed`).

### Pilar 4 — Integracao com o contrato JSON / FidelityGate (Onda 2)
- `GroundedResponder.generate()` (`app/core/responder.py:226-271`) recebe `chunks_recuperados: Optional[list[ChunkRecuperado]]` e popula `self.last_fonte_ids = [str(c.chunk_id) for c in chunks_recuperados] if chunks_recuperados else None` **ANTES de qualquer chamada ao LLM** — `fonte_ids` reflete exatamente o que foi injetado no prompt, nao o que o LLM "alega" ter usado (FR-011, anti-alucinacao por construcao, nao por confianca no modelo).
- `FidelityGate` (Onda 2, `app/core/fidelity.py`) consome o contrato `RespostaEstruturada`/`fonte_ids` preexistente sem mudanca de schema — o RAG so passa a alimentar `chunks_recuperados` como nova fonte de grounding.
- `flow.py`: os 3 call-sites de `ETAPA_DUVIDAS` (linhas ~1641/1830/2046) substituem o antigo grounding manual (`_load_faq`) por `HybridRetriever.buscar()`. Apresentacoes/precos/turmas/link de inscricao permanecem **fora do RAG** (verbatim direto da Base, `FR-010`/`FR-014`, confirmado por comentarios e codigo dedicado em `flow.py`).

---

## Auditoria de invariantes/restricoes (sem regressao)

| Invariante | Evidencia |
|---|---|
| Verbatim (apresentacoes/precos/turmas/link) fora do RAG | `app/core/flow.py` mantem carregamento verbatim dedicado (linhas 2142-2167), nunca passa por `HybridRetriever` |
| Abstencao obrigatoria abaixo do limiar (nunca inventa) | `app/core/retrieval.py:475-484`, `score_combinado < limiar_abstencao (0.45)` -> `abster=True`; testado em `tests/test_retrieval.py` |
| Pre-filtro impede vazamento de objecao entre produtos | `.where(curso_id==X \| curso_id IS NULL, idioma==Y)` aplicado nas duas queries SQL antes do ranqueamento + revalidacao em memoria |
| Handoff destino/queueId da allowlist (SEC-LLM-3) | `app/core/flow.py:205-223,917-918` — `_DESTINO_POR_CAMINHO`, resolvido estaticamente, nunca pelo LLM |
| Mensagem do lead como dado nao-confiavel (SEC-LLM-1) | `app/core/responder.py:242,312` — delimitacao explicita, comentada e testada |
| Anti-alucinacao / idioma PT/EN/ES | `Chunk.idioma CHECK IN (pt,en,es)`; `_TSCONFIG_POR_IDIOMA` mapeia tsquery por idioma; reserva de idioma sem fallback cross-idioma |
| Ondas 1+2 preservadas (contadores, reengajamento, debounce, lock TTL, contrato JSON, FidelityGate, SlotExtractor, log_turno) | Suite completa (588 passed) verde, incluindo os testes dedicados dessas ondas; nenhuma mudanca fora do diff aditivo desta feature |
| Anti-loop `_MAX_TENTATIVAS=3` nao fundido com o retry do contrato | `app/core/flow.py:201` (`_MAX_TENTATIVAS = 3`, anti-loop de `etapa_funil`) permanece distinto de `app/core/responder.py:155` (`_MAX_TENTATIVAS_CONTRATO = 2`, retry de pacote JSON malformado) |
| Teto `max_msgs_per_turn=4` | `app/config.py:134` inalterado |
| `_Pacer`+429, idempotencia, lock, gate IA=77, debounce 8s | `app/config.py:60` (`ai_queue_id=77`), `app/config.py:91` (`debounce_seconds=8`), `app/integrations/chatmaster.py:85-325` (`_Pacer`, retry 429/5xx backoff 1s/2s/4s) — todos inalterados, confirmados por grep direto |

---

## Pre-condicao de infraestrutura pgvector + seguranca de producao

- `stack.yml` (`git diff master...HEAD -- stack.yml`): **unica** mudanca estrutural e a troca da imagem do servico `postgres` de `postgres:16-alpine` para `pgvector/pgvector:pg16` (mesma base PG16, volume `sdr-whatsapp_postgres_data` compativel), acompanhada de novas variaveis de ambiente `RAG_*` (todas com default seguro via `${VAR:-default}`). Comentario no diff documenta explicitamente que a aplicacao em producao (`sdr-whatsapp_postgres`) e responsabilidade do **operador**, fora da janela desta feature.
- Boot tolerante confirmado: migration `e5f6a7b8c9d0` e `app/rag_seed.py` sao nao-fatais quando a extensao `vector` esta ausente; o RAG **abstem-se** ate a extensao existir (nenhum crash/degradacao do resto do agente).
- Blast radius: `git diff master...HEAD --stat` lista 44 arquivos, **todos** sob `app/`, `migrations/`, `tests/`, `docs/specs/sdr-rag-hibrido/`, `.claude/` do repo `sdr-goldincision` — nenhum arquivo fora do repo tocado.
- Nenhum comando `docker service update`/swarm foi executado nesta revisao ou (por evidencia de `dec-042`) durante a execucao da feature — a troca de imagem ficou **somente no repo** (`stack.yml`), a cargo do operador aplicar.
- `git status --short --branch` limpo; branch sincronizada com `origin/feature/sdr-rag-hibrido`; `gh pr view 14` confirma `state=OPEN`, `mergeable=MERGEABLE`, CI `SUCCESS`.

---

## Cobertura de Quality Gates

| Gate | Skill | Invocacoes | Resultado |
|---|---|---|---|
| doc-quality (spec.md) | `validate-documentation` | 1 (dec-004) | aceitar, score 3 |
| feature-00c-preflight (clarify->plan) | `feature-00c-preflight.sh` | 1 (dec-016) | prosseguir, score 3 |
| doc-quality (plan/research/data-model) | `validate-documentation` | 1 (dec-019) | corrigir-agora, score 2 (sanado antes de create-tasks) |
| security (design) | `owasp-security` | 1 (dec-020) | 4 findings (2 MEDIUM API3/API4-LLM10, 1 LOW, 1 INFO) — nenhum critical/high; MEDIUMs convertidos em tasks obrigatorias (dec-026, `tasks-dedicadas-mais-regressao`, score 3) |
| template-fidelity (tasks.md) | `validate-tasks-template.sh` (deterministico) | 1 (dec-028) | conformante, score 3 |
| docs-render (tasks.md) | `validate-docs-rendered` | 1 (dec-029) | conformante, score 3 |

Nenhum `skip-com-justificativa` registrado — sem finding `quality-gate-bypass`. Os 2 findings MEDIUM do gate `owasp-security` (dec-020) foram tratados como tasks obrigatorias (nao skip) — confirmado por `dec-026`.

---

## Selecao de modelo por subagente (model-routing)

| subagent_type | etapa | onda | modelo | score | fallback |
|---------------|-------|------|--------|-------|----------|
| feature-00c-clarify-asker | clarify | onda-001 | manter-atual | 0 | no |
| feature-00c-clarify-answerer | clarify | onda-001 | manter-atual | 0 | no |

**Sumario**:
- Total: 2
- haiku: 0
- sonnet: 0
- opus: 0
- manter-atual: 2
- fallback-default: 0 (0%)

## Selecao de modelo por onda (sugerido vs aplicado)

| onda | etapa | sugerido | aplicado | origem | divergente |
|------|-------|----------|----------|--------|------------|
| init | specify | sonnet | sonnet | mapa | no |
| onda-001 | clarify | sonnet | sonnet | mapa | no |
| onda-002 | create-tasks | sonnet | sonnet | mapa | no |
| onda-003 | execute-task | sonnet | sonnet | mapa | no |
| onda-004 | execute-task | sonnet | sonnet | mapa | no |
| onda-005 | execute-task | sonnet | sonnet | mapa | no |
| onda-006 | execute-task | sonnet | sonnet | mapa | no |

**Sumario por onda**:
- Total de ondas roteadas: 7
- aplicado haiku/sonnet/opus/manter-atual: 0/7/0/0
- origem mapa/refino/override-operador/fallback: 7/0/0/0
- fallback (manter-atual): 0 (0%)
- override do operador: 0 (0%)
- divergencias sugerido!=aplicado: 0 (rotuladas: 0, sem rotulo: 0)

Sem divergencia sem rotulo — sem finding `model-routing-divergencia-sem-rotulo`. Half-records: 0.

---

## Tarefas Pendentes

Nenhuma. 80/80 subtarefas concluidas (100%), 0 pendentes, 0 bloqueadas.

---

## Recomendacoes

### Acoes Imediatas
- Nenhuma bloqueante. Feature aprovada — pronta para revisao humana do PR #14.
- **Nao mergear automaticamente**: merge e decisao do humano/operador (inclui coordenar a janela de troca da imagem do Postgres em producao antes ou depois do merge, conforme preferencia operacional).
- (Nao-bloqueante) Apos o operador trocar a imagem `pgvector/pgvector:pg16` em producao, rodar `alembic upgrade head` novamente para completar a criacao da tabela `chunk`/indices, e em seguida `rag_seed` para popular os embeddings.

### Achados nao-bloqueantes
- Nenhum achado divergente do veredito de aprovacao identificado nesta revisao.
