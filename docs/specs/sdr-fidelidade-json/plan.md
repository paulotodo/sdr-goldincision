# Implementation Plan — Contrato JSON, Portão de Fidelidade e Interpretação Agêntica (Onda 2)

Feature: `sdr-fidelidade-json` · Branch: `feature/sdr-fidelidade-json` (a partir de
`master`, Onda 1 `sdr-turnos-obs` mergeada em 49bafbb) · Entrega: **PR** (master
protegido, CI Lint+Testes obrigatório) — **NÃO mergear**.

Inputs: `spec.md` (FR-001..FR-027 + §Clarifications), `research.md`, `data-model.md`,
design `docs/plano-interpretacao-agentica.md`, `docs/constitution.md` (v1.0.0),
`CLAUDE.md`.

## Summary

Adiciona três mecanismos de fidelidade sobre o agente SDR existente, sem tocar a
máquina de estados determinística:

1. **Contrato JSON de resposta** (Pilar 6): envolve `GroundedResponder.generate()` num
   pacote estruturado validado (`RespostaEstruturada`), com 1 retry e fallback a
   handoff.
2. **Portão de Fidelidade** (Pilar 7): `FidelityGate` verifica groundedness (gpt-4o-mini)
   de respostas que tocam condição comercial, ANTES do envio, fail-closed.
3. **Slot-filling agêntico** (`SlotExtractor`): entendimento assistido por etapa, só
   quando o fast-path determinístico não resolve, com limiar de confiança global 0.6.

Todos os pontos de escrita ficam confinados a `app/core/` do projeto-alvo; a Onda 1 e
mecanismos anteriores são preservados integralmente.

## Constitution Check (docs/constitution.md v1.0.0)

| Princípio | Como o plano cumpre | Status |
|-----------|---------------------|--------|
| I. Fidelidade ao Fluxo Oficial (Mapa Mestre) | Máquina de estados permanece 100% determinística; o pacote JSON só informa (FR-006). LLM não decide transição. | PASS |
| II. Anti-Alucinação Rígida | Portão de fidelidade fail-closed antes do envio; verbatim/objeções nunca passam pelo LLM (Decision 7); slot abaixo do limiar não adivinha (FR-015). | PASS — reforça |
| III. Memória e Jornada Sem Atrito | SlotExtractor usa `known_facts`+histórico (FR-016) p/ não reperguntar; fato consolidado não é revertido sem alta confiança. | PASS |
| IV. Comunicação Consultiva Premium | Redação de dúvidas mantém gpt-4o com humanização; contingência usa blocos canônicos. | PASS |
| V. Elegibilidade, Objeções e Handoff Disciplinados | Escopo de "condição comercial" (dec-010) inclui elegibilidade; handoff destino sempre da allowlist (SEC-LLM-3, config.py:55). | PASS |
| VI. Isolamento e Segurança de Infraestrutura | Sem novas dependências externas/rede; envs sem hardcode; mensagem do lead como dado (SEC-LLM-1). | PASS |
| VII. Cursos como Dados (sem redeploy) | `escolha_turma` lê turmas da config existente; nenhum dado de curso hardcoded. | PASS |

**Gate de segurança (constitution como MUST)**: nenhum princípio violado. Riscos OWASP
tratados na seção §OWASP abaixo. Nenhum desvio registrado — sem entradas em Complexity
Tracking.

## Technical Context

- Linguagem/stack: Python 3.12 · FastAPI · Postgres 16 · Redis 7 · OpenAI · Docker Swarm.
- Pydantic v2 (via pydantic-settings, já presente).
- Sem migrations (nenhuma tabela nova); envs aplicados no startup via `Settings`.
- Modelos: gpt-4o (`openai_model_reasoning`) só redação/contrato; gpt-4o-mini
  (`openai_model_cheap`) classificação/extração/verificação.

## Mapeamento FR → arquivo → abordagem (ancorado em refs reais)

### Pilar 6 — Contrato estruturado (FR-001..FR-007)

| FR | Arquivo | Abordagem |
|----|---------|-----------|
| FR-001, FR-007 | `app/core/contracts.py` (novo) | Schema `RespostaEstruturada {texto, fontes, precisa_handoff, confianca, idioma}` (ver data-model §1). |
| FR-002, FR-003 | `app/core/responder.py:165` (`generate`) | Gerar via `response_format=json_schema`; validar Pydantic; **1** retry; 2ª falha → `(handoff=True)`. |
| FR-004 | `app/core/responder.py` | `temperature` 0–0.2 quando a etapa trata de fatos (preço/data/condição/elegibilidade). |
| FR-005 | `app/core/contracts.py` + `responder.py` | Campo `idioma` no pacote conferido contra idioma da conversa; divergência = inválido. |
| FR-006 | `app/core/flow.py:1403,1409` | `generate()` mantém a 2-tupla `(texto, handoff)`; FlowEngine nunca vê o objeto → transição segue determinística. |

### Pilar 7 — Portão de Fidelidade (FR-008..FR-012)

| FR | Arquivo | Abordagem |
|----|---------|-----------|
| FR-008, FR-011 | `app/core/fidelity.py` (novo) | Gatilho = "condição comercial" (dec-010): preço/valor, parcelamento, desconto/promoção, data/prazo, disponibilidade de turma/vaga, elegibilidade médica. |
| FR-009, FR-010 | `app/core/fidelity.py` | `FidelityGate.verificar(texto, knowledge_context)` (gpt-4o-mini) → `VeredictoFidelidade {fiel, afirmacoes_nao_sustentadas}` (data-model §2). |
| FR-012 (fail-closed) | `app/core/fidelity.py` + `responder.py` | Erro/indisponibilidade/timeout == `fiel=False` → contingência: bloco canônico "informação indisponível" → reformulação → handoff. |
| Timeout (Q3/dec-011) | `app/config.py` + `fidelity.py` | `VERIFY_TIMEOUT_SECONDS=3` (hard), alvo interno ~2s. |

Ponto de invocação: dentro de `GroundedResponder.generate()`, após montar o texto e
antes de retornar, reusando o `knowledge_context` já passado por `flow.py`
(`_load_knowledge_by_slug`, caminho de dúvidas/objeções em flow.py:1399-1411).

### Interpretação agêntica — SlotExtractor (FR-013..FR-018)

| FR | Arquivo | Abordagem |
|----|---------|-----------|
| FR-013 | `app/core/flow.py` (fast-path existente) | Reconhecimento determinístico (`_detectar_medico_investidor`, `_detectar_fechamento`, `_eh_pergunta`) roda PRIMEIRO; resolve com alta certeza → sem LLM. |
| FR-014, FR-016 | `app/core/interpret.py` (novo) | `SlotExtractor.extract(slot_schema, user_message, contexto)` (gpt-4o-mini, json_schema); usa `known_facts`+histórico; mensagem = dado (SEC-LLM-1). |
| FR-015 | `app/config.py` + `interpret.py` | `confianca >= SLOT_CONFIDENCE_THRESHOLD` (0.6, dec-009); abaixo → "não entendida" → reformular. |
| FR-017 | `app/core/flow.py` (etapas) | Cobertura das 5 etapas: `qualif_medico`, objetivo, `qualif_experiencia`, `qualif_especialidade`, `escolha_turma`. |
| FR-018 | `app/observability/log.py` (Onda 1) | `confianca` de slot e veredito de fidelidade logados de forma **aditiva** no `log_turno` existente (limiares plural = observabilidade futura). |

### Restrições de segurança e preservação (FR-019..FR-027 + RESTRIÇÕES INVIOLÁVEIS)

- **SEC-LLM-1** (mensagem = dado): SlotExtractor e prompt de redação delimitam a
  mensagem do lead; guardas anti prompt-injection. Aplicado em `interpret.py` e
  `responder.py`.
- **SEC-LLM-3** (handoff da allowlist): destino/queueId sempre de
  `handoff_queue_ids_json` (config.py:55). O pacote JSON só seta `precisa_handoff: bool`.
- **Verbatim intacto** (Decision 7): apresentações/menus/paciente-modelo/objeções saem
  do DB sem LLM; contrato/portão não se aplicam a blocos canônicos.
- **Preservar Onda 1 + anteriores**: `log_turno`, contadores/nudge/handoff,
  reengajamento, debounce recovery, `lock_ttl_ms` (config.py:115), `max_msgs_per_turn=4`
  (config.py:134), `_Pacer`+429, idempotência, gate IA=77, `debounce_seconds=8`
  (config.py:91), anti-loop `_MAX_TENTATIVAS=3` (não fundido). Alterações são aditivas.

## Envs novos (config + stack.yml + .env.example)

| Env | Local | Default |
|-----|-------|---------|
| `SLOT_CONFIDENCE_THRESHOLD` | `app/config.py` (`slot_confidence_threshold: float`), `.env.example`, `stack.yml` | `0.6` |
| `VERIFY_TIMEOUT_SECONDS` | `app/config.py` (`verify_timeout_seconds: int`), `.env.example`, `stack.yml` | `3` |

Padrão herdado da Onda 1 (tasks 1.1.4/1.1.5): sem hardcode, sem secrets, teste de config
valida defaults + override.

## Estratégia de testes (RESTRIÇÃO INVIOLÁVEL)

- **FlowEngine REAL**: mock apenas do `OpenAIClient` (padrão de
  `tests/test_reengajamento.py`, `tests/test_responder.py`), nunca do motor.
- Novos testes de unidade: `RespostaEstruturada` (válido/malformado/retry/handoff),
  `FidelityGate` (fiel/não-fiel/timeout=reprovação), `SlotExtractor` (fast-path
  curto-circuita LLM; confiança < 0.6 reformula), config (defaults + override dos 2 envs).
- Golden set estendido: `@pytest.mark.golden` em `tests/golden/`, fora do CI padrão —
  casos de resposta verificada antes do envio (US1 Independent Test) e slot-filling.
- Ao final da execução: suíte verde + `ruff` limpo.

## OWASP / superfície de ataque (gate owasp-security)

- **LLM01 Prompt Injection**: mensagem do lead como dado delimitado (SEC-LLM-1); pacote
  JSON não controla destino de handoff nem transição (FR-006/SEC-LLM-3). Mitigado.
- **LLM02 Insecure Output Handling**: saída do LLM validada por Pydantic `extra="forbid"`
  antes de qualquer ação (FR-002); output nunca eval/exec.
- **LLM06 Sensitive Info Disclosure**: sem PII/secret nos prompts; anti-PII no `log_turno`
  já coberto na Onda 1 (`tests/test_anti_pii_turno.py`). Novos campos logados são
  não-sensíveis (confiança/veredito booleano).
- **LLM04 Denial of Service / custo**: `VERIFY_TIMEOUT_SECONDS=3` + fast-path
  curto-circuito + `llm_max_tokens_per_hour`/`_Pacer` existentes limitam gasto.
- **Sem nova superfície de rede**: nenhuma dependência/endpoint novo; handoff destino da
  allowlist. Postgres/Redis inalterados.

## Progress Tracking

- [x] Constitution Check inicial (todos PASS)
- [x] research.md (Decisions 1–11)
- [x] data-model.md (RespostaEstruturada, VeredictoFidelidade, SlotQualificacao + slot_schemas)
- [x] plan.md (este documento) com mapeamento FR→arquivo ancorado
- [ ] checklist (próxima fase)
- [ ] create-tasks → execute-task → review-task

## Próximo passo

Fase `checklist` (gerar checklist de qualidade da spec/plan), depois `create-tasks`.
