"""
Idempotencia de eventos de webhook por chamadoId+hash de conteudo.

Previne duplo-processamento em reenvios do n8n (FR-037-INFRA-IDEMP).
Implementa: data-model.md §Estruturas Redis `idemp:{chamadoId}:{sha256}`.

Mecanismo:
- Computar SHA-256 do payload recebido (serializado canonicamente)
- SET NX EX 86400 `idemp:{chamadoId}:{sha256}` -> valor "1"
- SE NX falhou (chave ja existe) -> evento ja foi processado -> descartar

Implementacao: FASE 3, task 3.2.1
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.core.redis_keys import IDEMP_TTL_SECONDS, idemp_key

logger = logging.getLogger(__name__)


def compute_payload_hash(payload: Any) -> str:
    """
    Computa SHA-256 do payload para chave de idempotencia.

    Serializacao canonica (sort_keys=True, ensure_ascii=False) garante
    que a mesma mensagem com campos em ordem diferente produz o mesmo hash.
    """
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyChecker:
    """
    Verifica e registra eventos processados usando Redis SET NX EX.

    Chave: `idemp:{chamadoId}:{sha256}`, TTL 24h.
    Thread-safe: SET NX e atomico no Redis.
    """

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def is_duplicate(self, chamado_id: int, payload: Any) -> bool:
        """
        Verifica se evento ja foi processado (e registra se for novo).

        Atomico: SET NX retorna True se a chave foi criada (evento novo),
        None/False se a chave ja existia (duplicata).

        Returns:
            True  -> duplicata (descartar sem processar)
            False -> evento novo (processar e marcar como processado)
        """
        payload_hash = compute_payload_hash(payload)
        key = idemp_key(chamado_id, payload_hash)

        # SET NX EX 86400: retorna True se criou, None se ja existia
        result = await self._redis.set(key, "1", nx=True, ex=IDEMP_TTL_SECONDS)
        is_dup = result is None

        if is_dup:
            logger.debug(
                "idempotency: duplicata descartada chamado_id=%s hash=%s",
                chamado_id,
                payload_hash[:16],
            )
        else:
            logger.debug(
                "idempotency: evento novo registrado chamado_id=%s hash=%s",
                chamado_id,
                payload_hash[:16],
            )

        return is_dup

    async def mark_processed(self, chamado_id: int, payload: Any) -> None:
        """
        Marca evento como processado sem verificar duplicata.
        Util para re-marcar apos processamento bem-sucedido.
        """
        payload_hash = compute_payload_hash(payload)
        key = idemp_key(chamado_id, payload_hash)
        await self._redis.set(key, "1", ex=IDEMP_TTL_SECONDS)
