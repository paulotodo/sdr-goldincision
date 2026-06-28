"""
Idempotencia de eventos de webhook por chamadoId+hash de conteudo.

Previne duplo-processamento em reenvios do n8n (FR-037-INFRA-IDEMP).
Implementa: data-model.md §Estruturas Redis `idemp:{chamadoId}:{sha256}`.

Mecanismo:
- Computar SHA-256 do payload recebido
- SET NX EX 86400 `idemp:{chamadoId}:{sha256}` -> valor "1"
- SE NX falhou (chave ja existe) -> evento ja foi processado -> descartar

Implementacao completa: FASE 3, task 3.2.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_IDEMP_TTL_SECONDS = 86400  # 24h


def compute_payload_hash(payload: Any) -> str:
    """Computa SHA-256 do payload para chave de idempotencia."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyChecker:
    """
    Verifica e registra eventos processados usando Redis.
    Chave: `idemp:{chamadoId}:{sha256}`, TTL 24h.
    STUB: implementacao completa em FASE 3, task 3.2.
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    async def is_duplicate(self, chamado_id: int, payload: Any) -> bool:
        """
        Verifica se evento ja foi processado.
        Returns:
            True se duplicata (deve descartar), False se novo.
        """
        # TODO (FASE 3): SET NX EX 86400 na chave
        # TODO (FASE 3): retornar True se SET retornou None (chave ja existia)
        raise NotImplementedError("IdempotencyChecker implementado em FASE 3")
