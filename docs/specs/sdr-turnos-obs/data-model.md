# Data Model: sdr-turnos-obs

> **Sem migration.** Todos os dados abaixo são efêmeros (Redis) ou eventos de
> log estruturado (stdout). Nenhuma tabela/coluna Postgres nova. As entidades
> conceituais da spec mapeiam a campos do hash Redis existente e ao schema do
> evento de turno.

## Entity: Contadores de sessão (hash Redis `estado:{chamadoId}`)

Chave: `estado_key(chamado_id)` → `estado:{chamadoId}`
(`app/core/redis_keys.py:56`). Hash já existente; adicionamos campos.

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| `turnos_sessao` | int (string no Redis) | >= 0; `HINCRBY` 1x/turno | Total de turnos da sessão. Ausente ⇒ tratar como 0 (fail-open). |
| `turnos_no_no:{etapa}` | int | >= 0; `HINCRBY` 1x/turno na etapa corrente | Turnos dentro do nó. Reinicia ao mudar `etapa_mapa_mestre` (HDEL do campo anterior ou ignorá-lo). |
| `ultima_interacao` | epoch seconds (int) | ISO/epoch; `HSET` a cada turno | Marca de última atividade (US2). Ausente/corrompido ⇒ trata como recente. |

**TTL**: herda a política de `estado:{id}` (sessão). Não introduz TTL novo.

### Regras de manipulação

- **Incremento (FR-002)**: no início do processamento do turno em
  `_process_consolidated_messages`/`engine.process`, `HINCRBY turnos_sessao 1`
  e `HINCRBY turnos_no_no:{etapa_entrada} 1` — exatamente 1x por turno.
- **Reset por-nó (Decision 2)**: ao detectar mudança de
  `etapa_mapa_mestre`, o campo `turnos_no_no:{etapa_anterior}` deixa de ser
  incrementado (o novo campo `turnos_no_no:{nova_etapa}` inicia em 0).
- **Marca de atividade (FR-008)**: `HSET ultima_interacao <agora>` a cada
  turno processado.

### State transitions (orçamento de turnos — US1)

```
turnos_no_no < teto_no                → fluxo normal
turnos_no_no == teto_no (ou teto_duvidas na ETAPA_DUVIDAS)  → NUDGE (lead segue)
turnos_sessao >= teto_sessao          → HANDOFF (destino lógico allowlist)   [precede o nudge]
```

Limiares (envs, Decision 1 / FR-007):
`MAX_TURNOS_NO_NO=6`, `MAX_TURNOS_SESSAO=25`, `MAX_TURNOS_DUVIDAS=12`.

### State transitions (inatividade — US2)

```
delta = agora - ultima_interacao
delta <= REENGAJAMENTO_HORAS                          → normal
REENGAJAMENTO_HORAS < delta <= EXPIRA_SESSAO_HORAS    → retomada cordial (mantém etapa)
delta > EXPIRA_SESSAO_HORAS                           → sessão nova (etapa→saudação),
                                                         PRESERVA perfil do Contato
```

Limiares (envs / FR-INFRA-01): `REENGAJAMENTO_HORAS=24`,
`EXPIRA_SESSAO_HORAS=72`.

## Entity: Registro de Turno (evento de log estruturado)

Emitido por `log_turno(...)` (novo, em `app/observability/log.py`) →
`_scrub` → `_mask_number` → `_emit` (stdout JSON). Um evento por turno
(FR-015), inclusive em falha (FR-016).

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| `event` | string | const `"turno"` | Discriminador do evento. |
| `chamado_id` | int | obrigatório | Identificador do atendimento. |
| `turno_sessao` | int | >= 1 | Valor de `turnos_sessao` após incremento. |
| `etapa_entrada` | string | obrigatório | Etapa no início do turno. |
| `etapa_saida` | string | obrigatório | Etapa ao fim do turno (pode == entrada). |
| `intencao` | string | nullable | Intenção classificada (`intent.py`). |
| `idioma` | enum `pt`\|`en`\|`es` | obrigatório | Idioma do lead. |
| `n_blocos_enviados` | int | >= 0 | Blocos efetivamente enviados (≤ `max_msgs_per_turn`). |
| `acao` | enum | ver abaixo | Ação resultante do turno. |
| `handoff_destino` | string | nullable | Destino lógico (allowlist) quando `acao=handoff`. |
| `duracao_ms` | int | >= 0 | Duração do processamento (relógio monotônico). |
| `tentativas` | int | >= 0 | Valor corrente do contador anti-loop da etapa. |
| `motivo` | string | nullable | `turnos_no_no` \| `turnos_sessao` quando nudge/handoff (FR-006). |

Enum `acao`: `resposta` | `nudge` | `handoff` | `retomada` | `sessao_nova`
| `erro`.

### Invariantes de segurança (Decision 8)

- Nenhum campo carrega conteúdo bruto da mensagem do lead (SEC-LLM-1).
- Número/telefone, se presente em qualquer subestrutura, é mascarado por
  `_mask_number`.
- `_scrub` remove chaves sensíveis (tokens/keys) antes de `_emit`.

## Entity: Caso de Referência (golden set — arquivo de teste)

Arquivo(s) em `tests/golden/` (JSON/YAML). Não é dado de runtime; é fixture
de teste.

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | Identificador do caso (ex: `golden-c1-preco-01`). |
| `mensagem` | string | Entrada do lead. |
| `estado_inicial` | object | Etapa + slots já preenchidos do Contato/Ticket. |
| `esperado.proxima_acao` | string | Ação esperada do FlowEngine. |
| `esperado.etapa` | string | Etapa de destino esperada. |
| `esperado.nao_repetir_slot` | string[] | Slots que NÃO podem ser re-perguntados. |
| `esperado.abster` | bool (opt) | Caso fora da Base → recusa esperada. |
| `esperado.sem_preco_inventado` | bool (opt) | Assert de anti-alucinação. |

### Relationships

- `Registro de Turno` referencia `chamado_id` (mesma chave lógica do
  `estado:{chamadoId}` e do `Ticket`), sem FK física (é log).
- `Contadores de sessão` e `Registro de Turno` compartilham `chamado_id`
  como chave natural de correlação em análise.
