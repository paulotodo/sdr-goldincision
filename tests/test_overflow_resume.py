"""
Regressao do fix de OVERFLOW + rigidez (follow-up pos-Onda 3).

Bug relatado: o convite de overflow ("Posso continuar explicando o restante ou
especialista?") era gerado no ENVIO e o restante DESCARTADO; a resposta do lead
("pode continuar") caia como texto livre no RAG -> abstencao -> handoff
("Nao tenho essa informacao confirmada..."). Promessa quebrada + roteamento
errado (origem do "engessado").

Cobre:
  - send_message_blocks retorna os blocos NAO entregues (SendResult) em vez de
    descarta-los;
  - "pode continuar" retoma o restante (verbatim), NAO chama o RAG;
  - "especialista" -> handoff ao destino da config (allowlist, SEC-LLM-3);
  - mensagem/pergunta nova durante overflow -> limpa o buffer e segue normal;
  - glue conversacional puro (saudacao/agradecimento/afirmacao) num no de
    DUVIDAS NAO vai ao RAG (nao cai em abstencao);
  - DUVIDA FACTUAL real AINDA vai ao RAG (anti-alucinacao preservada).

FlowEngine REAL; mock SOMENTE do client OpenAI/ChatMaster e da leitura da Base.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.flow import (
    ETAPA_DUVIDAS,
    CaminhoMapaMestre,
    _classificar_overflow_fastpath,
    _glue_pura,
)
from app.core.intent import ClassificacaoIntencao
from app.integrations.chatmaster import SendResult, overflow_notice
from tests.test_chatmaster import _make_client, _mock_response
from tests.test_flow import MockResponder, engine, make_context

# ---------------------------------------------------------------------------
# chatmaster: send_message_blocks retorna o restante (nao descarta)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_message_blocks_retorna_restantes_no_overflow():
    client = _make_client(max_msgs_per_turn=4)
    calls: list[str] = []

    async def fake_post(url, **kwargs):
        calls.append(kwargs["json"]["text"])
        return _mock_response(200)

    mock_http = AsyncMock()
    mock_http.post = fake_post
    client._client = mock_http

    texto = "\n\n".join(f"Paragrafo numero {i} com algum conteudo." for i in range(8))
    result = await client.send_message_blocks("5511967296849", texto)

    assert isinstance(result, SendResult)
    assert result.n_enviados == 4
    assert len(calls) == 4
    assert calls[-1] == overflow_notice("pt")
    # O restante (blocos reais nao entregues) e retornado para bufferizacao.
    assert len(result.blocos_restantes) >= 1
    assert overflow_notice("pt") not in result.blocos_restantes


@pytest.mark.asyncio
async def test_send_message_blocks_sem_overflow_restantes_vazio():
    client = _make_client(max_msgs_per_turn=4)
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=_mock_response(200))
    client._client = mock_http

    result = await client.send_message_blocks("5511967296849", "Texto curto")
    assert isinstance(result, SendResult)
    assert result.n_enviados == 1
    assert result.blocos_restantes == []


# ---------------------------------------------------------------------------
# fast-path de classificacao do convite de overflow
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", [
    "pode continuar", "continua", "sim", "pode", "quero", "manda", "isso",
    "ok", "claro", "continue", "keep going", "sigue", "pode continuar sim",
])
def test_overflow_fastpath_continuar(msg):
    assert _classificar_overflow_fastpath(msg) == "continuar"


@pytest.mark.parametrize("msg", [
    "especialista", "quero um especialista", "prefiro falar com um especialista",
    "falar com atendente", "quero um humano", "melhor um consultor",
])
def test_overflow_fastpath_especialista(msg):
    assert _classificar_overflow_fastpath(msg) == "especialista"


@pytest.mark.parametrize("msg", [
    "qual o valor do curso", "quando comeca a proxima turma", "onde e",
])
def test_overflow_fastpath_indeterminado(msg):
    assert _classificar_overflow_fastpath(msg) is None


# ---------------------------------------------------------------------------
# glue conversacional puro (nao vai ao RAG) vs duvida factual (vai)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", [
    "obrigado", "muito obrigado", "ok", "beleza", "bom dia", "sim", "entendi",
    "ok obrigado", "valeu",
])
def test_glue_pura_true(msg):
    assert _glue_pura(msg) is True


@pytest.mark.parametrize("msg", [
    "quanto custa?", "qual o preco", "onde e o curso", "tem desconto",
    "me explica melhor sobre o modulo 1", "",
])
def test_glue_pura_false(msg):
    assert _glue_pura(msg) is False


# ---------------------------------------------------------------------------
# FlowEngine.process — retomada de overflow
# ---------------------------------------------------------------------------

def _ctx_overflow():
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSO_ONLINE_HG, etapa=ETAPA_DUVIDAS,
    )
    ctx.overflow_blocos = ["Parte 2 do conteudo.", "Parte 3 do conteudo."]
    ctx.overflow_idioma = "pt"
    return ctx


@pytest.mark.asyncio
async def test_process_overflow_continuar_devolve_restante_sem_rag():
    resp = MockResponder()
    eng = engine(intencao=ClassificacaoIntencao.CURSO_ONLINE, responder=resp)
    ctx = _ctx_overflow()

    result = await eng.process(ctx.ticket_id, "pode continuar", ctx)

    assert result.action == "continue"
    assert result.response_text == "Parte 2 do conteudo.\n\nParte 3 do conteudo."
    # NUNCA passou pelo RAG/LLM (era a causa da abstencao/handoff).
    assert resp.generate_calls == []
    assert ctx.overflow_blocos == []


@pytest.mark.asyncio
async def test_process_overflow_especialista_faz_handoff_da_config():
    resp = MockResponder()
    eng = engine(intencao=ClassificacaoIntencao.CURSO_ONLINE, responder=resp)
    ctx = _ctx_overflow()

    result = await eng.process(ctx.ticket_id, "prefiro um especialista", ctx)

    assert result.action == "handoff"
    assert result.handoff_destino  # destino LOGICO da config (allowlist/SEC-LLM-3)
    assert resp.generate_calls == []
    assert ctx.overflow_blocos == []


@pytest.mark.asyncio
async def test_process_overflow_mensagem_nova_limpa_buffer_e_segue_normal():
    resp = MockResponder(response_text="RESP_RAG")
    eng = engine(intencao=ClassificacaoIntencao.CURSO_ONLINE, responder=resp)
    ctx = _ctx_overflow()

    # Pergunta factual real durante o overflow: abandona o buffer e processa
    # normalmente (a pergunta ainda precisa funcionar).
    result = await eng.process(ctx.ticket_id, "qual o valor do curso", ctx)

    assert ctx.overflow_blocos == []
    # roteou para a duvida (RAG/LLM), nao para a retomada de overflow.
    assert result.response_text != "Parte 2 do conteudo.\n\nParte 3 do conteudo."
    assert resp.generate_calls, "duvida factual real deve ir ao responder/RAG"


# ---------------------------------------------------------------------------
# glue no no de DUVIDAS: nao vai ao RAG; duvida factual vai
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duvida_online_glue_nao_chama_rag():
    resp = MockResponder()
    eng = engine(intencao=ClassificacaoIntencao.CURSO_ONLINE, responder=resp)
    ctx = make_context(caminho=CaminhoMapaMestre.CURSO_ONLINE_HG, etapa=ETAPA_DUVIDAS)

    result = await eng._responder_duvida_online(ctx, "muito obrigado!", {})

    assert result.action == "continue"
    assert "😊" in result.response_text
    assert resp.generate_calls == [], "glue puro NAO deve ir ao RAG/abstencao"


@pytest.mark.asyncio
async def test_duvida_online_pergunta_factual_ainda_vai_ao_rag():
    resp = MockResponder(response_text="RESP_RAG")
    eng = engine(intencao=ClassificacaoIntencao.CURSO_ONLINE, responder=resp)
    ctx = make_context(caminho=CaminhoMapaMestre.CURSO_ONLINE_HG, etapa=ETAPA_DUVIDAS)

    result = await eng._responder_duvida_online(ctx, "o curso tem certificado", {})

    # anti-alucinacao intacta: duvida factual real segue o caminho do RAG.
    assert resp.generate_calls, "duvida factual deve ir ao responder/RAG"
    assert result.response_text == "RESP_RAG"
