"""
Helpers de chave Redis com prefixos convencionados (task 2.2.1).

Implementa as chaves definidas em data-model.md §Estruturas Redis:

| Chave                               | Tipo   | TTL  | Proposito              |
|-------------------------------------|--------|------|------------------------|
| idemp:{chamadoId}:{sha256}          | string | 24h  | idempotencia de evento |
| debounce:{chamadoId}                | list   | 8s+  | buffer de rajada       |
| lock:ticket:{chamadoId}             | string | 90s  | serializacao           |
| sessao:{chamadoId}:hot              | list   | sess | janela quente          |
| estado:{chamadoId}                  | hash   | sess | cache de variaveis     |

Todos os prefixos sao constantes aqui — nunca inline no codigo.
"""
from __future__ import annotations

# TTLs em segundos
IDEMP_TTL_SECONDS = 86_400          # 24h — idempotencia de evento
# Default de referencia (30s). O valor EFETIVO/env-driven vem de
# settings.lock_ttl_ms (app/config.py, default 90000) e e aplicado em
# app/core/locks.py (FASE 6, task 1.1.3/6.1.1) — mantido aqui apenas como
# fallback documental para nao quebrar quem ainda importa esta constante.
LOCK_TTL_MS = 30_000                # 30s em ms — lock por ticket (ver settings.lock_ttl_ms)
HOT_WINDOW_TTL_SECONDS = 7_200      # 2h — janela quente de sessao

# ---------------------------------------------------------------------------
# Campos do hash `estado:{chamadoId}` — orcamento de turnos (US1, FASE 3,
# task 3.1.1). Nao sao chaves Redis proprias: vivem dentro do hash retornado
# por `estado_key()`, ao lado das demais variaveis de cache de estado.
# ---------------------------------------------------------------------------
TURNOS_SESSAO_FIELD = "turnos_sessao"            # contador de turnos da sessao inteira
TURNOS_NO_NO_FIELD = "turnos_no_no"              # contador de turnos no NO/etapa corrente
TURNOS_NO_NO_ETAPA_FIELD = "turnos_no_no_etapa"  # marca qual etapa o contador acima referencia

# Timeout de inatividade e reengajamento (US2, FASE 5, task 5.1.1). Tambem
# vive no hash `estado:{chamadoId}`, ao lado dos campos acima.
ULTIMA_INTERACAO_FIELD = "ultima_interacao"      # epoch seconds (int) da ultima interacao


def idemp_key(chamado_id: int, payload_hash: str) -> str:
    """
    Chave de idempotencia: `idemp:{chamadoId}:{sha256(payload)}`.
    TTL: 24h. SET NX EX 86400.
    """
    return f"idemp:{chamado_id}:{payload_hash}"


def debounce_key(chamado_id: int) -> str:
    """
    Chave de debounce: `debounce:{chamadoId}`.
    Tipo: LIST. TTL: janela + margem.
    """
    return f"debounce:{chamado_id}"


def lock_key(chamado_id: int) -> str:
    """
    Chave de lock por ticket: `lock:ticket:{chamadoId}`.
    Tipo: string. TTL: 30s (PX 30000). SET NX PX 30000.
    """
    return f"lock:ticket:{chamado_id}"


def hot_window_key(chamado_id: int) -> str:
    """
    Chave de janela quente de mensagens: `sessao:{chamadoId}:hot`.
    Tipo: LIST. TTL: por sessao.
    """
    return f"sessao:{chamado_id}:hot"


def estado_key(chamado_id: int) -> str:
    """
    Chave de cache de estado/variaveis: `estado:{chamadoId}`.
    Tipo: HASH. TTL: por sessao.
    """
    return f"estado:{chamado_id}"
