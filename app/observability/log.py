"""
Logging estruturado em JSON para observabilidade (FR-033/034, US7).

Emite eventos em stdout como JSON lines — compativel com qualquer
coletor de logs (Loki, CloudWatch, ELK).

Campos por tipo de evento:
- webhook_in : ticket_id, contact_number, stage, latency_ms
- llm_call   : ticket_id, stage, model_used, tokens_in, tokens_out, latency_ms
- message_out: ticket_id, contact_number, stage
- handoff    : ticket_id, contact_number, handoff_type, destino, motivo
- erro       : ticket_id, stage, detalhe (sem expor stack trace ao usuario)

Regras de seguranca:
- NUNCA logar secrets (token, openai key) — FR-032.
- NUNCA expor detalhe tecnico ao usuario — US7-AS3.
- contact_number e logado com mascara parcial (primeiros 4 digitos + **).
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)

# Campos que NUNCA devem aparecer em logs (filtragem defensiva)
_FORBIDDEN_KEYS = frozenset(
    {
        "token", "secret", "password", "senha", "openai_api_key",
        "chatmaster_token", "admin_token", "webhook_token",
        "authorization", "bearer",
    }
)


def _mask_number(number: Optional[str]) -> Optional[str]:
    """Mascara parcial do numero de telefone (primeiros 4 digitos + **)."""
    if not number:
        return number
    visible = number[:4]
    return f"{visible}****"


def _scrub(obj: Any, depth: int = 0) -> Any:
    """Remove recursivamente chaves suspeitas de dict (defesa em profundidade)."""
    if depth > 5:
        return obj
    if isinstance(obj, dict):
        return {
            k: _scrub(v, depth + 1)
            for k, v in obj.items()
            if k.lower() not in _FORBIDDEN_KEYS
        }
    if isinstance(obj, list):
        return [_scrub(i, depth + 1) for i in obj]
    return obj


def _emit(event: dict) -> None:
    """Emite evento como JSON line em stdout (via print para compatibilidade)."""
    try:
        safe = _scrub(event)
        print(json.dumps(safe, ensure_ascii=False, default=str), flush=True)
    except Exception:
        # Nunca propagar erro de logging
        pass


def log_webhook_in(
    ticket_id: Optional[int] = None,
    contact_number: Optional[str] = None,
    stage: Optional[str] = None,
    latency_ms: Optional[int] = None,
    num_mensagens: Optional[int] = None,
) -> None:
    """Registra evento de recepcao de webhook."""
    event: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tipo": "webhook_in",
    }
    if ticket_id is not None:
        event["ticket_id"] = ticket_id
    if contact_number is not None:
        event["contact_number"] = _mask_number(contact_number)
    if stage is not None:
        event["stage"] = stage
    if latency_ms is not None:
        event["latency_ms"] = latency_ms
    if num_mensagens is not None:
        event["num_mensagens"] = num_mensagens
    _emit(event)


def log_llm_call(
    ticket_id: Optional[int] = None,
    stage: Optional[str] = None,
    model_used: Optional[str] = None,
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
    latency_ms: Optional[int] = None,
) -> None:
    """Registra chamada ao LLM com uso/custo de tokens (FR-033)."""
    event: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tipo": "llm_call",
    }
    if ticket_id is not None:
        event["ticket_id"] = ticket_id
    if stage is not None:
        event["stage"] = stage
    if model_used is not None:
        event["model_used"] = model_used
    if tokens_in is not None:
        event["tokens_in"] = tokens_in
    if tokens_out is not None:
        event["tokens_out"] = tokens_out
    if latency_ms is not None:
        event["latency_ms"] = latency_ms
    _emit(event)


def log_message_out(
    ticket_id: Optional[int] = None,
    contact_number: Optional[str] = None,
    stage: Optional[str] = None,
    num_blocos: Optional[int] = None,
) -> None:
    """Registra envio de mensagem ao lead."""
    event: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tipo": "message_out",
    }
    if ticket_id is not None:
        event["ticket_id"] = ticket_id
    if contact_number is not None:
        event["contact_number"] = _mask_number(contact_number)
    if stage is not None:
        event["stage"] = stage
    if num_blocos is not None:
        event["num_blocos"] = num_blocos
    _emit(event)


def log_handoff(
    ticket_id: Optional[int] = None,
    contact_number: Optional[str] = None,
    handoff_type: str = "fila",
    destino: Optional[str] = None,
    motivo: Optional[str] = None,
) -> None:
    """
    Registra evento de handoff (FR-034).

    handoff_type: 'fila' | 'conexao' | 'paciente_modelo' | 'end'
    """
    event: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tipo": "handoff",
        "handoff_type": handoff_type,
    }
    if ticket_id is not None:
        event["ticket_id"] = ticket_id
    if contact_number is not None:
        event["contact_number"] = _mask_number(contact_number)
    if destino is not None:
        event["destino"] = destino
    if motivo is not None:
        event["motivo"] = motivo
    _emit(event)


def log_erro(
    ticket_id: Optional[int] = None,
    stage: Optional[str] = None,
    tipo_erro: str = "erro",
    detalhe: Optional[str] = None,
    contact_number: Optional[str] = None,
) -> None:
    """
    Registra erro tecnico (FR-033, US7-AS3).

    O 'detalhe' e para logs internos APENAS — nunca exposto ao usuario.
    """
    event: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tipo": tipo_erro,
    }
    if ticket_id is not None:
        event["ticket_id"] = ticket_id
    if stage is not None:
        event["stage"] = stage
    if contact_number is not None:
        event["contact_number"] = _mask_number(contact_number)
    if detalhe:
        event["detalhe"] = detalhe  # tecnico: para logs, nao para o lead
    _emit(event)


# Acoes possiveis do evento de turno (data-model.md §Entity Registro de Turno)
_TURNO_ACOES = frozenset(
    {"resposta", "nudge", "handoff", "retomada", "sessao_nova", "erro"}
)


def log_turno(
    chamado_id: int,
    turno_sessao: int,
    etapa_entrada: str,
    etapa_saida: str,
    idioma: str,
    n_blocos_enviados: int,
    acao: str,
    duracao_ms: int,
    tentativas: int,
    intencao: Optional[str] = None,
    handoff_destino: Optional[str] = None,
    motivo: Optional[str] = None,
) -> None:
    """
    Registra evento estruturado de observabilidade de turno (US5, FR-015,
    FR-016; contracts/turno-event.md; data-model.md §Entity Registro de
    Turno).

    Emitido exatamente 1x por turno processado, inclusive em falha
    (acao="erro", via try/finally no chamador — FR-016).

    Seguranca (Decision 8 / CHK006 / SEC-LLM-1):
    - NUNCA recebe/inclui conteudo bruto da mensagem do lead — apenas
      metadados (intencao classificada, idioma, contadores, etapas).
    - `chamado_id` NAO e mascarado aqui (nao e numero de telefone); se um
      numero/telefone precisar aparecer em algum campo futuro, deve passar
      por `_mask_number` antes de chegar a esta funcao.
    - `_scrub` remove chaves sensiveis (tokens/keys) dentro de `_emit`,
      antes do evento ser impresso.
    - `handoff_destino`, quando presente, e o destino logico ja resolvido
      pela configuracao/allowlist (nunca decidido pelo LLM — SEC-LLM-3);
      esta funcao apenas repassa o valor recebido, nao o gera.

    `acao` deve pertencer a: resposta | nudge | handoff | retomada |
    sessao_nova | erro.
    """
    if acao not in _TURNO_ACOES:
        logger.warning("log_turno: acao desconhecida %r (aceitas: %s)", acao, sorted(_TURNO_ACOES))

    event: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "turno",
        "chamado_id": chamado_id,
        "turno_sessao": turno_sessao,
        "etapa_entrada": etapa_entrada,
        "etapa_saida": etapa_saida,
        "intencao": intencao,
        "idioma": idioma,
        "n_blocos_enviados": n_blocos_enviados,
        "acao": acao,
        "handoff_destino": handoff_destino,
        "duracao_ms": duracao_ms,
        "tentativas": tentativas,
        "motivo": motivo,
    }
    _emit(event)


# Alias de compatibilidade (mantido para nao quebrar chamadas anteriores)
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
    API generica de evento. Preferir as funcoes especificas acima.
    Mantida para retrocompatibilidade.
    """
    event: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tipo": tipo,
    }
    if ticket_id is not None:
        event["ticket_id"] = ticket_id
    if contact_number is not None:
        event["contact_number"] = _mask_number(contact_number)
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
        event["detalhe"] = _scrub(detalhe)
    _emit(event)


@contextmanager
def timed_llm_call(
    ticket_id: Optional[int] = None,
    stage: Optional[str] = None,
    model_used: Optional[str] = None,
) -> Generator[dict, None, None]:
    """
    Context manager que mede latencia de chamada LLM e emite o evento ao final.

    Uso:
        with timed_llm_call(ticket_id=42, stage="apresentacao", model_used="gpt-4o") as ctx:
            resp = await openai_client.chat(...)
            ctx["tokens_in"] = resp.usage.prompt_tokens
            ctx["tokens_out"] = resp.usage.completion_tokens
    """
    ctx: dict = {}
    t0 = time.monotonic()
    try:
        yield ctx
    finally:
        latency_ms = int((time.monotonic() - t0) * 1000)
        log_llm_call(
            ticket_id=ticket_id,
            stage=stage,
            model_used=model_used or ctx.get("model_used"),
            tokens_in=ctx.get("tokens_in"),
            tokens_out=ctx.get("tokens_out"),
            latency_ms=latency_ms,
        )
