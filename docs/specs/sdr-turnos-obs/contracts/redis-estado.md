# Contract: Campos Redis do hash `estado:{chamadoId}`

Interface interna: campos adicionados ao hash de sessão existente
(`app/core/redis_keys.py::estado_key`). Sem migration. Operações atômicas
do Redis 7.

## Campos e operações

| Campo | Operação de escrita | Operação de leitura | Semântica |
|-------|---------------------|---------------------|-----------|
| `turnos_sessao` | `HINCRBY estado:{id} turnos_sessao 1` | `HGET` | Total de turnos da sessão. |
| `turnos_no_no:{etapa}` | `HINCRBY estado:{id} turnos_no_no:{etapa} 1` | `HGET` | Turnos na etapa corrente. |
| `ultima_interacao` | `HSET estado:{id} ultima_interacao <epoch>` | `HGET` | Última atividade (epoch seconds). |

## Invariantes

- **R-1 (FR-002)**: `HINCRBY` garante incremento atômico exatamente 1x por
  turno, mesmo sob concorrência (idempotência do incremento é
  responsabilidade do chamador: incrementar uma única vez por
  `_process_consolidated_messages`).
- **R-2 (Fail-open, Decision 2)**: leitura ausente (`HGET` → nil) é tratada
  como `0` (contadores) ou "interação recente" (`ultima_interacao`), nunca
  bloqueando o atendimento.
- **R-3 (Precedência)**: o consumidor avalia `turnos_sessao` (teto de
  sessão) ANTES de `turnos_no_no` (teto de nó); handoff de sessão precede
  nudge de nó.
- **R-4 (Reset por-nó)**: ao mudar `etapa_mapa_mestre`, o campo
  `turnos_no_no:{etapa_anterior}` deixa de ser lido/incrementado; opcional
  `HDEL` para higiene.
- **R-5 (Sem TTL novo)**: os campos herdam o TTL do hash `estado:{id}`.

## Lock por ticket (ajuste — FR-013)

| Constante | Antes | Depois | Local |
|-----------|-------|--------|-------|
| `LOCK_TTL_MS` | `30_000` | `~90_000` (env-driven) | `app/core/redis_keys.py:20` / `app/config.py` |

Invariante **L-1**: o TTL do lock (`SET NX PX`) deve cobrir o pior caso de
turno medido via `duracao_ms` (evento de turno). Valor 90s é ponto de
partida; confirmar/ajustar com dados reais (FR-013 AC-2).
