# Requirements Checklist: Contrato JSON, Portão de Fidelidade e Interpretação Agêntica (Onda 2)

**Purpose**: Validar a qualidade dos requisitos de `spec.md`/`plan.md` da
feature `sdr-fidelidade-json` antes de `create-tasks` — cobertura de
FR-001..FR-027, RESTRIÇÕES INVIOLÁVEIS e do finding OWASP dec-018.
**Created**: 2026-07-01
**Feature**: `docs/specs/sdr-fidelidade-json/spec.md`

## Completude de Requisitos

- [x] CHK001 - O contrato estruturado de resposta define todos os campos mínimos exigidos (texto, fontes, indicador de handoff, confiança)? [Completude, Spec §FR-001] {auto}
- [x] CHK002 - Existe requisito explícito para o caso de pacote malformado, incluindo número máximo de tentativas antes do handoff? [Completude, Spec §FR-003] {auto}
- [x] CHK003 - O requisito de temperatura reduzida está associado a critério objetivo (quais etapas contam como "fatos")? [Completude, Spec §FR-004] {auto}
- [x] CHK004 - Está definido o conjunto fechado de "condição comercial" que aciona o portão de fidelidade? [Completude, Spec §FR-008, Clarifications Q2] {auto}
- [x] CHK005 - O comportamento do portão de fidelidade em caso de erro/indisponibilidade/timeout está especificado (fail-closed)? [Completude, Spec §FR-009] {auto}
- [x] CHK006 - Está definido o que a verificação de fidelidade deve registrar quando reprova uma resposta? [Completude, Spec §FR-010] {auto}
- [x] CHK007 - A cobertura mínima de etapas do entendimento assistido (slot-filling) está enumerada explicitamente? [Completude, Spec §FR-017] {auto}
- [x] CHK008 - Existe requisito cobrindo o caso de reconhecimento numérico de menu permanecer sempre determinístico? [Completude, Spec §FR-019] {auto}
- [ ] CHK009 - Está definido um valor/mecanismo de fallback quando o `SLOT_CONFIDENCE_THRESHOLD` precisar ser recalibrado por etapa no futuro (hoje é global único)? [Gap, Spec §FR-015, Clarifications Q1] {humano}

## Clareza de Requisitos

- [x] CHK010 - O termo "condição comercial" está quantificado com lista fechada de categorias, e não deixado como julgamento livre do modelo? [Clareza, Spec §FR-008] {auto}
- [x] CHK011 - O limiar de confiança do slot-filling (`0.6`) é um valor numérico único e não uma faixa vaga? [Clareza, Spec §FR-015] {auto}
- [x] CHK012 - O timeout do portão de fidelidade (`~3s`) está expresso como valor concreto configurável, não como "rápido o suficiente"? [Clareza, Spec §FR-009, Clarifications Q3] {auto}
- [x] CHK013 - "Alta certeza" do reconhecimento determinístico (FR-013) é ancorada em funções/comportamento já existentes (fast-path) e não em critério subjetivo novo? [Clareza, Plan §Pilar-Interpretação-agêntica] {auto}
- [ ] CHK014 - O critério de "alta confiança" para reverter um dado de qualificação já consolidado (Edge Case) está quantificado (ex.: limiar numérico) ou permanece qualitativo? [Ambiguity, Spec §Edge-Cases] {humano}

## Consistência de Requisitos

- [x] CHK015 - A exceção de verbatim (FR-007) é replicada de forma consistente também para o portão de fidelidade (FR-012)? [Consistência, Spec §FR-007, FR-012] {auto}
- [x] CHK016 - O requisito de que o modelo nunca decide transição de estado (FR-006) é consistente entre o contrato estruturado, o portão de fidelidade e o slot-filling (FR-020/FR-021)? [Consistência, Spec §FR-006, FR-020, FR-021] {auto}
- [x] CHK017 - O requisito de "mensagem do lead como dado, nunca instrução" (FR-020) está espelhado igualmente nos 3 mecanismos novos (contrato, portão, slot)? [Consistência, Spec §FR-020, Plan §SEC-LLM-1] {auto}
- [x] CHK018 - O destino de handoff vindo exclusivamente da allowlist (FR-021) é consistente com o campo do contrato estruturado que só sinaliza boolean `precisa_handoff` (sem carregar destino)? [Consistência, Spec §FR-021, Plan §SEC-LLM-3] {auto}
- [x] CHK019 - A preservação dos mecanismos da Onda 1 (FR-025/FR-026/FR-027) é consistente com a marcação de "alterações aditivas" no plano? [Consistência, Spec §FR-025..FR-027, Plan §Restrições] {auto}

## Qualidade de Critérios de Aceite (Success Criteria)

- [x] CHK020 - SC-001 é mensurável (100% das respostas de condição comercial passam pelo portão, medido no conjunto de regressão)? [Mensurabilidade, Spec §SC-001] {auto}
- [x] CHK021 - SC-003 define um piso numérico (>=90%) e não apenas "a maioria"? [Mensurabilidade, Spec §SC-003] {auto}
- [ ] CHK022 - SC-004 define a "linha de base medida antes desta feature" com uma fonte/metodologia concreta de medição, ou fica em aberto até execução? [Ambiguity, Spec §SC-004] {humano}
- [x] CHK023 - SC-005 é objetivamente verificável (nº máximo de 1 retry antes de handoff, nunca conteúdo malformado chega ao lead)? [Mensurabilidade, Spec §SC-005] {auto}
- [x] CHK024 - SC-007 amarra a feature à suíte de regressão já existente da Onda 1 como critério de aceite explícito? [Mensurabilidade, Spec §SC-007] {auto}

## Cobertura de Cenários / Edge Cases

- [x] CHK025 - Está coberto o cenário de indisponibilidade simultânea do modelo de entendimento e do de redação? [Cobertura, Spec §Edge-Cases] {auto}
- [x] CHK026 - Está coberto o cenário de tentativa de prompt injection via mensagem do lead (ex.: "ignore as regras anteriores")? [Cobertura, Spec §Edge-Cases, FR-020] {auto}
- [x] CHK027 - Está coberto o cenário em que o portão de fidelidade não consegue decidir (tratado como reprovação)? [Cobertura, Spec §Edge-Cases, FR-009] {auto}
- [x] CHK028 - Está coberto o cenário de apresentação verbatim nunca passar pelo contrato nem pelo portão? [Cobertura, Spec §Edge-Cases, FR-007, FR-012] {auto}
- [ ] CHK029 - Está coberto o cenário de resposta cujo idioma detectado diverge do idioma da conversa (FR-005) — qual ação concreta é tomada além de "inválido"? [Gap, Spec §FR-005] {humano}
- [ ] CHK030 - Está coberto o cenário de esgotamento simultâneo do teto `max_msgs_per_turn=4` E acionamento do portão de fidelidade na mesma reação? [Gap, Plan §Preservação-Onda-1] {humano}

## Requisitos Não-Funcionais (Segurança / Observabilidade)

- [x] CHK031 - Existe requisito de que nenhuma mensagem do lead altera fila de destino, regra de elegibilidade ou instruções internas (LLM01)? [NFR-Segurança, Spec §FR-020, Plan §OWASP] {auto}
- [x] CHK032 - Existe requisito de validação estrita (schema/Pydantic) da saída do modelo antes de qualquer ação sobre ela (LLM02)? [NFR-Segurança, Spec §FR-002] {auto}
- [ ] CHK033 - O requisito de logging do veredito de fidelidade especifica explicitamente que `afirmações_não_sustentadas` (texto livre gerado) NUNCA é logado verbatim sem passar pelo scrubber anti-PII já existente (`tests/test_anti_pii_turno.py`)? [Gap, dec-018, Plan §LLM06] {auto} — **NÃO SATISFEITO NA SPEC**: FR-010/FR-018 exigem registrar/logar o veredito para observabilidade, mas não citam o scrubber anti-PII explicitamente; o requisito só existe como Decisão de plano (dec-018), não como FR numerado. Vira task explícita em `create-tasks`.
- [x] CHK034 - Existe requisito de elegibilidade médica permanecer inflexível e não contornável por nenhum dos 3 mecanismos novos (LLM06/compliance)? [NFR-Segurança, Spec §FR-023] {auto}
- [x] CHK035 - Existe requisito de custo/DoS mitigado via timeout do portão + fast-path + limitador de tokens já existente (LLM04)? [NFR-Segurança, Plan §OWASP] {auto}
- [x] CHK036 - Existe requisito de idioma da resposta (PT/EN/ES) e de 1 pergunta por mensagem (exceto menus) explicitamente amarrado às respostas geradas por esta feature? [NFR-UX, Spec §FR-024] {auto}

## Dependências e Premissas

- [x] CHK037 - O plano ancora cada FR em referência real de código (arquivo:linha), evitando presunção de estrutura inexistente? [Dependência, Plan §Mapeamento-FR] {auto}
- [x] CHK038 - Os dois envs novos (`SLOT_CONFIDENCE_THRESHOLD`, `VERIFY_TIMEOUT_SECONDS`) têm default explícito e local de declaração (config/stack.yml/.env.example)? [Dependência, Plan §Envs-novos] {auto}
- [x] CHK039 - A estratégia de testes assume FlowEngine real (não mockado), mockando apenas o client OpenAI, de forma consistente com o padrão já usado na Onda 1? [Dependência, Plan §Estratégia-de-testes] {auto}
- [x] CHK040 - A preservação explícita dos 8 mecanismos da Onda 1 (anti-loop, teto de mensagens, Pacer/429, idempotência, lock, gate IA, debounce, TTL) está listada nominalmente no plano, e não apenas genericamente citada? [Dependência, Plan §Preservação-Onda-1, Spec §FR-025..FR-027] {auto}

## Notes

- Items `{auto}` já vêm resolvidos pelo agente (`[x]` com citação, ou marcador `[Gap]`/`[Ambiguity]`).
- Items `{humano}` ficam `[ ]` aguardando decisão do dono do produto.
- **6 itens em aberto**: CHK009, CHK014, CHK022, CHK029, CHK030 (`{humano}` — julgamento de produto/risco, não bloqueiam `create-tasks`) e CHK033 (`{auto}` com `[Gap]` real — **vira task obrigatória** por já estar coberto por Decisão auditável dec-018).
- Nenhum `[Conflict]` encontrado entre FR-001..FR-027 e as RESTRIÇÕES INVIOLÁVEIS.
