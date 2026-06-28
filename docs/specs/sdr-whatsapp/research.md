# Research — Agente SDR WhatsApp (GoldIncision)

**Feature**: `sdr-whatsapp` | **Fase**: Phase 0 (resolução de unknowns)
**Spec**: [spec.md](./spec.md)

Cada decisão resolve um unknown do Technical Context ou uma escolha de
biblioteca/abordagem. Formato: **Decision / Rationale / Alternatives considered**.

---

## Decision 1 — Linguagem e framework: Python 3.12 + FastAPI

**Decision**: Python 3.12 com FastAPI (ASGI, Uvicorn worker) espelhando a stack
de referência `fast-api-homologacao`.

**Rationale**: O briefing fixa "Python + FastAPI (alinhado a
fast-api-homologacao)". FastAPI dá validação via Pydantic (essencial para o
payload heterogêneo do webhook), suporte assíncrono nativo (chamadas concorrentes
ao OpenAI + ChatMaster + Postgres + Redis), OpenAPI automático (alinha com os
contratos REST de admin) e healthcheck trivial. Espelhar a stack de referência
reduz risco de deploy (mesmo padrão de Dockerfile/labels Traefik já validado em
produção).

**Alternatives considered**:
- Node.js/NestJS: rejeitado — diverge da stack de referência; o operador já opera
  FastAPI em produção (`fast-api-homologacao`).
- Flask: rejeitado — sem async nativo de primeira classe; o fluxo tem I/O
  concorrente intenso (LLM + APIs externas) onde async importa.

---

## Decision 2 — Persistência: Postgres próprio (histórico/catálogo/funil) + Redis próprio (debounce/sessão/lock)

**Decision**: Dois data stores próprios da stack, em rede overlay isolada:
- **PostgreSQL 16**: catálogo de cursos (cursos, turmas, objeções, apresentações
  por idioma), contatos e variáveis de qualificação, histórico completo de
  mensagens, resumo rolante, estado de ticket/funil persistido.
- **Redis 7**: janela curta de debounce (buffer de rajada por ticket), cache de
  sessão (estado de fluxo corrente em quente), locks de serialização por ticket
  (FR-035-INFRA-MUTEX) e chave de idempotência por evento (FR-037-INFRA-IDEMP,
  TTL 24h).

**Rationale**: A constitution (Princípio III) e o briefing exigem histórico
durável (Postgres) + resumo rolante + cache/janela curta (Redis), com isolamento
absoluto dos stores compartilhados (Princípio VI). Postgres é a fonte da verdade
durável; Redis é volátil/efêmero (debounce de 8s, locks de TTL curto, idempotência
de 24h). Separar responsabilidades evita usar Redis como armazenamento durável
(perda em restart) e evita usar Postgres no caminho quente de debounce (latência).

**Alternatives considered**:
- Só Postgres (sem Redis): rejeitado — debounce e lock de rajada de alta
  frequência sobrecarregariam o Postgres; briefing pede Redis explícito.
- Reusar Postgres/Redis compartilhados do host: PROIBIDO pela constitution
  (Princípio VI — isolamento absoluto) e pelo briefing.

---

## Decision 3 — Acesso a dados: SQLAlchemy 2.0 (async) + Alembic; redis.asyncio

**Decision**: SQLAlchemy 2.0 estilo async (`asyncpg` driver) como ORM/mapper e
Alembic para migrations versionadas. Cliente Redis via `redis.asyncio`.

**Rationale**: ORM maduro com mapper explícito DB↔DTO (necessário para a
convenção snake_case no DB vs camelCase nos DTOs — ver §Convenções de Borda no
plan). Alembic dá migrations rastreáveis e seed reprodutível (FR-027). `asyncpg`
é o driver async mais rápido. `redis.asyncio` é o cliente oficial async.

**Alternatives considered**:
- SQL puro (asyncpg sem ORM): rejeitado — catálogo de cursos tem relacionamentos
  ricos (curso↔turmas↔objeções↔apresentações por idioma); ORM reduz boilerplate
  e dá o mapper layer explícito que a §Convenções de Borda exige.
- Tortoise ORM / SQLModel: rejeitado — SQLAlchemy é o padrão consolidado com
  melhor suporte async e ecossistema Alembic.

---

## Decision 4 — Estratégia LLM: dois modelos OpenAI (raciocínio + barato) com roteamento por tarefa

**Decision**: Duas faixas de modelo OpenAI, configuráveis por variável de
ambiente:
- **Modelo de raciocínio** (default `gpt-4.1` / classe equivalente de raciocínio):
  conduz o fluxo conversacional, geração da resposta final ao lead.
- **Modelo barato** (default `gpt-4.1-mini` / classe econômica): classificação de
  intenção (qual dos 6 caminhos), detecção de idioma e sumarização rolante.
- **Transcrição de áudio**: endpoint de transcrição OpenAI (Whisper / `gpt-4o-
  transcribe`). Chave única `OPENAI_API_KEY` via secret.

**Rationale**: O briefing exige explicitamente "modelo de raciocínio para o fluxo
+ modelo barato para classificação/sumarização" para controlar custo (Princípio
III — sem estourar custo do LLM). Roteamento por tarefa minimiza tokens caros:
classificação/sumarização/detecção de idioma são tarefas baratas de alto volume.
Modelos parametrizados por env permitem o operador trocar sem redeploy de código.

**Alternatives considered**:
- Modelo único caro para tudo: rejeitado — custo desnecessário em classificação/
  sumarização (briefing pede separação).
- Embeddings + RAG vetorial para a Base Oficial: considerado, mas a Base Oficial
  é pequena e estruturada (handful de documentos por curso). Decisão 7 adota
  recuperação estruturada por curso (catálogo) + injeção de texto oficial na
  íntegra no prompt, mais simples e 100% fiel (anti-alucinação) que RAG por chunks.

---

## Decision 5 — Anti-alucinação: prompt grounding estrito + hierarquia de fonte + guarda de cobertura

**Decision**: O motor NUNCA gera conteúdo livre sobre o produto. O prompt do
modelo de raciocínio recebe SOMENTE: (a) o trecho do Mapa Mestre da etapa
corrente, (b) a apresentação oficial do produto em atendimento (texto íntegro, no
idioma), (c) o banco de objeções do produto, (d) FAQ — nessa hierarquia fixa
(Princípio II). Apresentações são enviadas na íntegra, sem reescrita (FR-010).
Quando o lead pede algo fora da Base Oficial, o modelo é instruído a responder
"não possuo essa informação" + handoff (FR-008, SC-008). Guarda de cobertura:
respostas a objeção só podem citar entradas do banco de objeções carregado.

**Rationale**: Constitution Princípio II é MUST e inflexível. Injetar texto
oficial verbatim + instruir recusa explícita fora da base é a abordagem mais
robusta contra alucinação. Manter apresentações como blobs íntegros (não
reescritos pelo LLM) garante FR-010.

**Alternatives considered**:
- Deixar o LLM parafrasear apresentações: PROIBIDO (FR-010, Princípio II — texto
  íntegro).
- Fine-tuning com os documentos: rejeitado — caro, opaco, e não garante
  fidelidade verbatim; viola "cursos como dados" (catálogo muda sem redeploy).

---

## Decision 6 — Webhook: idempotência + debounce + serialização por ticket

**Decision**: Pipeline inbound em 4 estágios:
1. **Idempotência** (FR-037): chave Redis `idemp:{chamadoId}:{sha256(conteúdo)}`
   com TTL 24h; reenvio do n8n com mesmo conteúdo é descartado.
2. **Filtro** (FR-002, edge cases): descarta `fromMe:true`, tipos desconhecidos,
   e tickets já em handoff humano (consulta estado do ticket).
3. **Debounce** (FR-003): append da mensagem ao buffer Redis `debounce:{chamadoId}`
   e agenda processamento após janela configurável (default 8s); rajada vira
   uma única entrada consolidada (SC-005).
4. **Serialização** (FR-035): lock Redis `lock:ticket:{chamadoId}` (SET NX PX)
   garante processamento único por ticket, evitando resposta duplicada em rajada
   alta.

O webhook responde 200 imediatamente (ack rápido ao n8n) e processa em background
task; o trabalho pesado (LLM, envio) ocorre após a janela de debounce.

**Rationale**: O ChatMaster/n8n pode reenviar; o lead envia rajadas. Idempotência
+ debounce + lock cobrem FR-003, FR-035, FR-037 e os edge cases de rajada e
reenvio. Ack 200 rápido evita timeout/retry do n8n por demora de processamento.

**Alternatives considered**:
- Fila externa (Celery/RabbitMQ): rejeitado — adiciona 4º serviço à stack
  (viola simplicidade/isolamento); o volume e a latência toleram background tasks
  do FastAPI + agendamento via Redis. Reavaliar só se escala exigir.
- Processar síncrono no request: rejeitado — debounce exige espera; bloquear o
  request causaria timeout no n8n.

---

## Decision 7 — Catálogo como dados: tabelas Postgres + seed dos documentos oficiais

**Decision**: Cursos, turmas, apresentações (por idioma), bancos de objeções (por
idioma), regras de elegibilidade e links são linhas em Postgres, geridos pela API
de admin (FR-025). O motor lê o catálogo em runtime (FR-026). Seed inicial
(FR-027) é um script idempotente que popula os 6 produtos oficiais a partir do
texto extraído de `knowledge_base/documentos_agente/` (já presentes no repo como
.docx/.pdf). A extração do texto oficial dos .docx/.pdf para seed é feita uma vez
no build do seed (texto verbatim persistido na coluna de apresentação).

**Rationale**: "Cursos como dados" é Princípio VII (MUST). Persistir o texto
oficial verbatim no DB permite adicionar/remover curso sem redeploy e mantém a
fidelidade (o LLM lê do DB, não improvisa). Seed idempotente garante boot
reprodutível (SC-004, FR-027).

**Alternatives considered**:
- Cursos hardcoded em código/JSON no repo: PROIBIDO (Princípio VII — exige CRUD
  sem redeploy).
- Ler os .docx/.pdf em runtime a cada conversa: rejeitado — parsing repetido,
  lento e frágil; melhor extrair uma vez no seed e servir texto do DB.

---

## Decision 8 — Memória: histórico durável + resumo rolante + janela quente

**Decision**: Por ticket/contato:
- **Histórico completo** em Postgres (tabela de mensagens, append-only).
- **Resumo rolante** em Postgres (coluna texto), atualizado pelo modelo barato
  quando o histórico recente passa de um limiar de tokens (default ~3000): o
  resumo é re-sintetizado preservando a linha narrativa e as variáveis.
- **Variáveis de qualificação** persistidas por contato (idioma, é médico?,
  especialidade, experiência corporal, produto, etapa do funil) — FR-020.
- **Janela quente** em Redis (últimas N mensagens) para latência baixa.

O prompt do LLM recebe: resumo rolante + janela quente + variáveis. Nunca repete
perguntas já respondidas porque as variáveis já capturadas são injetadas e o
prompt é instruído a não re-perguntar (FR-021, SC-002).

**Rationale**: Cobre janelas longas (SC-002, 50+ mensagens) sem estourar
contexto/custo (Princípio III). Postgres garante durabilidade entre sessões
(FR-018); resumo rolante controla tokens; variáveis persistidas evitam
requalificação (FR-021).

**Alternatives considered**:
- Enviar histórico completo ao LLM: rejeitado — estoura contexto e custo em
  conversas longas (viola Princípio III).
- Só resumo (sem histórico completo durável): rejeitado — FR-018 exige histórico
  completo persistido (auditoria/reprocessamento).

---

## Decision 9 — Empacotamento: Dockerfile multi-stage + stack.yml Swarm isolado, build+push (sem deploy live)

**Decision**: Dockerfile multi-stage (builder com deps + runtime slim, usuário
não-root, healthcheck HTTP). `stack.yml` Swarm com 3 serviços (`app`, `postgres`,
`redis`) em rede overlay própria `sdr-whatsapp_net`; apenas `app` também ingressa
na rede do Traefik (nome a CONFIRMAR via `docker network inspect`/inspeção do
serviço `traefik_traefik` — provável `network_main`). Labels Traefik
(host/router/service/port), secrets Docker (`OPENAI_API_KEY`, token ChatMaster,
token admin), volumes nomeados para Postgres/Redis. Pipeline vai até `docker
build` + `docker push registry.todo-tips.com/sdr-whatsapp:latest` + geração do
`stack.yml`. **NÃO** executa `docker stack deploy` (FR-031, Princípio VI).

**Rationale**: Espelha `fast-api-homologacao` (padrão validado). Isolamento por
rede overlay própria + ingressar só na rede Traefik atende Princípio VI e FR-028/
FR-029. Secrets fora do `stack.yml`/git atendem FR-032. Parar em build+push atende
FR-031 (deploy live é do operador).

**Alternatives considered**:
- Docker Compose (não-Swarm): rejeitado — alvo é Swarm; Traefik + secrets + overlay
  são padrões Swarm.
- Expor Postgres/Redis na rede do Traefik: PROIBIDO (FR-029 — stores não expostos
  a redes externas).
- Executar deploy live: PROIBIDO (FR-031).

---

## Decision 10 — Nome da rede do Traefik: CONFIRMAR por inspeção (não assumir)

**Decision**: O `stack.yml` referencia a rede externa do Traefik como
`network_main` (provável, conforme briefing), porém o valor DEVE ser confirmado na
fase de implementação inspecionando o serviço `traefik_traefik`
(`docker service inspect traefik_traefik` / `docker network ls`). Marcado como
parâmetro confirmável no plano, não como fato.

**Rationale**: O briefing diz "provável network_main — a confirmar na fase de
plan via inspeção". Assumir nome errado quebra o roteamento. Como o ambiente de
execução desta onda não tem deploy live nem acesso garantido ao Swarm runtime, a
confirmação fica como passo explícito de uma task de implementação (não um
NEEDS CLARIFICATION de design — a abordagem está resolvida; só o literal do nome
é confirmável em runtime).

**Alternatives considered**:
- Hardcode `network_main` sem confirmação: rejeitado — risco de roteamento quebrado.

---

## Decision 11 — Identificação de tipos de mídia inbound (text/audio/video/image/document)

**Decision**: O parser do webhook usa o campo `mediaType` de cada item de
`mensagem[]` (visto em `json_audio` = `"audio"`, `json_document/video` =
`"video"`/etc.). `text` → texto direto (`text`); `audio` → baixar `mediaUrl`
(host `object.sp2.eveo.com.br`) e transcrever (Decisão 4); `video`/`image`/
`document` → reconhecidos mas não processados como conteúdo binário — o agente
pede descrição em texto quando necessário (FR-004, US5-AS6). Tipo fora do conjunto
conhecido → descartado silenciosamente com log de aviso (edge case).

**Rationale**: Os exemplos reais em `knowledge_base/example_webhook_json/`
mostram o shape (`mediaType`, `mediaUrl`, `remoteUrl`). Tratar só o que o briefing
pede (transcrever áudio; reconhecer os demais) evita complexidade de OCR/visão
fora de escopo.

**Alternatives considered**:
- Processar imagem/documento com visão/OCR: fora de escopo (briefing P2 limita a
  transcrição de áudio); rejeitado.

---

## Decision 12 — Testing: pytest + pytest-asyncio; Postgres/Redis efêmeros via testcontainers; LLM e ChatMaster mockados, com roundtrip real opcional

**Decision**: `pytest` + `pytest-asyncio`. Testes de integração usam Postgres/
Redis efêmeros (testcontainers ou serviços de CI). Chamadas OpenAI e ChatMaster
são mockadas por padrão; o quickstart inclui um cenário roundtrip end-to-end real
contra o webhook local (FastAPI rodando) para validar o shape do payload inbound
contra o contrato (evita drift de contrato — ver quickstart §Roundtrip).

**Rationale**: Padrão Python consolidado. Mock de APIs externas (OpenAI/ChatMaster)
evita custo e dependência de rede nos testes unitários; roundtrip real local
valida o parsing do webhook (a fronteira mais frágil) contra os exemplos reais.

**Alternatives considered**:
- unittest puro: rejeitado — pytest tem melhor ergonomia async e fixtures.
- Testar contra ChatMaster real: rejeitado — efeitos colaterais (envia mensagem a
  leads); apenas em validação manual controlada pelo operador.

---

## Unknowns remanescentes

**0 NEEDS CLARIFICATION de design.** O único valor confirmável-em-runtime é o
nome literal da rede externa do Traefik (Decisão 10), tratado como passo de
implementação com inspeção, não como lacuna de design. Versões/IDs exatos de
modelos OpenAI são parametrizados por env (Decisão 4) e ajustáveis pelo operador
sem redeploy de código.
