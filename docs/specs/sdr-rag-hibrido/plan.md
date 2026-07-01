# Implementation Plan — Recuperação Híbrida Ancorada com Abstenção (Onda 3)

Feature: `sdr-rag-hibrido` · Branch: `feature/sdr-rag-hibrido` (a partir de `master`,
Ondas 1 `sdr-turnos-obs` (49bafbb) e 2 `sdr-fidelidade-json` (020a420) mergeadas) ·
Entrega: **PR** (master protegido, CI Lint+Testes obrigatório) — **NÃO mergear**.

Inputs: `spec.md` (FR-001..FR-024-INFRA-PRECONDITION + §Clarifications Q1-Q5),
`research.md`, `data-model.md`, design
`.claude/skills/agente-atendimento-confiavel/padroes-implementacao.md` §4,
`docs/constitution.md` (v1.0.0), `CLAUDE.md`.

## ⚠️ Pré-condição de merge/deploy (dec-013, FR-024-INFRA-PRECONDITION)

**O serviço `sdr-whatsapp_postgres` roda hoje `postgres:16-alpine` — SEM pgvector.**
Esta Onda desenvolve e entrega TODO o código do RAG (migration, tabela `chunk`,
pipeline de recuperação) mesmo assim (ver `research.md` Decision 0: o boot atual não
quebra — `alembic upgrade head` e o seed já são não-fatais em `app/main.py`). Antes
de mergear/deployar esta feature em produção, o **operador** MUST:

1. Trocar a imagem do serviço `postgres` em `stack.yml` de `postgres:16-alpine` para
   `pgvector/pgvector:pg16` (mesma base PG16, volume `sdr-whatsapp_postgres_data`
   compatível — sem perda de dados).
2. Redeploy **apenas** desse serviço (`docker service update` ou re-`docker stack
   deploy` da stack `sdr-whatsapp`) na janela de infraestrutura dele.
3. NÃO tocar em nenhum outro serviço/stack (`fia`, `n8n`, `pgadmin`,
   `postgres_postgres`, `envio-massa`, `fast-api`, `portainer`, `traefik`,
   `metanoia`) — `fia_postgres` (que já tem pgvector) pertence a OUTRO projeto e não
   deve ser referenciado.
4. Só depois do swap `CREATE EXTENSION IF NOT EXISTS vector` (executado no startup
   do app) tem efeito e a tabela `chunk`/embeddings passam a ser preparados
   (`app/rag_seed.py`). Antes disso, a feature simplesmente abstém sempre nas
   perguntas de dúvida (comportamento seguro, nunca crash — `research.md`
   Decision 0/6).

Esta seção MUST ser reproduzida no corpo do PR quando aberto (FASE final).

## Summary

Introduz uma camada de recuperação (retrieval) híbrida e ancorada para o conteúdo
textual livre (objeções + FAQ + seções de base curadas) que hoje é despejado sem
ranqueamento em toda resposta de dúvida. Três peças:

1. **Tabela `chunk`** (pgvector + full-text): unidade de conhecimento com embedding
   `vector(1536)` e `search_vector` (tsvector), sincronizada idempotentemente de
   `CursoObjecao`/`Faq` + curadoria admin para `tipo='base'`.
2. **`HybridRetriever`** (`app/core/retrieval.py`): pré-filtro produto+idioma → busca
   vetorial (k=20) + textual (k=20) → fusão RRF → score combinado → top-5 → abstenção
   se abaixo do limiar (`RAG_LIMIAR_ABSTENCAO=0.45`, calibrável).
3. **Integração com a Onda 2**: `knowledge_context` passa a ser montado com os chunks
   recuperados (em vez do dump bruto); `RespostaEstruturada`/`FidelityGate`
   continuam validando o MESMO conteúdo, agora com rastreabilidade via
   `GroundedResponder.last_fonte_ids` (aditivo, sem quebrar o contrato JSON
   existente).

Apresentação/turmas/link permanecem verbatim e fora do RAG (FR-014). Todos os pontos
de escrita ficam confinados a `app/`, `migrations/`, `stack.yml`, `.env.example` do
projeto-alvo; Ondas 1 e 2 preservadas integralmente.

## Constitution Check (docs/constitution.md v1.0.0)

| Princípio | Como o plano cumpre | Status |
|-----------|----------------------|--------|
| I. Fidelidade ao Fluxo Oficial (Mapa Mestre) | Máquina de estados intocada; retrieval só troca a FONTE do `knowledge_context` dentro da etapa de dúvidas já existente (FR-015). | PASS |
| II. Anti-Alucinação Rígida | Abstenção obrigatória ANTES de chamar o LLM quando score < limiar (FR-005, Decision 7); `fonte_ids` deterministico (não pedido ao LLM, Decision 8); sem fallback cross-idioma no RAG (Decision 11). | PASS — reforça |
| III. Memória e Jornada Sem Atrito | Cache semântico opcional (desligado por padrão) para perguntas repetidas (FR-019); não altera perfil/known_facts. | PASS |
| IV. Comunicação Consultiva Premium | Bloco de abstenção reusa `_fallback_indisponivel_response` já existente e traduzido (PT/EN/ES) — tom consistente. | PASS |
| V. Elegibilidade, Objeções e Handoff Disciplinados | Abstenção aciona o MESMO handoff (allowlist, SEC-LLM-3) já usado — nenhum canal novo (FR-006). | PASS |
| VI. Isolamento e Segurança de Infraestrutura | Único pacote novo: `pgvector` (Python). Sem novas dependências externas de rede; `RAG_EMBEDDING_MODEL`/demais envs sem hardcode; mensagem do lead permanece dado (SEC-LLM-1). Swap de imagem do Postgres documentado como pré-condição operada pelo humano, nunca automatizado por esta feature. | PASS |
| VII. Cursos como Dados (sem redeploy) | Chunks derivam de `CursoObjecao`/`Faq` já existentes + curadoria admin — nenhum conteúdo hardcoded; sincronização idempotente roda no startup (mesmo padrão do catálogo). | PASS |

**Gate de segurança (constitution como MUST)**: nenhum princípio violado. Riscos
OWASP tratados em §OWASP abaixo. Nenhum desvio registrado — sem entradas em
Complexity Tracking.

## Technical Context

- Linguagem/stack: Python 3.12 · FastAPI · **Postgres 16 + pgvector** (pré-condição
  de deploy, ver acima) · Redis 7 (cache semântico opcional, `RAG_CACHE_ENABLED`) ·
  OpenAI · Docker Swarm.
- Dependência nova: `pgvector` (pacote Python, `pgvector.sqlalchemy.Vector`) —
  ÚNICA dependência nova desta feature.
- Migration Alembic nova: `CREATE EXTENSION IF NOT EXISTS vector` (tolerante a
  falha antes do swap de imagem) + tabela `chunk` + índices HNSW/GIN.
- Modelos: gpt-4o (`openai_model_reasoning`)/gpt-4o-mini (`openai_model_cheap`)
  INALTERADOS (Onda 2); `text-embedding-3-small` NOVO, só para embeddings
  (`RAG_EMBEDDING_MODEL`).

## Mapeamento FR → arquivo → abordagem (ancorado em refs reais)

### Schema e preparação de conteúdo (FR-007..FR-010, FR-023-INFRA-IDEMP)

| FR | Arquivo | Abordagem |
|----|---------|-----------|
| FR-007, FR-008 | `app/repository/models.py` (`Chunk`, novo) | 1 linha de origem (`CursoObjecao`/`Faq`) = 1 chunk; `fonte_tabela`+`fonte_id` rastreia a proveniência (data-model.md §1). |
| FR-007 (`tipo='base'`) | `app/api/admin.py` (novo endpoint `POST/GET/DELETE /admin/chunks`) | Curadoria manual explícita (Q2/dec-fase-1); protegido por `Depends(verify_admin_token)` — MESMO guard usado em todos os endpoints de `/admin` (`admin.py:354` etc., FR de acesso, gate OWASP finding 1 abaixo). |
| FR-009, FR-010, FR-023-INFRA-IDEMP | `app/rag_seed.py` (novo) + `migrations/versions/<rev>_add_chunk_pgvector.py` | Upsert condicional (`ON CONFLICT ... DO UPDATE ... WHERE conteudo IS DISTINCT FROM`); só reembeda o que mudou (research.md Decision 9). |
| Embedding | `app/integrations/openai_client.py` (`OpenAIClient.embed`, novo) | `text-embedding-3-small`, 1536 dims, chamado em lote pelo `rag_seed`. |
| Migration/pré-condição | `migrations/versions/<rev>_add_chunk_pgvector.py`, `stack.yml` | `CREATE EXTENSION IF NOT EXISTS vector` + tabela + índices HNSW(`embedding`)/GIN(`search_vector`); `stack.yml` troca a imagem do serviço `postgres` para `pgvector/pgvector:pg16` (aplicado pelo operador — ver §Pré-condição). |

### Pipeline de recuperação híbrida (FR-001..FR-006, FR-013, FR-021)

| FR | Arquivo | Abordagem |
|----|---------|-----------|
| FR-002 | `app/core/retrieval.py` (`HybridRetriever`, novo) | Pré-filtro `(curso_id OR NULL) AND idioma AND ativo` ANTES de qualquer busca (data-model.md §2). |
| FR-001, FR-003, FR-004 | `app/core/retrieval.py` | Busca vetorial (k=20) + textual (k=20) → RRF → score combinado → top-5 (research.md Decision 4). |
| FR-005, FR-006 | `app/core/flow.py` (3 call-sites de `ETAPA_DUVIDAS`: `:1641`, `:1830`, `:2046`) | Abstenção CURTO-CIRCUITA antes do LLM: `abster=True` → `_fallback_indisponivel_response(idioma), True` direto (research.md Decision 7). |
| FR-013 | `app/core/retrieval.py` | Pré-filtro por idioma aplicado nas 3 combinações PT/EN/ES; ausência no idioma do lead = abstenção (Decision 11, SEM fallback cross-idioma — diferente do `_load_faq` atual). |
| FR-021 | `app/core/retrieval.py` | Timeout duro `RAG_RETRIEVAL_TIMEOUT_SECONDS=3.0` + captura de qualquer exceção (inclusive `chunk`/`vector` inexistentes antes do swap) como `abster=True` (research.md Decision 6). |
| FR-014 | `app/core/flow.py` (`_load_knowledge_by_slug`) | Seções de Apresentação/Turmas/Link permanecem verbatim, sem tocar no RAG (research.md Decision 1). |
| FR-019 (SHOULD) | `app/core/retrieval.py` + Redis | Cache semântico opcional, `RAG_CACHE_ENABLED=false` por padrão (research.md Decision 5). |

### Integração com Onda 2 — rastreabilidade (FR-011, FR-012, FR-018, US4)

| FR | Arquivo | Abordagem |
|----|---------|-----------|
| FR-011 | `app/core/responder.py` (`GroundedResponder.last_fonte_ids`, novo atributo) | Populado deterministicamente a partir dos `chunk.id` incluídos no prompt — NUNCA reportado pelo LLM (research.md Decision 8, data-model.md §3). |
| FR-012 | `app/core/fidelity.py` (sem mudança de assinatura) | `FidelityGate.verificar()` já recebe o mesmo `knowledge_context` formado pelos chunks recuperados — valida automaticamente contra as MESMAS unidades. |
| FR-018 | `app/observability/log.py` (`log_turno`, aditivo) | `fonte_ids` logado junto do veredito de fidelidade já existente (Onda 2, task 4.3), sem novo endpoint. |
| FR-022, US4 | Consulta direta aos registros (Q4/resolvida) | Sem endpoint admin novo — revisão de amostras via banco/logs, mesmo padrão de `log_turno`. |

### Restrições de segurança e preservação (FR-016, FR-017 + RESTRIÇÕES INVIOLÁVEIS)

- **SEC-LLM-1** (mensagem = dado): mensagem do lead nunca entra na consulta de
  embedding/textual como instrução — é tratada como texto de busca, delimitada como
  já ocorre no prompt de redação.
- **SEC-LLM-3** (handoff da allowlist): abstenção aciona o MESMO mecanismo de
  handoff já existente (`handoff_queue_ids_json`) — nenhum destino novo.
- **Verbatim intacto** (Onda 2, Decision 7): Apresentação/menus/paciente-modelo/link
  saem do DB sem LLM e sem RAG — inalterados por esta feature.
- **Preservar Ondas 1 + 2**: `log_turno`, contadores/nudge/handoff, reengajamento,
  debounce recovery, `lock_ttl_ms`, `max_msgs_per_turn=4`, `_Pacer`+429,
  idempotência, gate IA=77, `debounce_seconds=8`, anti-loop `_MAX_TENTATIVAS=3`
  (Onda 1); contrato JSON `RespostaEstruturada`, `FidelityGate`, `SlotExtractor`
  (Onda 2) — todas as alterações desta Onda são aditivas (research.md Decision 10).
- **Elegibilidade médica**: gatilho de "condição comercial" do `FidelityGate`
  (`elegib` em `CONDICOES_COMERCIAIS`, `fidelity.py`) inalterado — chunks de
  elegibilidade seguem o mesmo caminho de verificação já existente.

## Envs novos (config + stack.yml + .env.example)

| Env | Local | Default |
|-----|-------|---------|
| `RAG_EMBEDDING_MODEL` | `app/config.py` (`rag_embedding_model: str`), `.env.example`, `stack.yml` | `text-embedding-3-small` |
| `RAG_LIMIAR_ABSTENCAO` | `app/config.py` (`rag_limiar_abstencao: float`), `.env.example`, `stack.yml` | `0.45` |
| `RAG_K_VETORIAL` | `app/config.py` (`rag_k_vetorial: int`), `.env.example`, `stack.yml` | `20` |
| `RAG_K_TEXTUAL` | `app/config.py` (`rag_k_textual: int`), `.env.example`, `stack.yml` | `20` |
| `RAG_TOP_K` | `app/config.py` (`rag_top_k: int`), `.env.example`, `stack.yml` | `5` |
| `RAG_RETRIEVAL_TIMEOUT_SECONDS` | `app/config.py` (`rag_retrieval_timeout_seconds: float`), `.env.example`, `stack.yml` | `3.0` |
| `RAG_CACHE_ENABLED` | `app/config.py` (`rag_cache_enabled: bool`), `.env.example`, `stack.yml` | `false` |

Padrão herdado das Ondas 1/2: sem hardcode, sem secrets, teste de config valida
defaults + override dos 7 envs novos.

## Estratégia de testes (RESTRIÇÃO INVIOLÁVEL)

- **FlowEngine REAL**: mock apenas de `OpenAIClient` (inclusive o novo `embed()`),
  nunca do motor (mesmo padrão de `tests/test_reengajamento.py`,
  `tests/test_responder.py`).
- Novos testes de unidade: `Chunk`/migration (constraints + índices), `rag_seed`
  (idempotência — 2ª execução não duplica nem reembeda sem mudança), `HybridRetriever`
  (pré-filtro produto+idioma, RRF, score combinado, abstenção por limiar, timeout ==
  abstenção, erro de DB/extensão ausente == abstenção — simula o cenário pré-swap),
  `GroundedResponder.last_fonte_ids` (populado corretamente / `None` quando sem
  chunks), config (defaults + override dos 7 envs novos).
- Testes de integração: os 3 call-sites de `ETAPA_DUVIDAS` em `flow.py` — abstenção
  curto-circuita sem chamar `OpenAIClient` de redação; recuperação bem-sucedida
  alimenta `knowledge_context` e `FidelityGate` com as MESMAS unidades.
- Golden set estendido: `@pytest.mark.golden` em `tests/golden/`, fora do CI padrão —
  casos de groundedness (resposta ancorada em chunk específico, US1) e abstenção
  (pergunta fora de escopo, US2), reusando a infraestrutura já existente (Q3/dec
  -fase-1 — curadoria do golden set fica fora do escopo de código desta feature).
- Ao final da execução: suíte inteira verde + `ruff` limpo.

## OWASP / superfície de ataque (gate owasp-security)

Revisão aplicada em fase de plano (sem código ainda) — findings viram requisitos de
design incorporados ao mapeamento acima; nenhum finding critical/high (não exige
BloqueioHumano).

- **LLM01 Prompt Injection**: mensagem do lead usada como texto de busca (embedding/
  tsquery), nunca como instrução; delimitação SEC-LLM-1 preservada no prompt de
  redação. Mitigado.
- **LLM02 Insecure Output Handling**: `Chunk.conteudo` sempre vem de fontes já
  curadas (`CursoObjecao`/`Faq`/admin) — nunca de saída de LLM não validada.
  `RespostaEstruturada` continua validada por Pydantic `extra="forbid"` (Onda 2,
  inalterado).
- **LLM04 Denial of Service / custo**: `RAG_RETRIEVAL_TIMEOUT_SECONDS=3.0` +
  `RAG_K_VETORIAL/RAG_K_TEXTUAL=20` limitam candidatos avaliados por consulta;
  `llm_max_tokens_per_hour`/`_Pacer` existentes continuam válidos (embeddings não
  contam tokens de chat, mas o volume é limitado por `RAG_TOP_K=5` injetado no
  prompt de redação).
- **LLM06 Sensitive Info Disclosure**: chunks derivam de conteúdo já oficial/público
  do catálogo — sem PII nova. `fonte_ids` logado em `log_turno` é uma lista de IDs
  numéricos, não-sensível (mesmo padrão do veredito booleano de fidelidade).
- **LLM08 Vector/Embedding Weaknesses (RAG poisoning / cross-tenant leak)**:
  ingestão de `chunk` é 100% admin-gated (sincronização automática de
  `CursoObjecao`/`Faq`, já curados, OU via `/admin/chunks` autenticado) — nenhum
  conteúdo submetido por lead/usuário final entra no índice vetorial. Pré-filtro
  `curso_id`/`idioma` sempre na cláusula `WHERE` da query (nunca filtrado só em
  memória depois de buscar) — isolamento por produto garantido no nível SQL
  (FR-002/US3). Mitigado.
- **A03 Injection (SQL)**: queries de busca vetorial/textual usam parâmetros
  bindados via SQLAlchemy (`select(...).where(...)` / `text()` com bind params) —
  NUNCA concatenação de string com `query`/`idioma`/`curso_id` do usuário.
- **A05 Security Misconfiguration**: `CREATE EXTENSION IF NOT EXISTS vector` é
  idempotente e restrito ao Postgres do próprio serviço `sdr-whatsapp_postgres`
  (nenhum outro serviço/stack tocado — ver §Pré-condição). Sem novo endpoint de rede
  exposto ao Traefik (o novo endpoint `/admin/chunks` reusa a mesma superfície e
  autenticação de `/admin` já existente, sem novo `network_main` label).
- **Sem nova dependência de rede externa**: `pgvector` é uma extensão do Postgres já
  interno à `sdr-internal` (rede privada, nunca exposta) — nenhuma superfície de
  ataque de rede nova.

### Findings (severidade — incorporados ao design acima, sem BloqueioHumano)

| # | Severidade | Categoria | Finding | Mitigação incorporada ao plano |
|---|-----------|-----------|---------|----------------------------------|
| 1 | MEDIUM | API3 BOPLA (mass assignment) / FR-008 integridade | `POST /admin/chunks` NÃO MUST aceitar `fonte_tabela`/`fonte_id`/`embedding`/`ativo` no corpo da requisição — um valor malicioso/errado de `fonte_tabela='faq'` + `fonte_id=<id real>` colidiria com a `UNIQUE(fonte_tabela, fonte_id, idioma)` e sequestraria silenciosamente uma linha auto-sincronizada de `Faq`, quebrando a idempotência (FR-010) e a proveniência (FR-008). | Endpoint aceita SOMENTE `{curso_id, tipo, idioma, conteudo}` e **restringe `tipo` a `'base'`** (server-side, 422 se `objecao`/`faq`); servidor sempre grava `fonte_tabela='admin'`, `fonte_id=<id autoincrementado do próprio chunk>` — nunca client-supplied. |
| 2 | MEDIUM | API4/LLM10 Unbounded Consumption | Sem limite de tamanho em `conteudo` (admin) nem cap de lote no `rag_seed` — POST grande ou reprocessamento completo poderiam gerar custo de embedding desproporcional (DoS de custo). | `conteudo` limitado a 4000 caracteres no schema Pydantic do endpoint (mesma ordem de grandeza de uma objeção/FAQ real); `rag_seed` envia embeddings em lotes de no máximo 100 textos por chamada a `OpenAIClient.embed()`. |
| 3 | LOW | A03 Supply Chain Failures | `pgvector` (pacote Python) é a única dependência nova — precisa de pin de versão, não "latest" implícito. | Adicionar `"pgvector>=0.3.0"` (ou versão estável mais recente no momento da task) ao `pyproject.toml`, mesmo padrão de pin dos demais deps (`sqlalchemy[asyncio]>=2.0.36` etc.). |
| 4 | INFO | A01 Broken Access Control | Confirma que o novo endpoint não introduz gap de auth. | `/admin/chunks` reusa `Depends(verify_admin_token)` (`app/api/admin.py`) — mesmo guard de TODOS os endpoints `/admin` existentes; nenhuma rota nova sem autenticação. |

## Progress Tracking

- [x] Constitution Check inicial (todos PASS)
- [x] research.md (Decisions 0–12)
- [x] data-model.md (`chunk`, `ResultadoRecuperacao`, `last_fonte_ids`, envs)
- [x] plan.md (este documento) com mapeamento FR→arquivo ancorado + pré-condição pgvector
- [ ] gates doc-quality (spec + plan) e owasp-security (plan) — próximo nesta onda
- [ ] checklist (se couber nesta onda)
- [ ] create-tasks → execute-task → review-task (ondas seguintes)

## Próximo passo

Rodar gates `validate-documentation` (doc-quality) sobre `spec.md`/`plan.md` e
`owasp-security` sobre `plan.md`. Se o orçamento da onda permitir, avançar para
`checklist`; senão, fechar a onda e agendar a continuação via
`/feature-00c-resume sdr-rag-hibrido`.
