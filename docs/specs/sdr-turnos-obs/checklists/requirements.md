# Requirements Checklist: Controle de Turnos Robusto e Observabilidade de Turno

**Purpose**: Quality gate de requisitos (spec.md + plan.md + research.md +
data-model.md) antes de `/create-tasks`. Foco solicitado: mensurabilidade dos
limiares (FR-003/004/005/007), cobertura de edge cases de concorrência
(US3/US4), completude do registro de observabilidade (FR-015/016) e
preservação de mecanismos existentes (FR-019/020).
**Created**: 2026-07-01
**Feature**: [spec.md](../spec.md) | [plan.md](../plan.md) |
[research.md](../research.md) | [data-model.md](../data-model.md)

## Completude de Requisitos

- [x] CHK001 - Está definido como os contadores de turno se distinguem do
  contador anti-loop existente, sem fundir os dois mecanismos?
  [Completude, Spec §FR-001, Spec Acceptance Scenario 5 US1] {auto}
  Evidência: FR-001 exige contagem "de forma independente do contador
  existente de respostas não-reconhecidas"; Acceptance Scenario 5 (US1)
  testa explicitamente que os dois "não se fundem nem se substituem";
  Decision 3 (research.md) documenta a distinção operacional.
- [x] CHK002 - Está definido o que acontece quando os contadores de turno
  são perdidos (Redis reiniciado sem persistência)? [Completude, Spec
  Edge Cases] {auto} Evidência: Edge Cases item 2 define "tratar como
  novo início de contagem — não bloquear o atendimento; perda de contador
  não é falha crítica, é degradação aceita"; Decision 2(c) (research.md)
  detalha o fail-open via `HGET` ausente ⇒ 0.
- [x] CHK003 - Está definido o comportamento de emissão do registro de
  observabilidade quando o processamento do turno falha antes de
  completar? [Completude, Spec §FR-016, Edge Cases] {auto} Evidência:
  FR-016 exige "registro parcial identificando a falha"; data-model.md
  define o enum `acao` incluindo `erro`; research.md Decision 5 mapeia
  para bloco try/finally.
- [x] CHK004 - Está definido o destino de handoff usado no
  encaminhamento por teto de sessão, e como ele é garantido como
  não-decidido pelo LLM? [Completude, Spec §FR-004, §FR-020] {auto}
  Evidência: FR-004 exige "destino lógico pré-configurado (nunca decidido
  pelo modelo de linguagem)"; FR-020 reitera a invariante; plan.md
  Princípio V confirma `handoff_destino` sempre da allowlist/config.
- [ ] CHK005 - O requisito de preservação de perfil na expiração de sessão
  (FR-010) enumera exaustivamente todos os campos de perfil que não podem
  ser re-perguntados, ou depende de uma lista mantida fora da spec?
  [Completude, Spec §FR-010] {auto} → `[Gap]`: FR-010 lista "elegibilidade
  médica, idioma, especialidade, experiência, interesse" — a spec não
  referencia formalmente o schema de `Contato` como fonte única da
  verdade dessa lista; risco de a lista divergir do modelo real do
  domínio se `Contato` ganhar novo campo de perfil no futuro.
- [ ] CHK006 - O requisito de anti-PII no registro de observabilidade
  (mascaramento de número, ausência de conteúdo bruto da mensagem) está
  formalizado como requisito funcional na spec, ou só aparece em
  research/plan? [Completude, Spec §FR-015, research.md Decision 8]
  {auto} → `[Gap]`: FR-015/FR-016 definem o *shape* do evento mas não
  enunciam explicitamente a restrição "nenhum conteúdo bruto do lead nem
  segredo pode aparecer no evento"; essa restrição de segurança só está
  em research.md Decision 8, não rastreada como FR próprio (ainda que
  herdada implicitamente de FR-020/SEC-LLM-1 e será coberta pelo gate
  `owasp-security` em `/plan`, já executado em onda-001/dec-008).

## Clareza de Requisitos (Mensurabilidade dos Limiares)

- [x] CHK007 - Os limiares de nudge/handoff/dúvidas (FR-003/004/005) são
  quantificáveis e configuráveis, evitando termos vagos como "muitos
  turnos"? [Clareza, Mensurabilidade, Spec §FR-003, §FR-004, §FR-005,
  §FR-007] {auto} Evidência: FR-007 exige explicitamente que os limiares
  "MUST ser configuráveis sem necessidade de alterar código"; data-model.md
  confirma os campos como envs (`MAX_TURNOS_NO_NO`, `MAX_TURNOS_SESSAO`,
  `MAX_TURNOS_DUVIDAS`) sem hardcode na spec — corretamente deferido ao
  plano (separação WHAT/HOW).
- [x] CHK008 - O limiar diferenciado de dúvidas abertas (FR-005) tem
  critério objetivo de precedência definido para quando colide com o
  teto de sessão? [Clareza, Spec Edge Cases, §FR-005] {auto} Evidência:
  Edge Cases item 3 ("O teto de sessão é global e prevalece sobre o
  limiar específico de dúvidas") resolve a ambiguidade explicitamente.
- [x] CHK009 - Os limiares de reengajamento e expiração de sessão
  (FR-009/FR-010) têm relação de ordem explícita (expiração > reengaja-
  mento) que evita configuração inconsistente? [Clareza, Spec §FR-010]
  {auto} Evidência: FR-010 declara textualmente "limiar de expiração de
  sessão configurável (maior que o de reengajamento)".
- [ ] CHK010 - O teto de validade do mecanismo de exclusividade (FR-013)
  tem um valor-alvo mensurável na spec, ou apenas uma condição qualitativa
  ("cobrir a duração observada do pior caso")? [Mensurabilidade, Spec
  §FR-013] {auto} → `[Assumption]`: FR-013 é intencionalmente qualitativo
  na spec (o valor numérico é derivado empiricamente via US5, por design —
  plan.md "Ordem de execução recomendada" item 5 e research.md Decision 4
  documentam 90s como estimativa a confirmar com dados reais, não como
  requisito fixo). Consistente com SC-006, mas o valor final de produção
  fica pendente de validação com dados (SC-010) — não é um gap de spec,
  é uma dependência sequencial explícita.
- [ ] CHK011 - O critério de sucesso do golden set (SC-008) define um
  patamar mínimo de taxa de acerto exigido para considerar o gate
  aprovado, ou apenas exige que a taxa seja reportada? [Mensurabilidade,
  Spec §SC-008] {humano} → `[Gap]`: SC-008 exige que o conjunto "reporta
  taxa de acerto por dimensão avaliada" mas não define um limiar mínimo
  (ex.: ">= 90% por dimensão") que bloqueie o merge; sem esse número, o
  golden set é observacional, não um gate. Decisão de produto: definir
  limiar de aceitação (ou manter deliberadamente informativo, dado que
  spec.md Out of Scope não veta esse formato).

## Consistência de Requisitos

- [x] CHK012 - O plano técnico preserva a decisão da spec de não introduzir
  migration nem entidade durável de Turno? [Consistência, Spec Out of
  Scope, plan.md Storage] {auto} Evidência: spec.md "Out of Scope" exclui
  "Entidade durável de turno para analytics históricos"; plan.md Storage
  confirma "Redis 7 (...); sem Postgres novo" e Complexity Tracking está
  vazio por design.
- [x] CHK013 - A ordem de execução recomendada no plano respeita as
  dependências implícitas da spec (ex.: US4 depender de dados de US5)?
  [Consistência, Spec US4 Acceptance Scenario 2, plan.md §Ordem de
  execução] {auto} Evidência: US4 AC-2 exige que o valor do TTL "seja
  justificado por dados observados, não por suposição"; plan.md ordena
  US5 (observabilidade) antes de US4 (lock) exatamente por esse motivo.
- [x] CHK014 - O mapeamento FR→arquivo no plano cobre 100% dos FRs
  funcionais da spec (nenhum FR órfão sem abordagem técnica)?
  [Consistência, plan.md §Mapeamento FR→arquivos] {auto} Evidência:
  tabela "Mapeamento FR → arquivos → abordagem" em plan.md cobre
  FR-001 a FR-020 e FR-INFRA-01 sem lacunas, incluindo FR-019/FR-020
  (preservação, linha "todos").
- [ ] CHK015 - Os nomes de campos do evento de turno (data-model.md) e os
  nomes de campos exigidos por FR-015 estão em correspondência 1:1 sem
  campo faltante ou renomeado silenciosamente? [Consistência, Spec
  §FR-015, data-model.md §Entity Registro de Turno] {auto} Evidência:
  FR-015 lista 11 campos (identificador do atendimento, número do turno,
  etapa entrada/saída, intenção, idioma, blocos enviados, ação, destino,
  duração, tentativas); data-model.md `turno-event.md`/tabela do Registro
  de Turno contém os 11 campos correspondentes (`chamado_id`,
  `turno_sessao`, `etapa_entrada`, `etapa_saida`, `intencao`, `idioma`,
  `n_blocos_enviados`, `acao`, `handoff_destino`, `duracao_ms`,
  `tentativas`) mais `motivo` (FR-006) e `event` (discriminador) —
  cobertura completa, sem gap.

## Qualidade de Critérios de Aceite

- [x] CHK016 - Os Success Criteria (SC-001 a SC-009) são objetivamente
  verificáveis por simulação, sem depender de julgamento subjetivo?
  [Critérios de Aceite, Spec §Success Criteria] {auto} Evidência: SC-001,
  SC-002, SC-003, SC-005 usam o padrão "100% dos casos simulados/das
  rajadas"; SC-006/SC-007/SC-009 são condições binárias verificáveis por
  teste automatizado.
- [ ] CHK017 - SC-010 (validação em produção multi-idioma) especifica
  qual "outro idioma suportado" (além de PT) deve ser exercitado, ou
  fica em aberto para quem executar a validação escolher? [Critérios de
  Aceite, Spec §SC-010] {humano} → SC-010 diz "pelo menos um outro idioma
  suportado" sem fixar qual (EN ou ES) — decisão operacional de quem
  conduzir a validação em produção, não bloqueante para create-tasks.
- [x] CHK018 - O critério de idempotência do recovery de debounce (US3
  AC-3 / FR-012) é verificável de forma determinística (execução dupla
  da rotina produz o mesmo resultado observável)? [Critérios de Aceite,
  Spec §FR-012, US3 Acceptance Scenario 3] {auto} Evidência: FR-012 exige
  "processado exatamente uma vez mesmo que a rotina de recuperação seja
  executada mais de uma vez"; research.md Decision 6 aponta o mecanismo
  concreto (`LRANGE`+`DEL` atômico) que torna isso testável por
  execução dupla em teste.

## Cobertura de Cenários e Edge Cases (foco: concorrência US3/US4)

- [x] CHK019 - A colisão simultânea entre teto de sessão e teto de nó
  tem resolução de precedência explícita e testável? [Cobertura de Edge
  Cases, Spec Edge Cases item 1] {auto} Evidência: "Precedência: teto de
  sessão prevalece — é o teto de segurança mais alto"; data-model.md
  State transitions US1 documenta a mesma ordem (`turnos_sessao >=
  teto_sessao → HANDOFF ... [precede o nudge]`).
- [x] CHK020 - O cenário de restart durante a janela de debounce cobre
  tanto "janela ainda válida" quanto "janela já expirada" como casos
  distintos? [Cobertura de Cenários, Spec §US3 Acceptance Scenarios 1-2]
  {auto} Evidência: AC1 (rajada pendente processada automaticamente) e
  AC2 (janela já expirada → processamento imediato na inicialização)
  cobrem os dois ramos; research.md Decision 6 detalha a lógica de
  decisão (marcador de vencimento vs flush imediato conservador).
- [x] CHK021 - O cenário de turno lento (pior caso de concorrência,
  US4) tem um "Independent Test" que aproxima efetivamente o pior caso
  conhecido (LLM + múltiplos envios + retries), não um caso trivial de
  curta duração? [Cobertura de Edge Cases, Spec §US4 Independent Test]
  {auto} Evidência: "Independent Test" de US4 declara "duração de
  processamento se aproxima do pior caso conhecido (resposta lenta +
  múltiplos envios + retentativas)"; research.md Decision 4 quantifica
  a composição do pior caso (LLM + até 4 envios + pacing + retries
  com backoff).
- [ ] CHK022 - Está definido o comportamento caso o recovery de debounce
  no startup encontre um agrupamento pendente cujo atendimento já foi
  encerrado ou passou a controle humano nesse meio-tempo (interação
  entre US3 e o gate de fila IA=77)? [Cobertura de Edge Cases, Spec
  Edge Cases item 5] {auto} Evidência: coberto — "o gate de fila existente
  prevalece: se o atendimento está sob controle humano, a rajada
  recuperada não gera resposta do bot" — item marcado `[x]` por conter
  a resolução explícita; citado aqui para reforçar rastreabilidade
  cruzada US3↔gate-de-fila no `/create-tasks` (tarefa deve tocar
  `debounce.py` + verificação do gate, não só `debounce.py` isolado).

## Requisitos Não-Funcionais (Segurança e Observabilidade)

- [x] CHK023 - A spec exige explicitamente que o registro de turno não
  substitua nem reduza os mecanismos de segurança existentes (verbatim,
  allowlist de handoff, elegibilidade médica)? [Não-Funcional/Segurança,
  Spec §FR-019, §FR-020] {auto} Evidência: FR-019 e FR-020 enumeram
  exaustivamente os mecanismos preservados, incluindo "apresentações
  enviadas sem passar por geração de texto livre" e "destino de
  encaminhamento sempre proveniente de configuração pré-definida".
- [x] CHK024 - A observabilidade de turno (FR-015/016) tem exatamente um
  registro por turno como invariante testável, evitando duplicação ou
  omissão silenciosa? [Não-Funcional, Spec §FR-015, §FR-016, SC-007]
  {auto} Evidência: FR-015 "exatamente um registro estruturado por turno
  processado"; SC-007 "100% dos turnos processados geram exatamente um
  registro"; FR-016 fecha o caso de falha (evita omissão).
- [x] CHK025 - A distribuição do pacing entre múltiplas réplicas (fora
  de escopo desta feature) está documentada como decisão consciente
  adiada, e não silenciosamente ignorada? [Não-Funcional, Spec §FR-014,
  Out of Scope] {auto} Evidência: FR-014 exige documentar explicitamente
  a pré-condição não implementada; spec.md "Out of Scope" reitera o
  mesmo item; plan.md mapeia FR-014 para documentação em
  research.md/plan.md (sem código).
- [ ] CHK026 - Há um requisito de retenção/volume para o log de eventos
  de turno (ex.: rotação, tamanho máximo por evento) que evite impacto
  de custo/armazenamento em produção com alto volume de turnos?
  [Não-Funcional, Spec §FR-015] {humano} → `[Gap]`: nem a spec nem o
  plano definem política de retenção do log estruturado ao stdout;
  aceitável para o escopo desta feature (log já existente reusa infra
  atual, spec.md Out of Scope exclui entidade durável), mas é uma
  premissa operacional que caberia ao operador de infraestrutura, não
  ao design desta feature — registrar como decisão consciente de
  não-escopo, não como bloqueio.

## Dependências e Premissas

- [x] CHK027 - A dependência entre US5 (observabilidade) e a validação
  empírica do TTL do lock (US4) está documentada e refletida na ordem de
  implementação? [Dependências, Spec §US4 AC-2, plan.md §Ordem de
  execução] {auto} Ver evidência de CHK013 (mesma dependência,
  perspectiva de premissa vs. de consistência de plano).
- [x] CHK028 - A premissa de que nenhum mecanismo existente precisa ser
  modificado (exceto o TTL do lock, FR-013) está explicitada como
  exceção única e justificada? [Dependências/Premissas, Spec §FR-019]
  {auto} Evidência: FR-019 lista os mecanismos preservados "exceto
  ajuste de FR-013" — a única exceção é nomeada explicitamente, não
  deixada implícita.
- [x] CHK029 - A premissa de "sem migration" está validada contra o
  fato de que todos os novos dados (contadores, evento) são efêmeros ou
  de log, sem necessidade de coluna Postgres? [Dependências/Premissas,
  Spec Contexto, data-model.md intro] {auto} Evidência: spec.md Contexto
  declara "introduz política de contadores efêmeros em Redis..."; data-
  model.md abre com "Sem migration. Todos os dados abaixo são efêmeros
  (Redis) ou eventos de log estruturado (stdout)".

## Ambiguidades e Conflitos

- [ ] CHK030 - Existe algum conflito entre o Edge Case "perda de
  contadores em Redis" (degradação aceita, fail-open) e o FR-004
  (handoff de segurança ao atingir teto de sessão)? Ou seja, a perda de
  contador pode ser explorada para nunca disparar handoff de segurança?
  [Ambiguidade/Conflito, Spec Edge Cases item 2, §FR-004] {humano} →
  `[Ambiguity]`: o fail-open (contador ausente ⇒ 0) é uma escolha de
  produto correta para não bloquear leads legítimos, mas tecnicamente
  abre uma janela em que uma sessão muito longa que sofre perda de
  contador reinicia a contagem de turnos-de-sessão, adiando o handoff
  de segurança. A spec resolve isso implicitamente como "degradação
  aceita" (item 2 dos Edge Cases), mas não there's trade-off de risco
  de segurança (frequência de perda de contador em produção é baixa,
  mas não nula) fica sem decisão explícita de aceite de risco — dono do
  produto deve confirmar que esse trade-off é aceitável (já é o
  comportamento assumido por research.md Decision 2(c); recomendação:
  registrar como risco aceito, não como bloqueio a `/create-tasks`).
- [x] CHK031 - Há conflito entre a preservação do contador anti-loop
  (`_MAX_TENTATIVAS=3`) e o novo contador de turnos-no-nó
  (`MAX_TURNOS_NO_NO=6`) quanto a qual dispara primeiro numa mesma
  sequência de turnos não-reconhecidos? [Ambiguidade/Conflito, Spec
  §FR-001, research.md Decision 3] {auto} Evidência: não há conflito —
  são contadores paralelos e independentes (Decision 3: "Os dois
  coexistem"); como `_MAX_TENTATIVAS=3` < `MAX_TURNOS_NO_NO=6`, o
  anti-loop tende a disparar `_reformular_ou_handoff` antes do nudge de
  orçamento em sequências totalmente não-reconhecidas — comportamento
  consistente com FR-019 (nenhuma linha do anti-loop é alterada) e não
  constitui conflito, apenas dois sinais de saúde distintos operando em
  paralelo, conforme intencional no design (Decision 3, Rationale).

## Notes

- Items `{auto}` já vêm resolvidos pelo agente (`[x]` com citação, ou
  marcador `[Gap]`/`[Assumption]`/`[Ambiguity]` quando não satisfeitos).
- Items `{humano}` ficam `[ ]` aguardando decisão do dono do produto.
- Rastreabilidade: 31/31 items (100%) referenciam spec/plan/research/
  data-model — acima do mínimo de 80%.

## Resolução

- **{auto} resolvidos**: 22 (`[x]` com evidência citada)
- **{humano} aguardando decisão**: 4 (CHK011, CHK017, CHK026, CHK030)
- **Gaps abertos** (`[Gap]`/`[Ambiguity]`/`[Assumption]`): CHK005 (Gap),
  CHK006 (Gap), CHK010 (Assumption — não-bloqueante), CHK011 (Gap),
  CHK026 (Gap), CHK030 (Ambiguity)

## Próximos Passos

- CHK011 (limiar mínimo do golden set) e CHK006 (anti-PII como FR
  explícito) são os dois gaps de maior risco — endereçar via tarefas
  específicas em `/create-tasks` ("definir limiar de aceitação do golden
  set" e "adicionar teste de regressão anti-PII/anti-secret no evento
  de turno", este último já implícito no gate `owasp-security` rodado em
  onda-001/dec-008, mas sem teste automatizado dedicado ainda).
- CHK005, CHK026 não bloqueiam `/create-tasks` — registrar como riscos
  aceitos/decisões conscientes (dono do produto já sinalizou defaults
  conservadores nas restrições invioláveis desta onda).
- CHK017, CHK030 ficam para decisão do dono do produto antes de
  `/execute-task` da US2 (idioma de validação) e antes do golden
  set/observabilidade cobrir o risco de segurança residual do fail-open
  de contadores (CHK030) — não bloqueiam a decomposição em tasks.
