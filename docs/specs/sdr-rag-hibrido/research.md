# Research — Recuperação Híbrida Ancorada com Abstenção (Onda 3)

Feature: `sdr-rag-hibrido` · Fase: plan · Branch: `feature/sdr-rag-hibrido` (a partir
de `master`, Ondas 1 `sdr-turnos-obs` (49bafbb) e 2 `sdr-fidelidade-json` (020a420)
mergeadas). Design primário:
`.claude/skills/agente-atendimento-confiavel/padroes-implementacao.md` §4 (Pilar 4 —
Recuperação ancorada). Constitution: `docs/constitution.md` (v1.0.0).

Este documento registra as decisões técnicas (com alternativas descartadas) que
sustentam `plan.md` e `data-model.md`. As decisões Q1–Q5 já foram ratificadas na fase
clarify (`spec.md` §Clarifications; `state.json` dec-009..dec-013/dec-015).

## Decision 0 — Pré-condição de infraestrutura pgvector (Q1/dec-013, FR-024-INFRA-PRECONDITION)

- **Decisão**: desenvolver TODO o código desta feature (migration, tabela `chunk`,
  índices HNSW/GIN, pipeline de recuperação) e abrir o PR normalmente, mesmo com
  pgvector **ainda não habilitado** no Postgres de produção. Fato verificado pelo
  operador (`docker service ls`): o serviço `sdr-whatsapp_postgres` roda hoje
  `postgres:16-alpine` (sem pgvector); o container `fia_postgres`
  (`pgvector/pgvector:pg16`) é de OUTRO projeto (stack `fia`) e não deve ser
  referenciado/tocado. A troca de imagem do serviço `sdr-whatsapp_postgres` para
  `pgvector/pgvector:pg16` (mesma base PG16, volume `sdr-whatsapp_postgres_data`
  compatível, redeploy só desse serviço) é registrada como **pré-condição explícita
  de merge/deploy** em `spec.md` (FR-024-INFRA-PRECONDITION), neste `plan.md` e no
  corpo do PR — executada pelo operador na janela de infraestrutura dele.
- **Por quê não desenvolver "no escuro" é seguro aqui**: `app/main.py:102-118`
  (`lifespan`) já envolve `alembic upgrade head` E o `run_seed` em blocos
  `try/except` que **logam e continuam** em vez de derrubar o boot
  (`logger.exception(...)  # continuando`). Isso significa que, ANTES do swap de
  imagem, a migration desta feature (`CREATE EXTENSION IF NOT EXISTS vector`) falha
  de forma NÃO-FATAL (extensão indisponível na imagem `postgres:16-alpine`) e o novo
  seed de embeddings (tabela `chunk` inexistente) também falha de forma não-fatal —
  o app continua subindo normalmente com o comportamento atual (sem RAG). O
  comportamento de runtime da retrieval em si (Decision 6 — `app/core/retrieval.py`)
  captura qualquer erro de DB (`relation "chunk" does not exist`,
  `extension "vector" does not exist`) como "mecanismo indisponível" e aplica FR-021
  (abstenção + handoff) — o MESMO caminho seguro que já existe para qualquer outra
  indisponibilidade do mecanismo de recuperação. Ou seja: o código desta feature é
  seguro de mergear e deployar SEM quebrar o boot atual mesmo antes do swap — apenas
  o comportamento de recuperação (RAG) fica inerte (abstém sempre) até o operador
  trocar a imagem.
- **Alternativas descartadas**: (a) aguardar o swap de imagem antes de escrever
  qualquer código — bloquearia toda a Onda 3 numa dependência de infraestrutura fora
  do controle desta execução autônoma; (b) tentar automatizar o swap de imagem via
  este PR/pipeline — violaria o blast radius (nenhuma ação de infraestrutura de
  produção fora de editar arquivos do repositório-alvo) e a restrição de nunca tocar
  outros serviços/stacks sem a janela do operador.

## Decision 1 — Escopo do RAG: apenas conteúdo textual livre (FR-007, FR-014)

- **Decisão**: a camada de recuperação híbrida cobre EXCLUSIVAMENTE as duas fontes de
  conteúdo textual livre já existentes — `CursoObjecao` (banco de objeções por curso/
  idioma) e `Faq` (FAQ oficial, global) — e uma nova categoria `tipo='base'` para
  seções de documento de base marcadas explicitamente pelo operador (Decision 2).
  Apresentação oficial (`CursoApresentacao`), turmas (`CursoTurma`) e link de
  inscrição (`CursoLink`) permanecem **fora** do RAG, enviados verbatim direto do
  catálogo exatamente como hoje (FR-014) — `_load_knowledge_by_slug`
  (`app/core/flow.py:2118`) mantém essas três seções inalteradas; só a parte que hoje
  despeja `Objeções` + `FAQ` sem ranqueamento é substituída pela recuperação.
- **Por quê**: FR-007 define a unidade de significado como "uma objeção = uma
  unidade; uma entrada de FAQ = uma unidade; uma seção coerente de documento de base
  = uma unidade" — mapeia 1:1 nas duas tabelas já existentes mais a categoria nova.
  FR-014 é explícito: verbatim/preço/link "continua sendo enviado exatamente como
  hoje, sem passar por busca ou ranqueamento".

## Decision 2 — Origem das unidades de conteúdo / chunking curado (Q2/dec-fase-1, score 3)

- **Decisão**: a tabela `chunk` é populada por **sincronização automática e
  idempotente** a partir de `CursoObjecao` e `Faq` (1 linha de origem = 1 chunk,
  sem heurística de fatiamento) — MAIS uma via de **curadoria manual explícita** via
  API de admin (`POST /admin/chunks`) para `tipo='base'` (seções de documento de
  base que não têm tabela própria hoje). A curadoria manual é o mecanismo previsto
  pela clarify Q2 ("segmentação manual/curada pelo operador... via API de admin"):
  não há fatiamento automático de documentos brutos nesta entrega.
- **Por quê**: FR-008 exige que as unidades derivem exclusivamente dos MESMOS
  documentos oficiais que já alimentam o catálogo — sincronizar de `CursoObjecao`/
  `Faq` (já curados/oficiais) evita uma segunda fonte divergente; a via de admin para
  `tipo='base'` usa a MESMA API de admin já existente (`app/api/admin.py`), reusando
  o padrão de autenticação/autorização em vez de inventar um mecanismo novo.
- **Alternativa descartada**: fatiamento automático de documentos (chunking por
  tamanho fixo ou heurística de parágrafo) — explicitamente rejeitado pela Q2
  ratificada (criaria uma segunda fonte de verdade divergente do catálogo curado).

## Decision 3 — Schema da tabela `chunk` e representação semântica

- **Decisão**: nova tabela `chunk` (ver `data-model.md` §1) com:
  `id, curso_id (FK curso.id, NULLABLE — NULL = aplica a todos os cursos, usado pelo
  FAQ global), tipo (objecao|faq|base), idioma (pt|en|es), conteudo (text),
  fonte_tabela + fonte_id (proveniência determinística — FR-008), embedding
  (vector(1536), nullable até ser calculado), search_vector (tsvector GERADO,
  STORED), ativo, criado_em, atualizado_em`. Restrição
  `UNIQUE(fonte_tabela, fonte_id, idioma)` garante upsert idempotente (mesmo padrão
  `ON CONFLICT DO UPDATE` já usado por `_upsert_curso`/`_upsert_faq` em
  `app/seed.py`).
- **Índices**: `HNSW (embedding vector_cosine_ops)` para a busca vetorial e
  `GIN (search_vector)` para a busca textual, mais um `btree(curso_id, idioma,
  ativo)` para o pré-filtro (Decision 4). `search_vector` é uma coluna gerada com
  `to_tsvector(<config por idioma>, conteudo)` — a config (`portuguese`/`english`/
  `spanish`) é resolvida por `CASE idioma` dentro da própria expressão gerada
  (`to_tsvector(regconfig, text)` é IMMUTABLE no catálogo do Postgres — permitido em
  coluna gerada).
- **Modelo de embedding**: `text-embedding-3-small` (1536 dimensões) — default fixado
  pela clarify (§Clarifications, defaults). Novo método `OpenAIClient.embed(texts:
  list[str]) -> list[list[float]]` (`app/integrations/openai_client.py`), reusando o
  cliente OpenAI já configurado (mesma API key/secret).
- **Dependência nova**: pacote `pgvector` (Python, `pgvector.sqlalchemy.Vector`) para
  o tipo de coluna SQLAlchemy + `op.execute("CREATE EXTENSION IF NOT EXISTS vector")`
  na migration Alembic. Único pacote novo desta feature.

## Decision 4 — Pipeline de recuperação híbrida (FR-001..FR-006, FR-013)

- **Decisão**: novo módulo `app/core/retrieval.py` com `HybridRetriever.buscar(db,
  query, curso_id, idioma) -> ResultadoRecuperacao` (ver `data-model.md` §2),
  implementando o pseudocódigo canônico de
  `padroes-implementacao.md §4b`:
  1. **Pré-filtro obrigatório** (FR-002): toda candidata restrita a
     `(curso_id = :curso_id OR curso_id IS NULL) AND idioma = :idioma AND ativo`
     ANTES de qualquer ranqueamento — nunca depois.
  2. **Busca vetorial**: `ORDER BY embedding <=> :query_embedding LIMIT
     k_vetorial=20` (distância de cosseno), só sobre linhas com `embedding IS NOT
     NULL`.
  3. **Busca textual**: `WHERE search_vector @@ plainto_tsquery(:tsconfig, :query)
     ORDER BY ts_rank(...) DESC LIMIT k_textual=20`.
  4. **Fusão** por Reciprocal Rank Fusion (RRF, `k=60`): decide o CONJUNTO de
     candidatos combinando as duas listas de forma robusta a escalas diferentes
     (cosseno vs. `ts_rank` não são comparáveis diretamente).
  5. **"Rerank" por score combinado** (MVP, cross-encoder como evolução futura por
     decisão da clarify): para os candidatos que sobreviveram à fusão RRF, calcula
     `score_combinado = 0.6 * sim_vetorial + 0.4 * sim_textual_normalizada`, onde
     `sim_vetorial = 1 - distancia_cosseno` (0..1) e `sim_textual_normalizada =
     ts_rank / max(ts_rank do lote)` (0..1) — produz um score interpretável 0..1
     comparável a um limiar fixo (o RRF score cru não seria, por não ter escala
     fixa).
  6. **Top-5** (`top_k=5`) por `score_combinado` desc.
  7. **Abstenção** (FR-005): se `score_combinado` do item #1 do top-5 < `LIMIAR`
     (default `0.45`, calibrável via env — `RAG_LIMIAR_ABSTENCAO`) OU zero
     candidatos foram encontrados, `ResultadoRecuperacao.abster = True`.
- **Por quê RRF em vez de só um dos dois sinais**: FR-003 exige combinar
  correspondência semântica E por termos exatos numa única ordem — RRF é o padrão
  documentado em `padroes-implementacao.md §4b` e evita que um sinal (ex.: sinônimo
  raro que o embedding capta mas nenhum termo bate) fique invisível ao outro.
- **Alternativa descartada**: normalizar e somar diretamente cosseno + `ts_rank` sem
  RRF — descartado porque as escalas das duas métricas variam por consulta
  (`ts_rank` não é limitado a [0,1] de forma estável), tornando a fusão direta menos
  robusta que RRF para decidir QUAIS 5 entram no conjunto final.

## Decision 5 — Limiar de abstenção e cache semântico (defaults da clarify)

- **Decisão**: `RAG_LIMIAR_ABSTENCAO=0.45` (calibrável, `app/config.py`),
  `RAG_K_VETORIAL=20`, `RAG_K_TEXTUAL=20`, `RAG_TOP_K=5`,
  `RAG_EMBEDDING_MODEL=text-embedding-3-small`. Cache semântico (FR-019, SHOULD) fica
  **desligado por padrão** (`RAG_CACHE_ENABLED=false`) — quando ligado, é uma camada
  fina em Redis (reusa `REDIS_URL` já existente) chaveada por
  `hash(curso_id + idioma + normalizacao(query))`, TTL curto, sem alterar o
  comportamento observável da resposta (mesmo resultado, mais rápido) — desativado
  não afeta corretude, apenas custo/latência de perguntas repetidas.
- **Por quê OFF por padrão**: FR-019 é SHOULD (não MUST); a clarify já fixou
  "semantic cache opcional/desligado no início" como default explícito — evita
  introduzir uma superfície de cache/staleness antes de calibrar o LIMIAR com casos
  reais (US4).

## Decision 6 — Indisponibilidade do mecanismo == abstenção (FR-021)

- **Decisão**: `HybridRetriever.buscar()` envolve TODA a operação (embedding da
  consulta + as duas queries SQL) num timeout duro `RAG_RETRIEVAL_TIMEOUT_SECONDS`
  (default `3.0`, mesmo padrão de `VERIFY_TIMEOUT_SECONDS` da Onda 2) e captura
  QUALQUER exceção (timeout, erro de rede à OpenAI, erro de SQL incluindo `relation
  "chunk" does not exist`/`extension "vector" does not exist` — ver Decision 0) como
  `ResultadoRecuperacao.abster = True`, nunca propagando a exceção para cima nem
  caindo de volta no comportamento antigo de despejar tudo sem filtro.
- **Por quê**: FR-021 é explícito — "MUST se comportar como se nenhuma fonte
  relevante tivesse sido encontrada... nunca cair de volta para o comportamento
  anterior". Fail-closed simétrico ao `FidelityGate` da Onda 2 (Decision 2 do
  `research.md` da Onda 2).

## Decision 7 — Ponto de curto-circuito: abstenção ANTES de chamar o LLM de redação

- **Decisão**: nos três call-sites de `_responder.generate()` para `ETAPA_DUVIDAS`
  (`app/core/flow.py:1641`, `:1830`, `:2046`), a chamada a `HybridRetriever.buscar()`
  acontece ANTES de `_responder.generate()`. Se `abster=True`, o handler retorna
  diretamente `_fallback_indisponivel_response(idioma), True` (reusa o MESMO bloco
  canônico já usado pelo `FidelityGate` reprovado na Onda 2 —
  `app/core/responder.py:517`) — **sem chamar o LLM de redação** e sem passar pelo
  `FidelityGate` (nada foi gerado para verificar). Quando NÃO há abstenção, os
  chunks recuperados substituem a seção de Objeções+FAQ dentro de
  `_load_knowledge_by_slug` (que passa a receber `user_message` como parâmetro
  adicional) e os IDs desses chunks alimentam `fonte_ids` (Decision 8).
- **Por quê**: FR-005 exige "abster-se de gerar QUALQUER resposta de conteúdo" — se a
  abstenção só acontecesse depois de gerar o texto (como o `FidelityGate`), o LLM já
  teria "inventado" uma resposta sem fonte antes de ser descartada, desperdiçando
  custo/latência e criando superfície para o texto descartado vazar por engano. Curto
  -circuitar ANTES é mais barato e mais seguro.
- **Reuso do bloco canônico**: em vez de criar um novo texto de "não tenho essa
  informação", reusa-se `_fallback_indisponivel_response()` (já existe, já traduzido
  PT/EN/ES, já aciona o mesmo handoff) — FR-006 exige "o MESMO mecanismo de
  encaminhamento... nenhum canal novo".

## Decision 8 — Rastreabilidade (`fonte_ids`, FR-011, FR-012, US4)

- **Decisão**: `fonte_ids` NÃO é adicionado ao schema Pydantic `RespostaEstruturada`
  enviado à OpenAI via `response_format=json_schema` (o campo `fontes: list[str]`
  existente, de texto livre preenchido pelo próprio LLM, permanece INALTERADO — zero
  risco de quebrar o contrato/schema já testado da Onda 2). Em vez disso,
  `GroundedResponder` ganha um novo atributo de instância `last_fonte_ids:
  Optional[list[str]]` (mesmo padrão aditivo de `last_fidelidade_fiel`/
  `last_fidelidade_afirmacoes_nao_sustentadas` — `responder.py:213-214`), resetado a
  cada turno e populado DETERMINISTICAMENTE por `generate()` a partir dos
  `chunk.id` efetivamente incluídos no `knowledge_context` daquele turno (não do que
  o LLM alega ter usado). `log_turno` (observabilidade da Onda 1) passa a registrar
  `last_fonte_ids` de forma aditiva, o mesmo caminho já usado para o veredito de
  fidelidade (Onda 2, task 4.3).
- **Por quê**: FR-012 exige que o contrato/portão validem contra "as MESMAS unidades
  de conteúdo recuperadas... nunca um conjunto mais amplo ou diferente" — a única
  forma de garantir isso com certeza é o ORQUESTRADOR (que já sabe exatamente quais
  chunks entraram no prompt) anexar os IDs, em vez de pedir ao LLM para "lembrar" e
  reportar IDs — que arriscaria invenção/omissão (o mesmo raciocínio anti-alucinação
  que já rege o resto da feature). `FidelityGate.verificar()` já recebe o mesmo
  `knowledge_context` que gerou a resposta (Decision do research.md da Onda 2) — como
  esse `knowledge_context` agora É formado pelos chunks recuperados, o portão
  automaticamente valida contra as MESMAS unidades (FR-012 satisfeito sem mudança na
  assinatura de `FidelityGate`).
- **Alternativa descartada**: adicionar `fonte_ids: list[str]` ao schema
  `RespostaEstruturada` pedindo ao LLM para preenchê-lo — descartada porque (a)
  arrisca o LLM inventar/errar IDs (viola Princípio II Anti-Alucinação Rígida), (b)
  altera o schema JSON já validado/testado da Onda 2 sem necessidade.

## Decision 9 — Idempotência da preparação de conteúdo (FR-009, FR-010, FR-023-INFRA-IDEMP)

- **Decisão**: novo módulo `app/rag_seed.py` (`run_rag_seed(db_session,
  openai_client)`), chamado em `app/main.py:lifespan` DEPOIS de `run_seed` (mesmo
  bloco `try/except` não-fatal — Decision 0), com o MESMO padrão de idempotência já
  usado por `app/seed.py`:
  1. Upsert de `chunk` a partir de `CursoObjecao`/`Faq` ativos via
     `INSERT ... ON CONFLICT (fonte_tabela, fonte_id, idioma) DO UPDATE SET
     conteudo = EXCLUDED.conteudo, atualizado_em = now() WHERE chunk.conteudo IS
     DISTINCT FROM EXCLUDED.conteudo` (retorna só as linhas cujo conteúdo REALMENTE
     mudou — via `RETURNING id`).
  2. Só as linhas retornadas (novas OU com conteúdo alterado OU `embedding IS NULL`)
     são reenviadas a `OpenAIClient.embed()` em lote — a representação semântica
     NUNCA é recalculada a cada boot para conteúdo inalterado (FR-009), e a
     re-execução nunca duplica unidades (FR-010/FR-023-INFRA-IDEMP — mesma garantia
     `UNIQUE(fonte_tabela, fonte_id, idioma)` de Decision 3).
  3. Curadoria de `tipo='base'` (Decision 2) é upsert direto via a API de admin (o
     operador já fornece o `conteudo` final) — sem necessidade de derivação, mesma
     tabela/mesmas garantias de unicidade.
- **Por quê "revisável" mesmo sem um arquivo estático tipo `faq_i18n.json`**: o
  conteúdo-fonte (`CursoObjecao`/`Faq`) já é 100% curado/revisável via a API de admin
  existente (mesmo mecanismo que rege hoje o catálogo de cursos) — a tabela `chunk`
  é um ÍNDICE DERIVADO e reconstruível dessas fontes (proveniência rastreável via
  `fonte_tabela`/`fonte_id`), análogo em espírito a `faq_i18n.json` ser um arquivo
  revisável versionado: aqui a revisão acontece via os mesmos endpoints/auditoria de
  admin já existentes, não via diff de arquivo.

## Decision 10 — Preservação das Ondas 1 e 2 (RESTRIÇÃO INVIOLÁVEL)

- **Decisão**: nenhuma alteração destrutiva em `log_turno`/observabilidade,
  contadores/nudge/handoff de sessão, reengajamento, debounce recovery, lock TTL
  (`config.py`), `max_msgs_per_turn=4`, `_Pacer`+429, idempotência, gate IA=77,
  debounce 8s, anti-loop `_MAX_TENTATIVAS=3` (Onda 1); contrato JSON
  `RespostaEstruturada`, `FidelityGate`, `SlotExtractor` (Onda 2) — todos preservados
  intactos. A recuperação híbrida troca APENAS a fonte do `knowledge_context` que já
  alimentava `GroundedResponder.generate()`/`FidelityGate.verificar()` (FR-015,
  FR-016, FR-017) — nenhuma assinatura pública muda (a 2-tupla `(texto, handoff)` de
  `generate()` permanece intacta, FR-006 da Onda 2).

## Decision 11 — Idioma sem fallback cross-idioma no RAG (Q5/dec-fase-1, ressalva)

- **Decisão**: DIFERENTE do comportamento hoje existente de `_load_faq`/
  `_scalar_idioma` (que fazem fallback para PT quando não há conteúdo no idioma do
  lead), a recuperação híbrida (chunks `objecao`/`faq`/`base`) NÃO faz fallback
  cross-idioma: ausência de unidade de conteúdo no idioma do lead é tratada como
  ausência de fonte relevante → abstenção + handoff (mesmo caminho de FR-005/FR-006).
  O comportamento de fallback-PT das seções verbatim (Apresentação/Turmas/Link, fora
  do RAG — Decision 1) permanece EXATAMENTE como está hoje, sem mudança.
- **Por quê**: Princípio II (Anti-Alucinação Rígida) não abre exceção de idioma;
  FR-002 exige pré-filtro por produto E idioma antes da relevância — misturar
  idiomas na mesma resposta é proibido por FR-013. Ratificado na clarify (Q5, score
  3).

## Decision 12 — Testes (RESTRIÇÃO INVIOLÁVEL)

- **Decisão**: FlowEngine REAL nos testes (mock só de `OpenAIClient`, incluindo o
  novo método `embed()` — nunca do motor). Suíte verde + `ruff` limpo ao final da
  execução. Golden set estendido para groundedness/abstenção
  (`tests/golden/`, `@pytest.mark.golden`), fora do CI padrão — casos onde a resposta
  correta é uma abstenção (US2 Independent Test) e casos onde a resposta deve
  refletir um único chunk específico (US1 Independent Test). Novos envs (Decision 5)
  validados por teste de config (mesmo padrão da Onda 1/2).
