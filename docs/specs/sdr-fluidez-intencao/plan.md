# Implementation Plan: Fluidez Agêntica de Intenção no Atendimento SDR

**Feature**: `sdr-fluidez-intencao` | **Date**: 2026-07-02 | **Spec**: [spec.md](./spec.md)

## Summary

Tornar a **interpretação** das mensagens do lead mais fluida e humana em
três frentes — (1) reconhecer correção de rumo em qualquer ponto da
jornada, (2) aceitar texto livre (com erro leve) no menu inicial, (3)
reformular sem repetir o bloco anterior verbatim — mantendo o motor de
decisão de fluxo **100% determinístico** (nenhuma capacidade de IA decide
para onde a conversa vai). Abordagem: um léxico compartilhado
determinístico (marcadores de correção + nomes de produto/caminho,
normalizados) reusado pelo menu e pelo novo detector de troca; quando o
léxico não resolve, um fallback agentico **confidence-gated** reusando o
`SlotExtractor` já existente (Pilar 8) — não o `IntentClassifier.classify()`,
cujo contrato de 2-tupla está protegido por gotcha e já colapsa confiança
baixa internamente sem expor um valor numérico configurável. O detector
vive num único choke-point (`_reformular_ou_handoff`), o que satisfaz por
construção que uma resposta legítima nunca é lida como troca (FR-009) e que
o resolver do nó sempre tenta primeiro (FR-001). Investigação empírica
confirmou a causa raiz do bug relatado: o bloco `"sistema_etapa1_2"`
(`app/core/flow.py:354`) é reusado goela-abaixo como bloco de entrada E como
pergunta de reformulação, reproduzindo a repetição verbatim (incluindo
saudação) relatada na spec — corrigida separando pergunta-curta reformulável
de bloco-de-entrada, com variantes cíclicas determinísticas por tentativa.
Observabilidade estende (aditivamente) o evento de turno já existente de
`sdr-turnos-obs`, sem novo evento. Sem migration — todo estado novo é
efêmero (Redis, mesmo padrão de `overflow_blocos`/`overflow_idioma`).

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: FastAPI, pydantic-settings, redis (async), openai,
SQLAlchemy async (Postgres) — todas já presentes; nenhuma nova.
**Storage**: Redis 7 (novo campo `troca_pendente` no hash `estado:{id}`;
sem Postgres novo). Log estruturado ao stdout (extensão aditiva de
`log_turno`).
**Testing**: pytest com FlowEngine REAL (`StubFlowEngine` stuba só I/O de
DB); mock somente do client OpenAI (fallback agentico via `SlotExtractor`);
golden set em suíte separada marcada `@pytest.mark.golden` (extensão de
`tests/golden/`, hoje 64 casos + 636 testes na suíte principal, master
`225b844`, produção `2.1.2`).
**Target Platform**: Docker Swarm
(`registry.todo-tips.com/sdr-whatsapp`), 1 réplica hoje.
**Project Type**: web-service (backend consultivo WhatsApp).
**Performance Goals**: detector determinístico O(1) por turno (normalização +
matching por token/substring); fallback agentico só quando o léxico não
resolve — mesmo teto de latência já aceito para os demais fallbacks de
`SlotExtractor` existentes (Pilar 8), sem novo orçamento de tempo.
**Constraints**: SEM migration nova; determinismo de fluxo (LLM nunca decide
próximo caminho/nó nem destino de handoff — só extrai candidato+confiança);
verbatim nunca reescrito pelo LLM; anti prompt-injection (mensagem do lead
sempre dado, nunca instrução); 1 pergunta por mensagem (exceto menus);
i18n PT/EN/ES; `_MAX_TENTATIVAS=3`, `max_msgs_per_turn=4`, pacer+429,
idempotência, lock, gate de fila IA (`AI_QUEUE_ID=77`), debounce 8s
preservados sem alteração; Ondas 1/2/3 (`sdr-turnos-obs`) e fixes #16/#17
(overflow-resume) preservados por construção (Research Decision 5); suíte
inteira verde (636 + golden 64) + `ruff check app/ tests/` limpo.
**Scale/Scope**: 1 réplica atual; feature não introduz nenhuma dependência
de coordenação multi-instância.

## Constitution Check

*GATE: passou antes do Phase 0 (`feature-00c-preflight.sh`, onda anterior,
`ok=true`, sem findings). Re-checado após Phase 1 (§Re-check).*

| Princípio | Status | Notas |
|-----------|--------|-------|
| I. Fidelidade ao Fluxo Oficial (Mapa Mestre) | PASS | Roteamento continua 100% determinístico; o LLM (via `SlotExtractor`) só extrai candidato+confiança, nunca decide o caminho — a comparação ao limiar é código (`FlowEngine`). Mapa Mestre e ordem das etapas preservados; troca reinicia o caminho-alvo do zero (FR-021), nunca pula etapa. Léxico compartilhado testável e rastreável a FR-002/003/011. |
| II. Anti-Alucinação Rígida | PASS | Léxico determinístico não gera conteúdo; `SlotExtractor` já é fail-safe (erro/baixa confiança → `None`, nunca adivinha, `contracts/slot-troca-caminho.md` S-3). Textos de confirmação/reformulação são i18n fixos (`_t`), não gerados livremente pelo LLM. Apresentações e conteúdo oficial continuam verbatim, sem tocar. |
| III. Memória de Conversa e Jornada Sem Atrito | PASS | Perfil do lead preservado por construção através de trocas (`data-model.md` §Relationships — nenhum campo depende de `caminho`); reformulação humanizada substitui a repetição robótica relatada (US3); nunca requalifica informação já respondida. |
| IV. Comunicação Consultiva Premium | PASS | Confirmação breve e natural antes de trocar quando não há marcador explícito (edge case); 1 pergunta por mensagem preservado; reformulação com variação evita "sensação robótica"; prioridade continua sendo o atendimento correto (nunca troca silenciosamente sem evidência suficiente). |
| V. Elegibilidade, Objeções e Handoff Disciplinados | PASS | `_MAX_TENTATIVAS`/encaminhamento automático a humano preservados sem alteração de escopo/limite (FR-016); `handoff_destino` continua resolvido só pela allowlist estática (`_DESTINO_POR_CAMINHO`), nunca pelo LLM; elegibilidade médica não é tocada pela troca de caminho. |
| VI. Isolamento e Segurança de Infraestrutura | PASS | Sem tocar containers/infra de terceiros; único estado novo é efêmero (Redis, `troca_pendente`, mesmo padrão auditado de `overflow_blocos`); novo env (`INTENT_SWITCH_CONFIDENCE_THRESHOLD`) documentado em `config.py`/`.env.example`/`stack.yml`; nenhum secret novo; sem deploy live neste pipeline (entrega por PR). |
| VII. Cursos como Dados | N/A | Feature não altera catálogo de cursos nem a Admin API. |
| Regra 30 (documento oficial prevalece) | PASS | Nenhum conteúdo oficial é reescrito ou resumido; a feature melhora apenas a INTERPRETAÇÃO da entrada do lead, nunca a saída de conteúdo oficial. |

Nenhuma violação de princípio MUST. Sem entradas em Complexity Tracking.

## Mapeamento FR → arquivos → abordagem

| FR | Arquivo(s) | Abordagem | Princípio |
|----|-----------|-----------|-----------|
| FR-001 (resolver primeiro) | `flow.py` (`_reformular_ou_handoff`) | Detector só roda depois que o resolver do nó já retornou `None` (Decision 3) | I |
| FR-002/003 (avaliação determinística + tolerância a variação) | `flow.py` (`_LEXICO_CAMINHOS`, `_MARCADORES_CORRECAO`, reuso de `_norm`) | Constantes + matching por token/substring (Decision 1) | I |
| FR-004 (fallback agentico confidence-gated) | `interpret.py` (`_SLOT_SCHEMA_TROCA_CAMINHO`), `config.py` (`INTENT_SWITCH_CONFIDENCE_THRESHOLD`), `flow.py` | Reuso de `SlotExtractor`, não `IntentClassifier` (Decision 2) | II |
| FR-005 (confirmar e conduzir) | `flow.py` (novo bloco i18n de confirmação de troca) | Despacho para `_despachar_caminho` após confirmação (Decision 3) | IV |
| FR-006 (preservar perfil) | — | Sem código novo — perfil já é independente de `caminho` (`data-model.md` §Relationships) | III |
| FR-007 (zerar contador da etapa abandonada) | `flow.py` (`_tent_clear`, já existente) | Chamado no despacho da troca | III |
| FR-008/012 (desambiguação exatamente 2 caminhos) | `flow.py`, `redis_keys.py`, `memory.py` (`troca_pendente`) | Novo estado transiente (Decision 6) | I, IV |
| FR-009 (resposta legítima nunca é troca) | — | Satisfeito por construção (Decision 3) | I |
| FR-010 (fallback ao comportamento existente) | `flow.py` (`_reformular_ou_handoff`) | Sem match em nenhum estágio → reformulação/handoff atual | I |
| FR-011/013 (menu texto livre + typo) | `flow.py` (bloco "2.bis Menu", `app/core/flow.py:1283`) | Reuso do MESMO `_LEXICO_CAMINHOS` (Decision 1) | I |
| FR-014/015 (reformulação sem repetição, ciclo determinístico) | `flow.py` (`_reformular_ou_handoff`, novo `_REFORMULACOES`) | Corrige causa raiz achada (`sistema_etapa1_2`); ciclo por tentativa (Decision 7) | IV |
| FR-016 (limite de tentativas preservado) | — | `_MAX_TENTATIVAS`/`_tent_bump` intocados | I |
| FR-017/018 (registro aditivo) | `observability/log.py` (`log_turno`), `api/webhook.py` | Campos opcionais novos (Decision 8) | VI |
| FR-019 (não alterar autoridade determinística/verbatim) | — | Nenhuma alteração estrutural; validado pelo golden set | I, II |
| FR-020 (salvaguardas preservadas) | — | Nenhuma alteração; validado por testes de regressão (Decision 9) | I, III, IV |
| FR-021 (reiniciar caminho já visitado do zero) | `flow.py` (despacho sempre entra pela etapa inicial do caminho-alvo) | Nunca retoma etapa salva de visita anterior | I |

## Ordem de execução recomendada

1. **Fundação** — léxico compartilhado (`_LEXICO_CAMINHOS`/`_MARCADORES_CORRECAO`,
   Decision 1) + novo slot schema do `SlotExtractor` (Decision 2) + novo env
   `INTENT_SWITCH_CONFIDENCE_THRESHOLD`. Pré-requisito de US1 e US2.
2. **US1 — Correção de rumo mid-jornada** (P1). Detector centralizado em
   `_reformular_ou_handoff` (Decision 3), preservação da supressão do fix #9
   na classificação global (Decision 4), estado `troca_pendente` para
   confirmação/desambiguação (Decision 6), precedência do overflow-resume
   verificada por regressão (Decision 5). Maior impacto — resolve
   diretamente o caso real relatado.
3. **US2 — Menu em texto livre** (P2). Reuso do léxico da Fundação no
   fast-path do menu (`app/core/flow.py:1283`).
4. **US3 — Reformulação humanizada** (P2). Corrige a causa raiz encontrada
   (Decision 7) — bloco `"sistema_etapa1_2"` e demais call sites de
   `_reformular_ou_handoff` auditados individualmente em `/create-tasks`.
5. **US4 — Observabilidade** (P3). Extensão aditiva de `log_turno`
   (Decision 8) — feito por último porque depende dos eventos já existirem
   nos fluxos acima para ter o que registrar.
6. **Golden set de ponta a ponta** (Decision 9) — consolidado ao longo de
   cada US (cada uma adiciona seus casos), com verificação final combinada.

## Convenções de Borda

N/A — single-layer backend. A feature não atravessa fronteira
backend↔frontend nem introduz DTO/serialização de UI. As "interfaces" são
internas: um novo campo de hash Redis (`contracts/estado-troca-pendente.md`),
um novo slot schema do `SlotExtractor` (`contracts/slot-troca-caminho.md`) e
uma extensão aditiva do evento de log JSON já existente
(`contracts/turno-event-extensao.md`). Convenção de nomes: `snake_case` em
todos os três, consistente com o padrão já estabelecido em
`sdr-turnos-obs/plan.md` §Convenções de Borda.

## Project Structure

### Documentation (this feature)

```
docs/specs/sdr-fluidez-intencao/
├── spec.md
├── plan.md                        # This file
├── research.md                    # Phase 0 — 10 decisoes
├── data-model.md                  # Phase 1 — lexico + estado Redis + evento + golden
├── quickstart.md                  # Phase 1 — 15 cenarios
└── contracts/
    ├── slot-troca-caminho.md
    ├── estado-troca-pendente.md
    └── turno-event-extensao.md
```

### Source Code (repository root — arquivos existentes a tocar)

```
app/
├── config.py                    # + INTENT_SWITCH_CONFIDENCE_THRESHOLD
├── core/
│   ├── flow.py                  # lexico compartilhado, detector em
│   │                             #   _reformular_ou_handoff, estado de
│   │                             #   confirmacao/desambiguacao pendente,
│   │                             #   reformulacao ciclica (_REFORMULACOES),
│   │                             #   _tent_clear na troca, i18n novo
│   ├── interpret.py             # + _SLOT_SCHEMA_TROCA_CAMINHO (SlotExtractor)
│   ├── redis_keys.py            # + TROCA_PENDENTE_FIELD no hash estado:{id}
│   ├── memory.py                # + SessionContext.troca_caminho_pendente
│   └── intent.py                # NENHUMA mudanca (contrato 2-tupla preservado)
└── observability/
    └── log.py                   # log_turno + campos aditivos
app/api/webhook.py                # repassar novos campos a log_turno
tests/
├── test_flow.py                 # + testes do detector/lexico/reformulacao/desambiguacao
├── test_troca_caminho.py        # NOVO (ou extensao de test_flow.py — decisao de /create-tasks)
├── test_observability.py        # + shape estendido do evento
└── golden/casos/*.json          # + novos casos (US1-US4 + edge cases)
.env.example                     # + INTENT_SWITCH_CONFIDENCE_THRESHOLD
stack.yml                        # + INTENT_SWITCH_CONFIDENCE_THRESHOLD
```

**Structure Decision**: reuso máximo da estrutura existente; nenhum módulo
novo em `app/` (extensão de `flow.py`/`interpret.py`/`redis_keys.py`/
`memory.py`/`log.py`, mesmo padrão de `sdr-turnos-obs`). Único arquivo de
teste possivelmente novo é `tests/test_troca_caminho.py` (dedicado ao
detector), decisão final cabe a `/create-tasks`.

## Complexity Tracking

> Nenhuma violação de constitution — seção vazia por design.

## Re-check pós-design (Phase 1)

Revalidado após data-model/contracts/quickstart: o design não introduz
serviço, camada ou dependência nova; o único estado novo (`troca_pendente`)
é efêmero e segue padrão já auditado; o fallback agentico reusa infra
existente (`SlotExtractor`) em vez de criar um segundo sistema de
classificação; a extensão de observabilidade é puramente aditiva. Nenhum
princípio MUST é violado; o roteamento determinístico e a disciplina de
handoff permanecem centralizados na allowlist estática. **Constitution
Check permanece PASS em todos os princípios MUST.** NEEDS CLARIFICATION
restantes: 0.

## Próximos Passos

1. `/checklist` — quality gate antes de implementar.
2. `/create-tasks` — decompor este plano em backlog executável (incluindo a
   auditoria individual dos ~10 call sites de `_reformular_ou_handoff`
   citada em Research Decision 7).
3. `/analyze` — validar consistência spec↔plan↔tasks (após tasks).
