# Requirements Checklist: Recuperação Híbrida Ancorada com Abstenção (Onda 3)

**Purpose**: Validar a qualidade dos requisitos de `spec.md`/`plan.md` da feature
`sdr-rag-hibrido` antes de `create-tasks` — cobertura de FR-001..FR-024-INFRA
-PRECONDITION, RESTRIÇÕES INVIOLÁVEIS e dos findings OWASP dec-020.
**Created**: 2026-07-01
**Feature**: `docs/specs/sdr-rag-hibrido/spec.md`

## Completude de Requisitos

- [x] CHK001 - Está definido o pré-filtro obrigatório (produto + idioma) que deve rodar ANTES de qualquer ranqueamento por relevância? [Completude, Spec §FR-002] {auto}
- [x] CHK002 - Está definida a combinação obrigatória de correspondência semântica e por termos exatos numa única ordem? [Completude, Spec §FR-003] {auto}
- [x] CHK003 - Está definido o limite do conjunto final de conteúdo usado no grounding (não o conjunto bruto de candidatos)? [Completude, Spec §FR-004, Plan §RAG_TOP_K] {auto}
- [x] CHK004 - O comportamento de abstenção abaixo do patamar mínimo de relevância está especificado, incluindo a mensagem padrão e o mecanismo de encaminhamento reusado? [Completude, Spec §FR-005, FR-006] {auto}
- [x] CHK005 - Está definida a unidade de significado (objeção/FAQ/seção de base) e sua origem exclusiva nos documentos oficiais já curados? [Completude, Spec §FR-007, FR-008] {auto}
- [x] CHK006 - Está definido que a representação semântica é calculada uma única vez e nunca recalculada a cada boot? [Completude, Spec §FR-009] {auto}
- [x] CHK007 - Está definido o comportamento de indisponibilidade do mecanismo de recuperação (== ausência de fonte, nunca fallback ao dump bruto)? [Completude, Spec §FR-021] {auto}
- [ ] CHK008 - Está definido um mecanismo concreto de calibração do `RAG_LIMIAR_ABSTENCAO` ao longo do tempo (quem revisa, com que frequência, contra qual conjunto), ou fica em aberto até haver dados reais de produção? [Gap, Spec §FR-022, US4] {humano}
- [x] CHK009 - A pré-condição de infraestrutura (pgvector não habilitado hoje) está formalizada como requisito funcional numerado, e não apenas como nota de rodapé? [Completude, Spec §FR-024-INFRA-PRECONDITION] {auto}

## Clareza de Requisitos

- [x] CHK010 - O patamar mínimo de relevância (`LIMIAR`) é um valor numérico único configurável (`0.45`), não uma faixa vaga? [Clareza, Spec §FR-005, Plan §Envs-novos] {auto}
- [x] CHK011 - A quantidade de candidatos avaliados (k) e o tamanho do conjunto final (top-k) são valores concretos, não "alguns"/"poucos"? [Clareza, Spec §FR-004, Plan §RAG_K_VETORIAL/RAG_K_TEXTUAL/RAG_TOP_K] {auto}
- [x] CHK012 - O que conta como "seção coerente de documento de base" está ancorado numa via concreta de curadoria (API de admin), não em heurística automática subjetiva? [Clareza, Spec §Clarifications Q2] {auto}
- [x] CHK013 - O comportamento de reserva quando não existe conteúdo no idioma do lead está explicitamente definido como abstenção (não fallback cross-idioma), inclusive a ressalva sobre o comportamento atual de `_load_faq` ser diferente? [Clareza, Spec §Clarifications Q5, Research Decision 11] {auto}
- [ ] CHK014 - O peso da combinação `score_combinado = 0.6*vetorial + 0.4*textual` (Research Decision 4) está justificado com dado empírico, ou é um ponto de partida arbitrário sujeito a calibração futura junto do LIMIAR? [Ambiguity, Research §Decision-4] {humano}

## Consistência de Requisitos

- [x] CHK015 - A exceção de verbatim (apresentação/turmas/link fora do RAG, FR-014) é consistente entre spec, research (Decision 1) e o mapeamento FR→arquivo do plan (`_load_knowledge_by_slug` inalterado nessas 3 seções)? [Consistência, Spec §FR-014, Plan §Mapeamento-FR] {auto}
- [x] CHK016 - O requisito de que o contrato/portão validam contra as MESMAS unidades recuperadas (FR-012) é consistente com o desenho de `last_fonte_ids` (Research Decision 8, Data-Model §3) — nenhum conjunto mais amplo é passado ao `FidelityGate`? [Consistência, Spec §FR-012] {auto}
- [x] CHK017 - A preservação da máquina de estados determinística (FR-015) é consistente com o ponto de curto-circuito de abstenção ficar DENTRO da etapa de dúvidas já existente, sem nova transição? [Consistência, Spec §FR-015, Research §Decision-7] {auto}
- [x] CHK018 - A preservação das Ondas 1 e 2 (FR-016, FR-017) é consistente com a marcação de "todas as alterações são aditivas" no plano? [Consistência, Spec §FR-016, FR-017, Plan §Restrições] {auto}
- [x] CHK019 - O padrão de idempotência exigido (FR-023-INFRA-IDEMP) é consistente com o mecanismo de upsert condicional `ON CONFLICT ... WHERE conteudo IS DISTINCT FROM` descrito no research (Decision 9) e no data-model (§1)? [Consistência, Spec §FR-023-INFRA-IDEMP] {auto}

## Qualidade de Critérios de Aceite (Success Criteria)

- [x] CHK020 - SC-001 define um piso numérico (>=95%) medido contra um conjunto de casos de referência concreto, não "a maioria das perguntas"? [Mensurabilidade, Spec §SC-001] {auto}
- [x] CHK021 - SC-002 é objetivamente verificável (100% de abstenção quando não há fonte, zero conteúdo inventado)? [Mensurabilidade, Spec §SC-002] {auto}
- [x] CHK022 - SC-003 é mensurável como contagem zero de vazamento cross-produto em conjunto de referência com produtos semelhantes? [Mensurabilidade, Spec §SC-003] {auto}
- [ ] CHK023 - SC-004 ("tempo de resposta não aumenta de forma perceptível... comparado à linha de base anterior") define a metodologia/fonte concreta de medição da linha de base, ou fica em aberto até execução (mesmo gap já identificado na Onda 2, CHK022)? [Ambiguity, Spec §SC-004] {humano}
- [x] CHK024 - SC-005 amarra rastreabilidade a um mecanismo concreto (`last_fonte_ids` + `log_turno`), não a "auditoria manual"? [Mensurabilidade, Spec §SC-005, Data-Model §3] {auto}

## Cobertura de Cenários / Edge Cases

- [x] CHK025 - Está coberto o cenário de pergunta que toca dois produtos simultaneamente (comparação entre cursos)? [Cobertura, Spec §Edge-Cases] {auto}
- [x] CHK026 - Está coberto o cenário de atualização de conteúdo oficial (nova objeção/FAQ revisado) refletir sem reprocessar tudo e sem duplicar? [Cobertura, Spec §Edge-Cases, FR-010] {auto}
- [x] CHK027 - Está coberto o cenário de pergunta repetida na mesma conversa (reaproveitamento opcional do resultado de busca, FR-019, cache desligado por padrão)? [Cobertura, Spec §Edge-Cases, FR-019] {auto}
- [x] CHK028 - Está coberto o cenário de ausência total de conteúdo cadastrado no idioma do lead (comportamento de reserva claro, sem falha visível)? [Cobertura, Spec §Edge-Cases, FR-013] {auto}
- [x] CHK029 - Está coberto o cenário de mudança de versão de documento oficial durante uma conversa em andamento (próxima pergunta já reflete o novo conteúdo, sem exigir compatibilidade retroativa)? [Cobertura, Spec §Edge-Cases] {auto}
- [x] CHK030 - Está coberto o cenário de o mecanismo de busca falhar ou não responder a tempo (mesmo tratamento de ausência de fonte, FR-021, Research Decision 6)? [Cobertura, Spec §US2-AS2, FR-021] {auto}
- [ ] CHK031 - Está coberto o cenário de o Postgres de produção ainda não ter pgvector habilitado no momento do deploy desta feature (boot não quebra, RAG fica inerte/sempre abstém)? [Gap→Coberto, Spec §FR-024-INFRA-PRECONDITION, Research §Decision-0] {auto} — coberto explicitamente via try/except não-fatal já existente em `app/main.py:102-118`; validar com teste de integração dedicado em `create-tasks`.

## Requisitos Não-Funcionais (Segurança / Observabilidade)

- [x] CHK032 - Existe requisito de que a mensagem do lead nunca é tratada como instrução na consulta de busca (embedding/tsquery), preservando SEC-LLM-1 (LLM01)? [NFR-Segurança, Plan §OWASP] {auto}
- [x] CHK033 - Existe requisito de que o índice vetorial só é alimentado por conteúdo admin-gated (sincronização de `CursoObjecao`/`Faq` ou `/admin/chunks` autenticado), sem ingestão de conteúdo de lead/usuário final (LLM08 RAG poisoning)? [NFR-Segurança, Plan §OWASP-Findings] {auto}
- [ ] CHK034 - O novo endpoint `/admin/chunks` tem requisito explícito de rejeitar `fonte_tabela`/`fonte_id`/`embedding`/`ativo` client-supplied e restringir `tipo='base'` (finding dec-020 #1, API3 BOPLA)? [Gap, Plan §OWASP-Findings #1] {auto} — **NÃO SATISFEITO NA SPEC como FR numerado**: existe como finding de gate (dec-020) incorporado ao `plan.md`, não como FR. Vira task obrigatória explícita em `create-tasks` (mesmo padrão do CHK033 da Onda 2).
- [ ] CHK035 - Existe requisito de limite de tamanho de `conteudo` e de lote de embeddings para mitigar custo/DoS via admin ou reprocessamento (finding dec-020 #2, API4/LLM10)? [Gap, Plan §OWASP-Findings #2] {auto} — mesma situação do CHK034: existe como finding de gate, vira task obrigatória em `create-tasks`.
- [x] CHK036 - Existe requisito de que queries de busca vetorial/textual usam parâmetros bindados (nunca concatenação de string com input do lead)? [NFR-Segurança, Plan §OWASP A03] {auto}
- [x] CHK037 - Existe requisito de que a extensão pgvector e a troca de imagem do Postgres ficam confinadas ao próprio serviço `sdr-whatsapp_postgres`, sem tocar outros serviços/stacks? [NFR-Segurança, Spec §FR-024-INFRA-PRECONDITION] {auto}
- [x] CHK038 - Existe requisito de que `fonte_ids`/observabilidade nova não introduz PII (lista de IDs numéricos, mesmo padrão do veredito booleano de fidelidade da Onda 2)? [NFR-Segurança, Plan §OWASP LLM06] {auto}

## Dependências e Premissas

- [x] CHK039 - O plano ancora cada FR em referência real de código (arquivo:linha) — `flow.py:1641/1830/2046`, `responder.py:213-214`, `fidelity.py`, `seed.py`? [Dependência, Plan §Mapeamento-FR] {auto}
- [x] CHK040 - Os 7 envs novos (`RAG_EMBEDDING_MODEL`, `RAG_LIMIAR_ABSTENCAO`, `RAG_K_VETORIAL`, `RAG_K_TEXTUAL`, `RAG_TOP_K`, `RAG_RETRIEVAL_TIMEOUT_SECONDS`, `RAG_CACHE_ENABLED`) têm default explícito e local de declaração (config/stack.yml/.env.example)? [Dependência, Plan §Envs-novos] {auto}
- [x] CHK041 - A única dependência Python nova (`pgvector`) tem plano de pin de versão (finding dec-020 #3), consistente com o padrão dos demais deps em `pyproject.toml`? [Dependência, Plan §OWASP-Findings #3] {auto}
- [x] CHK042 - A estratégia de testes assume FlowEngine real (mock só de `OpenAIClient`, incluindo `embed()`), consistente com o padrão das Ondas 1/2? [Dependência, Plan §Estratégia-de-testes] {auto}
- [x] CHK043 - A pré-condição de infraestrutura (swap de imagem do Postgres) está explicitamente marcada como ação do OPERADOR fora do escopo de código desta feature, e não como task de `create-tasks`? [Dependência, Plan §Pré-condição] {auto}

## Notes

- Items `{auto}` já vêm resolvidos pelo agente (`[x]` com citação, ou marcador
  `[Gap]`/`[Ambiguity]`).
- Items `{humano}` ficam `[ ]` aguardando decisão do dono do produto — não bloqueiam
  `create-tasks`.
- **5 itens em aberto**: CHK008, CHK014, CHK023 (`{humano}` — julgamento de
  produto/calibração empírica, não bloqueiam `create-tasks`) e CHK034/CHK035
  (`{auto}` com `[Gap]` real — **viram tasks obrigatórias** por já estarem cobertas
  por Decisão auditável dec-020, mesmo padrão do CHK033 herdado da Onda 2).
- Nenhum `[Conflict]` encontrado entre FR-001..FR-024-INFRA-PRECONDITION e as
  RESTRIÇÕES INVIOLÁVEIS.
