"""
Testes DEDICADOS de anti-PII para o evento de turno (task 1.3 — fecha o gap
de checklist CHK006: a restricao anti-PII estava documentada apenas em
research.md Decision 8, sem teste dedicado).

Cobre, especificamente para `log_turno` / evento `"turno"`
(contracts/turno-event.md; data-model.md §Entity Registro de Turno;
Spec FR-020, SEC-LLM-1):

1.3.1 - `log_turno` nunca inclui conteudo bruto da mensagem do lead.
1.3.2 - o payload do evento de turno nunca carrega numero/telefone cru
        (o contrato nao tem campo de telefone; `_mask_number` continua
        correto caso um campo desse tipo seja adicionado no futuro).
1.3.3 - `_scrub` remove chaves sensiveis (tokens/keys) de um evento
        formato "turno" antes de `_emit` imprimir.
"""
from __future__ import annotations

import inspect
import json
from unittest.mock import patch

from app.observability.log import _emit, _mask_number, log_turno


def _capture_log_turno(**kwargs) -> dict:
    """Invoca log_turno e retorna o unico evento JSON emitido."""
    captured: list[str] = []
    with patch("builtins.print", side_effect=lambda *a, **k: captured.append(a[0])):
        log_turno(**kwargs)
    assert len(captured) == 1
    return json.loads(captured[0])


_BASE_TURNO_KWARGS = dict(
    chamado_id=138901,
    turno_sessao=3,
    etapa_entrada="QUALIFICACAO_MEDICO",
    etapa_saida="APRESENTACAO_CURSO",
    idioma="pt",
    n_blocos_enviados=2,
    acao="resposta",
    duracao_ms=4210,
    tentativas=0,
    intencao="interesse_curso",
)


# ---------------------------------------------------------------------------
# 1.3.1 — nunca inclui conteudo bruto da mensagem do lead
# ---------------------------------------------------------------------------

def test_log_turno_assinatura_nao_tem_parametro_de_mensagem_bruta():
    """
    Defesa estrutural: a assinatura de log_turno NAO aceita nenhum parametro
    que carregue o texto/conteudo bruto da mensagem do lead — a mensagem do
    lead e dado nao-confiavel (SEC-LLM-1) e nunca deve chegar a um sink de
    observabilidade em texto livre.
    """
    params = set(inspect.signature(log_turno).parameters)
    proibidos_de_conteudo_bruto = {
        "mensagem", "texto", "conteudo", "user_message", "texto_lead",
        "body", "raw_message", "mensagem_bruta",
    }
    assert not (params & proibidos_de_conteudo_bruto)


def test_log_turno_evento_so_tem_os_campos_do_contrato():
    """
    O evento emitido tem EXATAMENTE os campos do contrato
    (contracts/turno-event.md) — nenhum campo extra (ex.: uma mensagem bruta
    injetada por engano) sobrevive ate o `_emit`.
    """
    e = _capture_log_turno(**_BASE_TURNO_KWARGS)
    campos_esperados = {
        "timestamp", "event", "chamado_id", "turno_sessao", "etapa_entrada",
        "etapa_saida", "intencao", "idioma", "n_blocos_enviados", "acao",
        "handoff_destino", "duracao_ms", "tentativas", "motivo",
    }
    assert set(e.keys()) == campos_esperados
    assert e["event"] == "turno"


# ---------------------------------------------------------------------------
# 1.3.2 — numero/telefone nunca aparece cru no evento de turno
# ---------------------------------------------------------------------------

def test_log_turno_nao_expoe_campo_de_numero_de_telefone():
    """
    O contrato de `log_turno` nao tem (e nao deve ganhar sem passar por
    `_mask_number`) nenhum campo de numero/telefone do lead — `chamado_id`
    e um identificador interno do ticket, nunca o numero de WhatsApp.
    """
    e = _capture_log_turno(**_BASE_TURNO_KWARGS)
    campos_proibidos = {"numero", "telefone", "contact_number", "phone", "sender"}
    assert not (set(e.keys()) & campos_proibidos)


def test_mask_number_permanece_correto_para_uso_futuro_em_eventos_de_turno():
    """
    Se um campo de numero/telefone for adicionado ao evento de turno no
    futuro, `_mask_number` (a mesma funcao ja usada por log_webhook_in/
    log_message_out/log_handoff) deve mascarar corretamente — trava de
    regressao para essa dependencia compartilhada, testada aqui no contexto
    de observabilidade de turno.
    """
    assert _mask_number("5511967296849") == "5511****"
    assert _mask_number(None) is None


# ---------------------------------------------------------------------------
# 1.3.3 — _scrub remove chaves sensiveis de um evento "turno" antes de _emit
# ---------------------------------------------------------------------------

def test_scrub_remove_chaves_sensiveis_de_evento_turno_antes_de_emit():
    """
    Mesmo que um campo sensivel (token/secret) seja acidentalmente incluido
    num payload em formato de evento de turno, `_emit` (o sink usado por
    `log_turno`) aplica `_scrub` e remove a chave antes de imprimir.
    """
    evento_turno_com_vazamento_acidental = {
        "event": "turno",
        "chamado_id": 138901,
        "acao": "handoff",
        "chatmaster_token": "NUNCA-DEVE-VAZAR",
        "openai_api_key": "sk-NUNCA-DEVE-VAZAR",
        "authorization": "Bearer NUNCA-DEVE-VAZAR",
    }
    captured: list[str] = []
    with patch("builtins.print", side_effect=lambda *a, **k: captured.append(a[0])):
        _emit(evento_turno_com_vazamento_acidental)

    assert len(captured) == 1
    result = json.loads(captured[0])
    assert "chatmaster_token" not in result
    assert "openai_api_key" not in result
    assert "authorization" not in result
    # Campos legitimos do evento de turno sobrevivem ao scrub.
    assert result["event"] == "turno"
    assert result["chamado_id"] == 138901
    assert result["acao"] == "handoff"


def test_log_turno_integra_scrub_via_emit():
    """
    `log_turno` de fato passa pelo caminho `_emit` -> `_scrub` (nao emite
    via print/json direto, contornando o filtro).
    """
    with patch("app.observability.log._emit") as mock_emit:
        log_turno(**_BASE_TURNO_KWARGS)
    mock_emit.assert_called_once()
    (evento_passado,), _ = mock_emit.call_args
    assert evento_passado["event"] == "turno"
