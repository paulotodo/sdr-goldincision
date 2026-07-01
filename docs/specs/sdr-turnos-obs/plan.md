# Implementation Plan: Controle de Turnos Robusto + Observabilidade de Turno

**Feature**: `sdr-turnos-obs` | **Date**: 2026-07-01 | **Spec**: [spec.md](./spec.md)

## Summary

Fechar os gaps G1–G6 de controle de turnos (Pilar 7) e tornar cada turno
observável (Pilar 8) do agente SDR GoldIncision, **sem migration** e **sem
refazer** mecanismos existentes. Abordagem: contadores efêmeros no hash
Redis `estado:{chamadoId}` (turnos por sessão/nó + última interação) com
escalonamento gracioso (nudge de nó → handoff de sessão via allowlist);
detecção lazy de inatividade no início de `engine.process`; recovery de
debounce no lifespan de startup; TTL de lock elevado para cobrir o pior caso
de turno; evento JSON por turno reusando a infra existente de
`observability/log.py`; e um golden set pytest (FlowEngine real) em suíte
separada. Ordem de execução recomendada prioriza a observabilidade (US5)
cedo, para medir a duração real de turno que justifica empiricamente o TTL do
lock (US4/FR-013).

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: FastAPI, pydantic-settings, redis (async), openai,
SQLAlchemy async (Postgres) — todas já presentes; nenhuma nova.
**Storage**: Redis 7 (contadores efêmeros no hash `estado:{id}`; sem
Postgres novo). Log estruturado ao stdout.
**Testing**: pytest com FlowEngine REAL (`StubFlowEngine` stuba só I/O de
DB); golden set em suíte separada marcada `@pytest.mark.golden`.
**Target Platform**: Docker Swarm (`registry.todo-tips.com/sdr-whatsapp`),
1 réplica hoje.
**Project Type**: web-service (backend consultivo WhatsApp).
**Performance Goals**: teto de turno ~90s (pior caso: LLM + 4 envios paced +
retries); nudge/handoff determinísticos O(1) por turno.
**Constraints**: SEM migration nova; determinismo de fluxo (LLM não decide
próximo nó nem destino de handoff); verbatim nunca pelo LLM; anti
prompt-injection; 1 pergunta por mensagem; i18n PT/EN/ES; suíte inteira
verde (~320) + `ruff check app/ tests/` limpo; entrega por PR (master
protegido).
**Scale/Scope**: 1 réplica atual; pacing distribuído (G5) apenas documentado
como pré-condição para >1 réplica (não implementado — FR-014).

## Constitution Check

*GATE: passou antes do Phase 0. Re-checado após Phase 1 (§Re-check).*

| Princípio | Status | Notas |
|-----------|--------|-------|
| I. Fidelidade ao Fluxo Oficial (Mapa Mestre) | PASS | Nudge/handoff/retomada são decisões determinísticas de código em `flow.py`; o LLM não ganha poder de rotear. Etapas não são puladas; expiração volta à saudação sem inventar caminho. |
| II. Anti-Alucinação Rígida | PASS | Textos de nudge/retomada são i18n fixos (`_T`/`_t`), não gerados; golden set testa abstenção correta e zero preço inventado (FR-017/018). |
| III. Memória de Conversa sem Atrito | PASS | Expiração (FR-010) PRESERVA perfil do Contato (médico/idioma/especialidade); retomada (FR-009) não reinicia jornada; contadores em Redis (camada quente). |
| IV. Comunicação Consultiva Premium | PASS | Nudge é cordial e não corta o lead; dúvidas têm limiar elevado (FR-005); fail-open nunca degrada por falha de contador. |
| V. Elegibilidade, Objeções e Handoff Disciplinados | PASS | `handoff_destino` sempre da allowlist/config (`handoff_queue_ids_json`), nunca do LLM (FR-004, C-5). Elegibilidade médica inalterada. |
| VI. Isolamento e Segurança de Infraestrutura | PASS | Sem tocar containers de terceiros; secrets/PII protegidos por `_scrub`+`_mask_number` no evento (Decision 8); envs em stack.yml/.env.example; sem deploy live no pipeline. |
| VII. Cursos como Dados | N/A | Feature não altera catálogo nem a Admin API. |
| Regra 30 (documento oficial prevalece) | PASS | Nenhuma regra de conteúdo alterada; só controle de sessão/observabilidade. |

Nenhuma violação de princípio MUST. Sem entradas em Complexity Tracking.

## Mapeamento FR → arquivos → abordagem

| FR | Arquivo(s) | Abordagem | Princípio |
|----|-----------|-----------|-----------|
| FR-001/002 | `redis_keys.py`, `webhook.py` (`_process_consolidated_messages`) | `HINCRBY turnos_sessao`/`turnos_no_no:{etapa}` 1x/turno | III |
| FR-003 (nudge nó) | `flow.py` (`_T`/`_t` + checagem), `config.py` | Ao atingir `max_turnos_no_no`, injetar nudge i18n; não handoff | I, IV |
| FR-004 (handoff sessão) | `flow.py`, `config.py` | Ao atingir `max_turnos_sessao`, `FlowResult.handoff_destino` da allowlist | V |
| FR-005 (dúvidas) | `flow.py`, `config.py` | Limiar `max_turnos_duvidas` para ETAPA_DUVIDAS | II, IV |
| FR-006 (motivo) | `webhook.py`/`flow.py` → `log_turno` | Campo `motivo` = turnos_no_no\|turnos_sessao | — |
| FR-007 (config) | `config.py`, `.env.example`, `stack.yml` | Envs pydantic-settings | VII (dados > código) |
| FR-008 (última interação) | `webhook.py`/`flow.py`, `redis_keys.py` | `HSET ultima_interacao <epoch>` por turno | III |
| FR-009 (retomada) | `flow.py` (início de `process`), `_T` | Detecção lazy; delta > reengajamento ⇒ retomada cordial | III, IV |
| FR-010 (expiração) | `flow.py`, `memory.py` | delta > expira ⇒ etapa→saudação PRESERVANDO perfil | III |
| FR-INFRA-01 | `config.py` | Envs `reengajamento_horas`, `expira_sessao_horas`; detecção incidental (sem worker) | VI |
| FR-011/012 (debounce) | `main.py` (lifespan), `debounce.py` | SCAN `debounce:*`; reagendar ou flush imediato; flush atômico idempotente | III |
| FR-013 (lock) | `redis_keys.py` (`LOCK_TTL_MS`), `locks.py`, `config.py` | Elevar TTL ~90s (env); renovação documentada como alternativa | VI |
| FR-014 (pacing dist.) | `research.md`/`plan.md` (doc) | Documentar como pré-condição multi-réplica; NÃO implementar | VI |
| FR-015/016 (evento) | `observability/log.py` (`log_turno`), `webhook.py` | 1 evento/turno via `_emit`+`_scrub`+`_mask_number`; try/finally p/ falha | VI |
| FR-017/018 (golden) | `tests/golden/` (novos) | Casos §7; harness FlowEngine real; relatório por dimensão; suíte separada | I, II, III |
| FR-019 (preservar) | — | Não alterar anti-loop/cap-msgs/pacing/idempotência/gate-fila/debounce | I |
| FR-020 (invioláveis) | todos | verbatim, destino allowlist, elegibilidade, 1-pergunta, idioma | II, V |

## Ordem de execução recomendada

1. **US5 — Observabilidade** (`log_turno` + emissão em `_process_consolidated_messages`).
   Primeiro porque `duracao_ms` mede o pior caso real de turno, dado que
   justifica empiricamente o TTL do lock (FR-013 AC-2).
2. **US1 — Orçamento de turnos** (contadores Redis + nudge/handoff). Maior
   impacto no pedido do operador.
3. **US3 — Durabilidade do debounce** (recovery no startup). Baixo risco,
   corrige perda silenciosa.
4. **US2 — Timeout/reengajamento** (detecção lazy + preservação de perfil).
5. **US4 — Lock** (elevar TTL, agora justificado pelos dados da US5).
6. **US6 — Golden set** (regressão de jornada, suíte separada).

## Convenções de Borda

N/A — single-layer backend. A feature não atravessa fronteira
backend↔frontend nem introduz DTO/serialização de UI. As "interfaces" são
internas: campos de hash Redis (`contracts/redis-estado.md`) e evento de log
JSON ao stdout (`contracts/turno-event.md`). Convenção de nomes dos campos
Redis: `snake_case` com sufixo `:{etapa}` para o contador por-nó, fonte da
verdade em `app/core/redis_keys.py`. Campos do evento JSON: `snake_case`,
fonte da verdade em `contracts/turno-event.md` + `app/observability/log.py`.

## Project Structure

### Documentation (this feature)

```
docs/specs/sdr-turnos-obs/
├── spec.md
├── plan.md          # This file
├── research.md      # Phase 0 — 8 decisões
├── data-model.md    # Phase 1 — chaves Redis + evento + golden
├── quickstart.md    # Phase 1 — 10 cenários
└── contracts/
    ├── turno-event.md
    └── redis-estado.md
```

### Source Code (repository root — arquivos existentes a tocar)

```
app/
├── config.py                    # + envs (turnos, horas, lock)
├── main.py                      # + recovery de debounce no lifespan
├── api/
│   └── webhook.py               # incremento contador + emissão evento turno
├── core/
│   ├── flow.py                  # nudge/handoff/retomada/expiração + _T i18n
│   ├── redis_keys.py            # campos estado:{id} + LOCK_TTL_MS
│   ├── debounce.py              # helper de recovery (reagendar/flush)
│   ├── locks.py                 # TTL elevado (ou refresh documentado)
│   └── memory.py                # preservação de perfil na expiração
└── observability/
    └── log.py                   # + log_turno (reusa _emit/_scrub/_mask_number)
tests/
├── test_flow.py                 # + testes nudge/handoff/retomada/expiração
├── test_webhook.py              # + evento de turno emitido
├── test_idempotency_debounce_lock.py  # + recovery debounce + lock longo
├── test_observability.py        # + shape do evento de turno
└── golden/                      # NOVO — suíte separada
    ├── casos/*.json
    └── test_golden_runner.py
.env.example                     # + novos envs
stack.yml                        # + novos envs
```

**Structure Decision**: reuso máximo da estrutura existente; nenhum módulo
novo em `app/` (apenas `log_turno` adicionado a `observability/log.py` e
helper de recovery em `debounce.py`). Único diretório novo: `tests/golden/`.

## Complexity Tracking

> Nenhuma violação de constitution — seção vazia por design.

## Re-check pós-design (Phase 1)

Revalidado após data-model/contracts/quickstart: o design não introduziu
serviço, camada ou dependência nova; contadores efêmeros e evento de log são
aditivos; determinismo de fluxo e disciplina de handoff preservados; secrets/
PII cobertos por scrub+mask. **Constitution Check permanece PASS em todos os
princípios MUST.** NEEDS CLARIFICATION restantes: 0.

## Próximos Passos

1. `/checklist` — quality gate antes de implementar.
2. `/create-tasks` — decompor este plano em backlog executável.
3. `/analyze` — validar consistência spec↔plan↔tasks (após tasks).
