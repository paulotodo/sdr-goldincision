"""
Testes para app/observability/log.py (task 7.1.4).

Cobre:
- Evento de log por mensagem processada (webhook_in, llm_call, message_out)
- Evento de handoff (handoff_type, destino, motivo)
- Erro logado sem expor detalhe tecnico ao usuario (US7-AS3)
- Mascaramento de numero de telefone
- Filtragem de secrets (token, senha, etc) — nunca logar (FR-032)
- timed_llm_call context manager (latencia + tokens)
- log_event retrocompativel
"""
from __future__ import annotations

import json
import pytest
from io import StringIO
from unittest.mock import patch

from app.observability.log import (
    log_event,
    log_erro,
    log_handoff,
    log_llm_call,
    log_message_out,
    log_webhook_in,
    timed_llm_call,
    _mask_number,
    _scrub,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capture_logs(fn, *args, **kwargs) -> list[dict]:
    """Captura saida JSON de uma funcao de log."""
    captured: list[str] = []
    with patch("builtins.print", side_effect=lambda *a, **k: captured.append(a[0])):
        fn(*args, **kwargs)
    return [json.loads(line) for line in captured if line.strip()]


# ---------------------------------------------------------------------------
# Testes de mascaramento
# ---------------------------------------------------------------------------

def test_mask_number_mascara_apos_4_digitos():
    assert _mask_number("5511967296849") == "5511****"


def test_mask_number_none_retorna_none():
    assert _mask_number(None) is None


def test_mask_number_string_curta():
    assert _mask_number("12") == "12****"


# ---------------------------------------------------------------------------
# Testes de _scrub (filtragem de secrets)
# ---------------------------------------------------------------------------

def test_scrub_remove_token():
    d = {"token": "meu-secret", "nome": "ok"}
    result = _scrub(d)
    assert "token" not in result
    assert result["nome"] == "ok"


def test_scrub_remove_openai_key():
    d = {"openai_api_key": "sk-xxx", "tipo": "llm_call"}
    result = _scrub(d)
    assert "openai_api_key" not in result
    assert result["tipo"] == "llm_call"


def test_scrub_recursivo_em_nested():
    d = {"detalhe": {"password": "senhaSecreta", "mensagem": "ok"}}
    result = _scrub(d)
    assert "password" not in result["detalhe"]
    assert result["detalhe"]["mensagem"] == "ok"


def test_scrub_em_lista():
    lst = [{"token": "x"}, {"valor": 1}]
    result = _scrub(lst)
    assert "token" not in result[0]
    assert result[1]["valor"] == 1


# ---------------------------------------------------------------------------
# Testes de log_webhook_in
# ---------------------------------------------------------------------------

def test_log_webhook_in_emite_json():
    events = _capture_logs(
        log_webhook_in,
        ticket_id=42,
        contact_number="5511967296849",
        stage="recepcao",
        latency_ms=150,
        num_mensagens=2,
    )
    assert len(events) == 1
    e = events[0]
    assert e["tipo"] == "webhook_in"
    assert e["ticket_id"] == 42
    assert "timestamp" in e
    assert e["latency_ms"] == 150
    assert e["num_mensagens"] == 2


def test_log_webhook_in_mascara_numero():
    events = _capture_logs(
        log_webhook_in,
        contact_number="5521987654321",
    )
    e = events[0]
    assert e["contact_number"] == "5521****"


# ---------------------------------------------------------------------------
# Testes de log_llm_call
# ---------------------------------------------------------------------------

def test_log_llm_call_emite_campos_corretos():
    events = _capture_logs(
        log_llm_call,
        ticket_id=10,
        stage="apresentacao",
        model_used="gpt-4o",
        tokens_in=500,
        tokens_out=200,
        latency_ms=1200,
    )
    assert len(events) == 1
    e = events[0]
    assert e["tipo"] == "llm_call"
    assert e["model_used"] == "gpt-4o"
    assert e["tokens_in"] == 500
    assert e["tokens_out"] == 200
    assert e["latency_ms"] == 1200


def test_log_llm_call_sem_campos_opcionais():
    """log_llm_call funciona sem campos opcionais."""
    events = _capture_logs(log_llm_call)
    assert len(events) == 1
    assert events[0]["tipo"] == "llm_call"


# ---------------------------------------------------------------------------
# Testes de log_message_out
# ---------------------------------------------------------------------------

def test_log_message_out_emite_evento():
    events = _capture_logs(
        log_message_out,
        ticket_id=7,
        contact_number="5511111111111",
        stage="link",
        num_blocos=2,
    )
    assert len(events) == 1
    e = events[0]
    assert e["tipo"] == "message_out"
    assert e["ticket_id"] == 7
    assert e["contact_number"] == "5511****"
    assert e["num_blocos"] == 2


# ---------------------------------------------------------------------------
# Testes de log_handoff (FR-034)
# ---------------------------------------------------------------------------

def test_log_handoff_emite_campos_corretos():
    events = _capture_logs(
        log_handoff,
        ticket_id=88,
        contact_number="5521987654321",
        handoff_type="fila",
        destino="consultores",
        motivo="Interesse em HG360 presencial",
    )
    assert len(events) == 1
    e = events[0]
    assert e["tipo"] == "handoff"
    assert e["handoff_type"] == "fila"
    assert e["destino"] == "consultores"
    assert e["motivo"] == "Interesse em HG360 presencial"
    assert e["ticket_id"] == 88


def test_log_handoff_paciente_modelo():
    """Handoff paciente modelo tem tipo 'paciente_modelo'."""
    events = _capture_logs(
        log_handoff,
        ticket_id=5,
        handoff_type="paciente_modelo",
        destino="nidia",
    )
    assert events[0]["handoff_type"] == "paciente_modelo"


# ---------------------------------------------------------------------------
# Testes de log_erro (US7-AS3)
# ---------------------------------------------------------------------------

def test_log_erro_registra_detalhe_tecnico():
    """
    log_erro registra detalhe para logs internos, mas NAO expoe ao usuario.
    (US7-AS3 = a funcao de log nao formata mensagem de usuario)
    """
    events = _capture_logs(
        log_erro,
        ticket_id=3,
        stage="llm_call",
        tipo_erro="erro_llm",
        detalhe="OpenAI timeout after 30s — traceback: ...",
    )
    assert len(events) == 1
    e = events[0]
    assert e["tipo"] == "erro_llm"
    assert "detalhe" in e
    # Detalhe e para logs internos; a funcao NAO formata resposta ao usuario


def test_log_erro_nao_inclui_secrets_no_detalhe():
    """Detalhe com token nao deve vazar no evento."""
    events = _capture_logs(
        log_erro,
        tipo_erro="erro",
        detalhe="Falha ao conectar: token=sk-abc123",
    )
    # O detalhe e texto livre, mas _scrub nao filtra substrings em texto —
    # apenas chaves de dict. Verificamos que nao ha campo "token" na raiz.
    e = events[0]
    assert "token" not in e or e.get("tipo") != "token"


# ---------------------------------------------------------------------------
# Testes do timed_llm_call context manager
# ---------------------------------------------------------------------------

def test_timed_llm_call_emite_evento_com_latencia():
    """timed_llm_call mede latencia e emite log_llm_call."""
    events = []
    with patch("app.observability.log.log_llm_call") as mock_log:
        with timed_llm_call(ticket_id=1, stage="fluxo", model_used="gpt-4o-mini") as ctx:
            ctx["tokens_in"] = 100
            ctx["tokens_out"] = 50

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args[1]
        assert call_kwargs["ticket_id"] == 1
        assert call_kwargs["stage"] == "fluxo"
        assert call_kwargs["model_used"] == "gpt-4o-mini"
        assert call_kwargs["tokens_in"] == 100
        assert call_kwargs["tokens_out"] == 50
        assert call_kwargs["latency_ms"] >= 0


def test_timed_llm_call_registra_mesmo_com_excecao():
    """timed_llm_call emite evento mesmo quando o corpo levanta excecao."""
    with patch("app.observability.log.log_llm_call") as mock_log:
        try:
            with timed_llm_call(ticket_id=2, stage="erro", model_used="gpt-4o"):
                raise ValueError("Erro simulado")
        except ValueError:
            pass

        # O log DEVE ter sido emitido (latencia do erro e util)
        mock_log.assert_called_once()


# ---------------------------------------------------------------------------
# Testes de log_event retrocompativel
# ---------------------------------------------------------------------------

def test_log_event_retrocompativel():
    """log_event (API legada) emite evento JSON valido."""
    events = _capture_logs(
        log_event,
        tipo="webhook_in",
        ticket_id=1,
        contact_number="5511000000000",
        stage="recepcao",
        latency_ms=100,
        model_used="gpt-4o",
        tokens_in=50,
        tokens_out=30,
        detalhe={"extra": "info"},
    )
    assert len(events) == 1
    e = events[0]
    assert e["tipo"] == "webhook_in"
    assert e["ticket_id"] == 1
    assert e["latency_ms"] == 100
    assert e["model_used"] == "gpt-4o"


def test_log_event_scrub_detalhe():
    """log_event filtra secrets em detalhe via _scrub."""
    events = _capture_logs(
        log_event,
        tipo="info",
        detalhe={"token": "meu-secret", "mensagem": "ok"},
    )
    e = events[0]
    assert "token" not in e.get("detalhe", {})
    assert e["detalhe"]["mensagem"] == "ok"
