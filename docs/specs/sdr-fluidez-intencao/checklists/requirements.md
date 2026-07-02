# Requirements Checklist: Fluidez Agêntica de Intenção no Atendimento SDR

**Purpose**: Quality gate de requisitos antes de `/create-tasks` — valida
completude, clareza, consistência e mensurabilidade de `spec.md`, `plan.md`,
`research.md`, `data-model.md`, `contracts/*.md` e `quickstart.md`. Não
valida implementação (código ainda não escrito).
**Created**: 2026-07-02
**Feature**: [spec.md](../spec.md) | [plan.md](../plan.md)

## Completude de Requisitos

- [x] CHK001 - O fallback agentico (FR-004) tem schema de extração, mecanismo
      de aceitação por confiança e fail-safe para erro/indisponibilidade do
      LLM totalmente especificados? [Completude, contracts/slot-troca-caminho.md
      S-1..S-3] {auto}
- [x] CHK002 - Existe requisito de fail-safe para o caso em que o fallback
      agentico retorna confiança acima do limiar mas um `valor` fora do
      conjunto de 6 caminhos esperados (hallucinação/erro de formatação)?
      [Completude, contracts/slot-troca-caminho.md S-6 — achado do gate
      owasp-security] {auto}
- [ ] CHK003 - Os ~10 call sites de `_reformular_ou_handoff`
      (`app/core/flow.py`, confirmado via `grep -n _reformular_ou_handoff` →
      10 chamadas nas linhas 1641/1692/1737/1877/1885/1904/2059/2071/2083/2095)
      estão individualmente enumerados com a classificação
      pergunta-curta-já-bare vs. precisa-de-pergunta-curta-dedicada? [Completude,
      research.md Decision 3/7] {auto} [Gap] — research.md Decision 7 confirma
      apenas 1 dos ~10 sites (`sistema_etapa1_2`, linha 354) com causa raiz
      empírica; os outros 9 estão explicitamente marcados "precisam de
      auditoria individual em `/create-tasks`" (research.md linha 223-224,
      plan.md linha 117 e §Próximos Passos item 2). Destino: `/create-tasks`
      (task dedicada de auditoria por call site, conforme o próprio plano já
      prevê — não é um gap de requisito, é decomposição intencionalmente
      deferida).
- [x] CHK004 - A decisão sobre o arquivo de teste que hospeda os testes de
      unidade do detector (`test_troca_caminho.py` novo vs. extensão de
      `test_flow.py`) está explicitamente marcada como deferida, com critério
      de decisão declarado (não é uma omissão silenciosa)? [Completude,
      plan.md linhas 172, 182-183 "decisão de `/create-tasks`"] {auto}
- [x] CHK005 - Existe requisito para o comportamento do sistema quando a
      leitura do campo Redis `troca_pendente` falha (ausente ou JSON
      corrompido)? [Completude, contracts/estado-troca-pendente.md P-4
      "Fail-open"] {auto}
- [x] CHK006 - A extensão do evento de log (`log_turno`) declara
      explicitamente que nenhum campo novo carrega texto bruto da mensagem do
      lead? [Completude, data-model.md linhas 124-132 "Invariantes de
      segurança"] {auto}
- [x] CHK007 - Existe requisito definindo o limite de chamadas ao fallback
      agentico por lead malicioso tentando forçar consumo repetido (custo/
      LLM10)? [Completude, contracts/slot-troca-caminho.md S-7 — gated por
      `_MAX_TENTATIVAS=3` já existente] {auto}

## Clareza de Requisitos

- [ ] CHK008 - O termo "erro leve de digitação/acentuação" (FR-003/FR-013) é
      operacionalizado com um mecanismo objetivo, ou fica subjetivo? [Clareza,
      Spec FR-003] {auto} — parcialmente resolvido: data-model.md define que
      o reconhecimento é por pertencimento a um conjunto pré-computado de
      variantes normalizadas (`_LEXICO_CAMINHOS`, via `_norm()`), não por
      distância de edição/fuzzy matching (research.md Decision 1, "nunca
      fuzzy-matching probabilístico") — o MECANISMO é claro, mas o CONTEÚDO
      exato (quais erros específicos entram na lista por caminho/idioma) não
      está enumerado nos artefatos de design. [Gap] destino `/create-tasks`
      (task de popular a lista de variantes/typos conhecidos por caminho e
      idioma).
- [ ] CHK009 - O texto de "confirmação breve e natural" (FR-005) tem o
      conteúdo i18n concreto (PT/EN/ES) definido, ou apenas a existência de um
      novo bloco? [Clareza, FR-005, plan.md linha 88] {auto} [Gap] — plan.md
      aponta "novo bloco i18n de confirmação de troca" mas não lista os
      textos; destino `/create-tasks` (redação de conteúdo, sujeita ao
      Princípio IV "Comunicação Consultiva Premium", plan.md linha 73).
- [ ] CHK010 - O texto da "pergunta direta de desambiguação" (FR-008/FR-012)
      está definido para os pares de caminhos ambíguos, ou fica a critério da
      implementação? [Clareza, FR-008] {auto} [Gap] — mesma classe do CHK009,
      destino `/create-tasks`.
- [ ] CHK011 - O conteúdo textual das variantes de reformulação
      (`_REFORMULACOES`, FR-015) — quantidade exata (2 ou 3) e texto por
      idioma — está definido, além do algoritmo de rotação? [Clareza,
      research.md Decision 7, FR-015] {auto} — o ALGORITMO está totalmente
      definido e sem ambiguidade (`variante_idx = (n-1) % len(pool)`,
      determinístico e testável); o CONTEÚDO textual das variantes é [Gap]
      destino `/create-tasks`.
- [x] CHK012 - A distinção entre "pergunta_curta" (reformulável) e
      "bloco-de-entrada" (com saudação/explicação) está definida com um
      exemplo concreto e verificável no código-fonte? [Clareza, research.md
      Decision 7 — causa raiz em `app/core/flow.py:354`
      (bloco `"sistema_etapa1_2"`), confirmado por leitura direta do arquivo
      (linhas 350-360: bloco inicia com `"Perfeito! 😊\n\n"` seguido de
      explicação longa)] {auto}

## Consistência entre Artefatos

- [x] CHK013 - O limiar de confiança (60% / 0.6) é idêntico entre spec.md
      (FR-004), plan.md (Technical Context/Constraints) e
      contracts/slot-troca-caminho.md (S-2)? [Consistência] {auto}
- [x] CHK014 - O nome do env var `INTENT_SWITCH_CONFIDENCE_THRESHOLD` é
      idêntico em todas as ocorrências (plan.md Constitution Check, Mapeamento
      FR, Project Structure; contracts/slot-troca-caminho.md S-2)?
      [Consistência] {auto}
- [x] CHK015 - O schema JSON do campo Redis `troca_pendente` é idêntico entre
      data-model.md (§Estado de Confirmação/Desambiguação Pendente) e
      contracts/estado-troca-pendente.md? [Consistência] {auto}
- [x] CHK016 - A preservação da supressão de `_ETAPAS_AGUARDANDO_RESPOSTA`
      (fix #9) é tratada de forma consistente entre research.md Decision 3/4
      e plan.md (linha 108)? [Consistência] {auto}
- [x] CHK017 - A precedência do overflow-resume sobre o novo detector
      (clarify Q2/dec-008) está refletida de forma idêntica em spec.md (edge
      case), research.md Decision 5, data-model.md (§Regras de manipulação) e
      contracts/estado-troca-pendente.md (P-1)? [Consistência] {auto}
- [x] CHK018 - O comportamento "não-confirmação conta como tentativa da
      pergunta original" (clarify Q1/dec-007) está refletido de forma idêntica
      em spec.md (§Clarifications), data-model.md (§State transitions) e
      research.md Decision 6? [Consistência] {auto}
- [x] CHK019 - O limite de exatamente 2 caminhos para desambiguação
      (FR-008/012, clarify Q4/dec-009 sobre 3+) é consistente entre spec.md e
      o schema `destinos` de data-model.md (documentado como 1 ou 2
      elementos, nunca 3+)? [Consistência] {auto}
- [x] CHK020 - O princípio "roteamento 100% determinístico, LLM só extrai"
      (Summary do plan.md) é reforçado sem contradição em todas as 7 linhas da
      Constitution Check (todas PASS) e em contracts/slot-troca-caminho.md
      (S-4)? [Consistência] {auto}

## Qualidade de Critérios de Aceite (Mensurabilidade)

- [x] CHK021 - SC-002/SC-003/SC-004/SC-005 (todos "0%") têm um mecanismo de
      medição objetivo definido (golden set com casos específicos), e não
      apenas uma meta qualitativa? [Mensurabilidade, research.md Decision 9]
      {auto}
- [ ] CHK022 - SC-001 (cenário real relatado: "harmonização glutea" citado
      dentro do caminho "Sistema GoldIncision") tem um caso de golden set que
      reproduz literalmente essa conversa, ou apenas cenários genéricos
      equivalentes? [Mensurabilidade, quickstart.md Cenário 1] {auto} [Gap] —
      quickstart.md Cenário 1 cobre "correção de rumo com marcador explícito"
      de forma genérica (FR-002/003/005), sem menção ao texto literal do caso
      relatado no §Contexto e motivação da spec. Destino `/create-tasks`
      (adicionar caso de golden set específico reproduzindo o caso real,
      reforçando SC-001 além da cobertura genérica já prevista).
- [x] CHK023 - SC-006 ("100% dos eventos... sem lacunas") tem um mecanismo de
      verificação automatizado, não dependente de inspeção manual?
      [Mensurabilidade, data-model.md linha 131 "emitido exatamente 1x por
      turno"; quickstart.md Cenário 14] {auto}
- [x] CHK024 - SC-007 (PT/EN/ES) tem cobertura de teste explícita nos três
      cenários centrais da feature (troca de caminho, menu texto livre,
      reformulação), não apenas em um deles? [Mensurabilidade,
      quickstart.md Cenário 13 — repete Cenários 1, 5 e 7 em EN/ES] {auto}

## Cobertura de Cenários (User Stories)

- [x] CHK025 - Todos os 4 Acceptance Scenarios de US1 (correção de rumo) têm
      caso de golden set/quickstart correspondente? [Cobertura,
      quickstart.md Cenários 1-4] {auto}
- [x] CHK026 - Todos os 3 Acceptance Scenarios de US2 (menu em texto livre)
      têm caso de golden set/quickstart correspondente? [Cobertura,
      quickstart.md Cenários 5-6; AS3 de US2 — texto não reconhecido — cai no
      `[Gap]` documentado abaixo em CHK029] {auto}
- [x] CHK027 - Ambos os Acceptance Scenarios de US3 (reformulação
      humanizada) têm caso de golden set/quickstart correspondente?
      [Cobertura, quickstart.md Cenários 7-8] {auto}
- [x] CHK028 - Ambos os Acceptance Scenarios de US4 (observabilidade) têm
      caso de golden set/quickstart correspondente? [Cobertura,
      quickstart.md Cenário 14] {auto}

## Cobertura de Edge Cases

- [x] CHK029 - "Resposta legítima e direta nunca é desviada por engano" está
      coberto por caso de regressão? [Edge Case, Spec §Edge Cases item 1;
      quickstart.md Cenário 4] {auto}
- [x] CHK030 - "Intenção clara sem marcador explícito → pergunta de
      confirmação curta antes de trocar" está coberto? [Edge Case, Spec
      §Edge Cases item 2; quickstart.md Cenário 9] {auto}
- [ ] CHK031 - "Lead pede para 'voltar' ou 'ver o menu de novo' → tratado
      como pedido de troca de rumo" está mapeado a algum mecanismo de
      reconhecimento (léxico/marcador) e coberto por caso de teste? [Edge
      Case, Spec §Edge Cases item 3] {auto} [Gap] — busca em plan.md,
      research.md, data-model.md e quickstart.md não encontrou nenhuma
      referência a "voltar"/"ver o menu de novo" fora da própria linha da
      spec (grep confirmado: única ocorrência é `spec.md:197`). Este edge case
      não está mapeado para `_MARCADORES_CORRECAO`/`_LEXICO_CAMINHOS`
      (Decision 1) nem para nenhum cenário do quickstart — não há evidência
      de que o design cobre esse caso. Destino recomendado: `/clarify` (é uma
      lacuna de design, não apenas de decomposição de tarefa) — confirmar se
      "voltar"/"menu" deve entrar em `_MARCADORES_CORRECAO` como um marcador
      especial (sem produto associado, aciona reapresentação do menu) antes
      de `/create-tasks` gerar a tarefa correspondente.
- [x] CHK032 - "Retorno a caminho já visitado reinicia do zero" está
      coberto? [Edge Case, Spec §Edge Cases item 4, FR-021; quickstart.md
      Cenário 12] {auto}
- [x] CHK033 - "Produto/caminho citado não reconhecível nem por léxico nem
      por classificação assistida → cai no comportamento de reformulação
      existente" está coberto? [Edge Case, Spec §Edge Cases item 5; FR-010,
      satisfeito por construção — nenhum novo caminho de código, é o `else`
      natural do detector (research.md Decision 3)] {auto}
- [x] CHK034 - "Retomada de overflow tem prioridade sobre detecção de troca"
      está coberto? [Edge Case, Spec §Edge Cases item 6; quickstart.md
      Cenário 10] {auto}
- [x] CHK035 - "PT/EN/ES, sempre respondendo no idioma do lead" está
      coberto para os três fluxos centrais? [Edge Case, Spec §Edge Cases item
      7; quickstart.md Cenário 13] {auto}
- [x] CHK036 - "Correção para o mesmo caminho já ativo → sem efeito
      colateral" está coberto? [Edge Case, Spec §Edge Cases item 8;
      quickstart.md Cenário 11] {auto}

## Requisitos Não-Funcionais

- [x] CHK037 - O requisito de que "nenhuma capacidade de IA decide o
      roteamento" (Princípio I/II da constitution) está garantido
      estruturalmente (não apenas por convenção de código)? [NFR-Segurança,
      contracts/slot-troca-caminho.md S-4; research.md Decision 10] {auto}
- [x] CHK038 - A mensagem do lead é tratada como dado não confiável (anti
      prompt-injection) em todo o pipeline do detector, incluindo o fallback
      agentico? [NFR-Segurança, research.md Decision 10, SEC-LLM-1/SEC-LLM-3]
      {auto}
- [x] CHK039 - O requisito de performance (detector determinístico O(1) por
      turno) está quantificado e distinto do teto de latência do fallback
      agentico (que herda o orçamento já aceito do `SlotExtractor`)?
      [NFR-Performance, plan.md §Performance Goals] {auto}
- [x] CHK040 - A feature não introduz nenhuma dependência nova de
      infraestrutura (biblioteca, serviço, migration)? [NFR-Dependências,
      plan.md §Primary Dependencies "todas já presentes; nenhuma nova",
      §Storage "sem Postgres novo"] {auto}

## Dependências e Premissas

- [x] CHK041 - A premissa de 1 réplica ativa (sem necessidade de coordenação
      multi-instância) está validada contra o ambiente real de produção
      (Docker Swarm)? [Premissas, plan.md §Target Platform/§Scale/Scope]
      {auto}
- [ ] CHK042 - A calibração do limiar de confiança padrão (60%) para o
      ambiente de produção real é uma decisão de produto que depende de dados
      de observabilidade (US4) ainda não coletados sobre esta feature — deve
      permanecer no default documentado até haver dados suficientes para
      ajuste? [Premissas/Risco, FR-004, plan.md §Constitution Check linha 75]
      {humano} — decisão de calibração pós-deploy; default 0.6 já está
      documentado e não bloqueia `/create-tasks`. Sem contradição entre
      artefatos — não gera bloqueio humano nesta onda.

## Notes

- Items `{auto}` já vêm resolvidos pelo agente (`[x]` com citação, ou
  marcador `[Gap]`/`[Ambiguity]`/`[Conflict]` quando a evidência não fecha o
  item).
- Items `{humano}` ficam `[ ]` aguardando decisão do dono do produto — CHK042
  é o único desta rodada; não é bloqueante (default já documentado, apenas
  aguarda dados futuros de observabilidade).
- Marcar items concluídos com `[x]`.
- Items numerados sequencialmente (CHK001-CHK042) para referência.

### Resolução

- **{auto} resolvidos** (`[x]` com evidência citada): 33
- **{humano} aguardando decisão** (não-bloqueante, default já documentado):
  1 (CHK042)
- **Gaps abertos** (`[Gap]`): 7 — CHK003, CHK008, CHK009, CHK010, CHK011,
  CHK022, CHK031
- **Rastreabilidade**: 42/42 items (100%) citam `[Spec §X]`, decisão de
  research.md, contrato, ou marcador `[Gap]` — acima do mínimo de 80%.

### Follow-up obrigatório (destino de cada gap aberto)

| CHK | Marcador | Destino |
|-----|----------|---------|
| CHK003 | `[Gap]` (decomposição) | `/create-tasks` — task de auditoria individual dos ~9 call sites restantes de `_reformular_ou_handoff` (já previsto em plan.md §Próximos Passos) |
| CHK008 | `[Gap]` (conteúdo) | `/create-tasks` — task de popular `_LEXICO_CAMINHOS`/`_MARCADORES_CORRECAO` com variantes/typos concretos por caminho e idioma |
| CHK009 | `[Gap]` (conteúdo) | `/create-tasks` — task de redigir o bloco i18n de confirmação de troca (PT/EN/ES) |
| CHK010 | `[Gap]` (conteúdo) | `/create-tasks` — task de redigir o texto de desambiguação por par de caminhos ambíguos |
| CHK011 | `[Gap]` (conteúdo) | `/create-tasks` — task de redigir 2-3 variantes de `_REFORMULACOES` por idioma |
| CHK022 | `[Gap]` (cobertura) | `/create-tasks` — task de adicionar caso de golden set que reproduz literalmente o cenário real relatado (SC-001) |
| CHK031 | `[Gap]` (design não mapeado) | **`/clarify`** — único item que não é decomposição de tarefa, mas lacuna de design: confirmar se "voltar"/"ver o menu de novo" deve ser tratado como marcador especial em `_MARCADORES_CORRECAO`/novo mecanismo antes de gerar a task em `/create-tasks` |

### Próximos Passos

- Resolver CHK031 via `/clarify` (recomendado, único gap de design — os
  demais 6 gaps são decomposição de conteúdo já prevista pelo próprio plano)
- `/create-tasks` — os demais `[Gap]` (CHK003/008/009/010/011/022) viram
  tarefas de acordo com a tabela de follow-up acima
- `/checklist security` ou gate `owasp-security` (já rodado uma vez na onda
  de `/plan`, achado S-6 incorporado) — reavaliar se `/clarify` alterar o
  desenho do reconhecimento de "voltar ao menu"
