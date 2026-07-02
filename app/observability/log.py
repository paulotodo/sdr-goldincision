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
import re
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

# ---------------------------------------------------------------------------
# Scrubber anti-PII de TEXTO LIVRE (FASE 4, task 4.2 — sdr-fidelidade-json).
#
# `_scrub` (acima) remove CHAVES de dict conhecidas (defesa contra
# secrets/tokens). Este bloco cobre um caso distinto: texto livre GERADO
# PELO MODELO (ex.: `VeredictoFidelidade.afirmacoes_nao_sustentadas` do
# Portao de Fidelidade, `app/core/fidelity.py`) pode, em tese, ecoar um
# trecho do contexto de conhecimento ou da mensagem do lead contendo um
# email/telefone/CPF. Antes de qualquer texto assim chegar a um sink de
# observabilidade (`log_turno`), ele passa por `scrub_texto_livre` —
# fail-safe: qualquer excecao de regex -> None (o chamador trata None como
# "nao disponivel para log" e cai no fallback de contagem apenas).
# ---------------------------------------------------------------------------

_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_RE_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_RE_TELEFONE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")


def scrub_texto_livre(texto: Optional[str]) -> Optional[str]:
    """
    Redige padroes comuns de PII (email, CPF, telefone) de um texto livre.

    Fail-safe: qualquer excecao durante a redacao retorna None — o chamador
    deve tratar None como "scrubbing indisponivel" e aplicar o fallback
    (nunca logar o texto bruto nesse caso).
    """
    if not texto:
        return texto
    try:
        out = _RE_EMAIL.sub("<email>", texto)
        out = _RE_CPF.sub("<cpf>", out)
        out = _RE_TELEFONE.sub("<telefone>", out)
        return out
    except Exception:
        return None


def scrub_afirmacoes_nao_sustentadas(
    afirmacoes: Optional[list[str]],
) -> tuple[Optional[list[str]], int]:
    """
    Aplica `scrub_texto_livre` a cada afirmacao nao sustentada do veredito de
    fidelidade (`app/core/fidelity.py:VeredictoFidelidade`).

    Retorna `(lista_redigida_ou_None, contagem)`:
    - Sucesso: `(lista_redigida, len(afirmacoes))`.
    - Falha (qualquer excecao) ou entrada vazia/None: `(None, contagem)` —
      fallback do chamador e logar SOMENTE a contagem, nunca texto bruto.
    """
    if not afirmacoes:
        return None, 0
    try:
        redigidas = [scrub_texto_livre(a) for a in afirmacoes]
        if any(r is None for r in redigidas):
            # Uma falha pontual de scrub em qualquer item -> fallback total
            # (nunca mistura texto bruto com texto redigido).
            return None, len(afirmacoes)
        return redigidas, len(afirmacoes)
    except Exception:
        return None, len(afirmacoes)


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
    confianca_slot: Optional[float] = None,
    fidelidade_fiel: Optional[bool] = None,
    fidelidade_afirmacoes_nao_sustentadas: Optional[list[str]] = None,
    fonte_ids: Optional[list[str]] = None,
    troca_caminho_origem: Optional[int] = None,
    troca_caminho_destino: Optional[int] = None,
    troca_metodo: Optional[str] = None,
    troca_confianca: Optional[float] = None,
    reformulacao_variante: Optional[int] = None,
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

    Campos ADITIVOS (FASE 4, sdr-fidelidade-json, task 4.3 — NUNCA alteram
    o contrato/schema da Onda 1: sao OPTIONAL, default None, e so entram no
    evento emitido quando o CHAMADO explicitamente os passa; uma chamada
    sem esses kwargs produz EXATAMENTE o mesmo payload de antes):
    - `confianca_slot`: confianca (0..1) do fallback agentico de
      slot-filling (`app/core/interpret.py:SlotExtractor`) quando acionado
      neste turno (Pilar 8). None quando o turno resolveu por fast-path
      deterministico ou nao envolveu slot-filling.
    - `fidelidade_fiel`: resultado (`fiel: bool`) do Portao de Fidelidade
      (`app/core/fidelity.py:FidelityGate`) quando acionado neste turno
      (Pilar 7). None quando o portao nao foi acionado (texto sem condicao
      comercial, ou verbatim — que nunca passa pelo portao).
    - `fidelidade_afirmacoes_nao_sustentadas`: texto livre GERADO PELO
      MODELO (nunca a mensagem do lead) — task 4.2: SEMPRE roteado por
      `scrub_texto_livre`/`scrub_afirmacoes_nao_sustentadas` (scrubber
      anti-PII) DENTRO desta funcao antes do `_emit`, nunca verbatim.
      Fallback fail-safe (scrubbing indisponivel/erro): o campo de texto e
      OMITIDO do evento e substituido por
      `fidelidade_n_afirmacoes_nao_sustentadas` (apenas a contagem, sempre
      seguro).
    - `fonte_ids` (Onda 3, FASE 5, US4/FR-018): lista de ids de `chunk`
      (`HybridRetriever`) que embasaram a resposta deste turno — ids
      determinados pelo orquestrador (`GroundedResponder.last_fonte_ids`),
      nunca reportados pelo LLM. None quando RAG nao foi acionado neste
      turno (fast-path/verbatim/sem duvida). Aditivo: nao contem texto
      livre, apenas ids — sem necessidade de scrubbing.
    - `troca_caminho_origem`/`troca_caminho_destino` (FASE 5, US4/FR-017,
      contracts/turno-event-extensao.md): caminho de origem/destino quando
      uma troca de caminho mid-jornada foi despachada neste turno
      (`FlowEngine._despachar_troca_caminho`). Sempre preenchidos JUNTOS
      (ambos presentes ou ambos None — E-1); None quando nenhuma troca
      ocorreu neste turno. Apenas metadados estruturados (indices de
      `CaminhoMapaMestre`) — nunca texto livre, sem necessidade de
      scrubbing (SEC-LLM-1).
    - `troca_metodo` (FASE 5, US4/FR-017): `"deterministico"` (lexico) ou
      `"assistido"` (fallback agentico via `SlotExtractor`, S-5). None
      quando nenhuma troca ocorreu neste turno.
    - `troca_confianca` (FASE 5, US4/FR-017): confianca (0..1) da
      classificacao assistida — SO nao-nula quando `troca_metodo=
      "assistido"` (E-2); `None` quando `troca_metodo="deterministico"`
      (o lexico nao produz confianca fracionaria) ou quando nenhuma troca
      ocorreu.
    - `reformulacao_variante` (FASE 5, US4/FR-018): indice ciclico
      (`(n-1) % len(pool)`) da variante de reformulacao humanizada
      enviada neste turno (`FlowEngine._reformular_ou_handoff`, dec-011/
      dec-012). None quando o turno nao envolveu reformulacao (resposta
      compreendida de primeira, troca de caminho detectada em vez de
      reformular, ou encaminhamento a humano por limite de tentativas).
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

    # --- Campos aditivos (task 4.3) — SO adicionados quando explicitamente
    # passados pelo chamador, preservando o contrato exato da Onda 1 quando
    # nao usados (ver docstring acima e tests/test_observability_fidelidade_slot.py).
    if confianca_slot is not None:
        event["confianca_slot"] = confianca_slot
    if fidelidade_fiel is not None:
        event["fidelidade_fiel"] = fidelidade_fiel
    if fidelidade_afirmacoes_nao_sustentadas is not None:
        # task 4.2: scrubber anti-PII ANTES de qualquer inclusao no log;
        # fallback = so a contagem quando o scrubbing falha/indisponivel.
        redigidas, contagem = scrub_afirmacoes_nao_sustentadas(
            fidelidade_afirmacoes_nao_sustentadas
        )
        if redigidas is not None:
            event["fidelidade_afirmacoes_nao_sustentadas"] = redigidas
        else:
            event["fidelidade_n_afirmacoes_nao_sustentadas"] = contagem
    if fonte_ids is not None:
        # Onda 3 (FASE 5, US4/FR-018): apenas ids (sem texto livre), aditivo,
        # junto do veredito de fidelidade (mesmo caminho aditivo acima).
        event["fonte_ids"] = fonte_ids
    # --- Campos aditivos (FASE 5, US4 — sdr-fluidez-intencao, FR-017/FR-018):
    # troca de caminho mid-jornada e reformulacao humanizada. Mesmo padrao
    # aditivo acima — SO adicionados quando explicitamente passados,
    # preservando o payload exato das Ondas 1/2/3 quando nao usados (ver
    # docstring e tests/test_observability.py).
    if troca_caminho_origem is not None:
        event["troca_caminho_origem"] = troca_caminho_origem
    if troca_caminho_destino is not None:
        event["troca_caminho_destino"] = troca_caminho_destino
    if troca_metodo is not None:
        event["troca_metodo"] = troca_metodo
    if troca_confianca is not None:
        event["troca_confianca"] = troca_confianca
    if reformulacao_variante is not None:
        event["reformulacao_variante"] = reformulacao_variante

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
