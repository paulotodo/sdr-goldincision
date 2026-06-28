"""
Logging estruturado em JSON para observabilidade (FR-033/034).

Emite eventos em stdout como JSON lines:
- ticket_id, contact_number, stage, timestamp, latency_ms
- Para chamadas LLM: model_used, tokens_in, tokens_out
- Para handoff: handoff_type, destino, motivo

Nunca expoe detalhes tecnicos internos ao usuario (US7-AS3).
Implementacao completa: FASE 7, task 7.1.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def log_event(
    tipo: str,
    ticket_id: Optional[int] = None,
    contact_number: Optional[str] = None,
    stage: Optional[str] = None,
    latency_ms: Optional[int] = None,
    model_used: Optional[str] = None,
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
    detalhe: Optional[dict] = None,
) -> None:
    """
    Emite evento de observabilidade como JSON line em stdout.

    Tipos padrao: webhook_in, llm_call, message_out, handoff, erro
    Implementacao completa: FASE 7.
    """
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tipo": tipo,
    }
    if ticket_id is not None:
        event["ticket_id"] = ticket_id
    if contact_number is not None:
        event["contact_number"] = contact_number
    if stage is not None:
        event["stage"] = stage
    if latency_ms is not None:
        event["latency_ms"] = latency_ms
    if model_used is not None:
        event["model_used"] = model_used
    if tokens_in is not None:
        event["tokens_in"] = tokens_in
    if tokens_out is not None:
        event["tokens_out"] = tokens_out
    if detalhe is not None:
        event["detalhe"] = detalhe

    # Emite como JSON line (capturado por sistema de logging)
    print(json.dumps(event, ensure_ascii=False, default=str))
