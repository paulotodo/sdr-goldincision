"""
Testes para app/core/responder.py (contrato JSON estruturado + concisao).

Cobre:
- generate() chama chat_reasoning_json com o teto de tokens configurado.
- O default (REASONING_MAX_TOKENS) e usado quando nao informado.
- O prompt de sistema reforca objetividade/resumo.
- Payload valido -> (texto, precisa_handoff) extraidos do pacote (FR-001/FR-006).
- Payload malformado + retry -> handoff=True na 2a falha (FR-002/FR-003).
- Temperatura baixa (0-0.2) em contexto factual; padrao (0.3) fora dele (FR-004).
- Divergencia de idioma do pacote conta como pacote invalido (FR-005).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.contracts import RespostaEstruturada
from app.core.responder import (
    _SYSTEM_BASE,
    REASONING_MAX_TOKENS,
    GroundedResponder,
)


def _pacote_json(
    texto: str = "Resposta curta.",
    fontes: list[str] | None = None,
    precisa_handoff: bool = False,
    confianca: float = 0.9,
    idioma: str = "pt",
) -> str:
    """Serializa um RespostaEstruturada valido, como o LLM devolveria."""
    return RespostaEstruturada(
        texto=texto,
        fontes=fontes or [],
        precisa_handoff=precisa_handoff,
        confianca=confianca,
        idioma=idioma,
    ).model_dump_json()


def _make_openai_mock(raw_json: str | None = None) -> AsyncMock:
    client = AsyncMock()
    client.chat_reasoning_json = AsyncMock(return_value=raw_json or _pacote_json())
    return client


@pytest.mark.asyncio
async def test_generate_usa_max_tokens_configurado():
    """generate() repassa o max_tokens configurado ao chat_reasoning_json."""
    client = _make_openai_mock()
    responder = GroundedResponder(openai_client=client, max_tokens=280)

    texto, handoff = await responder.generate(
        user_message="qual a duracao?",
        caminho="hg360-sp",
        etapa="duvidas",
        knowledge_context="Curso de 3 dias.",
        idioma="pt",
    )

    assert texto == "Resposta curta."
    assert handoff is False
    client.chat_reasoning_json.assert_awaited_once()
    kwargs = client.chat_reasoning_json.call_args.kwargs
    assert kwargs["max_tokens"] == 280


@pytest.mark.asyncio
async def test_generate_default_max_tokens_e_conciso():
    """O default e o teto conciso do modulo (REASONING_MAX_TOKENS), bem abaixo de 600."""
    client = _make_openai_mock()
    responder = GroundedResponder(openai_client=client)

    await responder.generate(
        user_message="oi",
        caminho="licenciamento-internacional",
        etapa="duvidas",
        knowledge_context="Base.",
        idioma="pt",
    )

    kwargs = client.chat_reasoning_json.call_args.kwargs
    assert kwargs["max_tokens"] == REASONING_MAX_TOKENS
    assert REASONING_MAX_TOKENS <= 320, "teto deve ser conciso (era 600 antes)"


@pytest.mark.asyncio
async def test_generate_injeta_perfil_conhecido_no_system():
    """known_facts e injetado no system prompt para o LLM nao re-perguntar."""
    client = _make_openai_mock()
    responder = GroundedResponder(openai_client=client)
    facts = (
        "=== FATOS JA CONHECIDOS DO LEAD ===\n"
        "- Ja confirmou que e medico — NAO pergunte de novo se e medico."
    )

    await responder.generate(
        user_message="quais os valores?",
        caminho="licenciamento-internacional",
        etapa="duvidas",
        knowledge_context="Base.",
        idioma="pt",
        known_facts=facts,
    )

    messages = client.chat_reasoning_json.call_args.args[0]
    system = messages[0]["content"]
    assert facts in system


@pytest.mark.asyncio
async def test_generate_sem_perfil_nao_injeta_bloco():
    """Sem known_facts, o system prompt nao ganha o bloco de fatos conhecidos."""
    client = _make_openai_mock()
    responder = GroundedResponder(openai_client=client)

    await responder.generate(
        user_message="oi",
        caminho="hg360-sp",
        etapa="duvidas",
        knowledge_context="Base.",
        idioma="pt",
    )

    system = client.chat_reasoning_json.call_args.args[0][0]["content"]
    assert "FATOS JA CONHECIDOS DO LEAD" not in system


def test_system_prompt_reforca_concisao():
    """O prompt base instrui respostas objetivas e resumidas, sem despejar tudo."""
    base_lower = _SYSTEM_BASE.lower()
    assert "objetivo" in base_lower and "resumid" in base_lower
    assert "apresentação inteira" in base_lower or "apresentacao inteira" in base_lower


# ---------------------------------------------------------------------------
# Contrato JSON estruturado (FASE 1 — FR-001..FR-007)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_payload_valido_extrai_texto_e_handoff():
    """Pacote valido com precisa_handoff=True e propagado na 2-tupla (FR-001/FR-006)."""
    client = _make_openai_mock(
        _pacote_json(texto="Vou conectar você com nossa equipe.", precisa_handoff=True)
    )
    responder = GroundedResponder(openai_client=client)

    texto, handoff = await responder.generate(
        user_message="quero um desconto especial",
        caminho="hg360-sp",
        etapa="duvidas",
        knowledge_context="Base.",
        idioma="pt",
    )

    assert texto == "Vou conectar você com nossa equipe."
    assert handoff is True
    # FlowEngine so recebe a 2-tupla — nunca o objeto RespostaEstruturada (FR-006).


@pytest.mark.asyncio
async def test_generate_payload_malformado_faz_1_retry_e_depois_handoff():
    """JSON malformado nas 2 tentativas -> exatamente 1 retry, depois handoff=True."""
    client = AsyncMock()
    client.chat_reasoning_json = AsyncMock(side_effect=["not-json", "{ainda invalido"])
    responder = GroundedResponder(openai_client=client)

    texto, handoff = await responder.generate(
        user_message="oi",
        caminho="hg360-sp",
        etapa="duvidas",
        knowledge_context="Base.",
        idioma="pt",
    )

    assert handoff is True
    assert client.chat_reasoning_json.await_count == 2  # 1 tentativa + 1 retry


@pytest.mark.asyncio
async def test_generate_retry_recupera_apos_1a_falha():
    """1a tentativa malformada, 2a valida -> sucesso sem handoff forcado."""
    client = AsyncMock()
    client.chat_reasoning_json = AsyncMock(
        side_effect=["not-json", _pacote_json(texto="Recuperado.", precisa_handoff=False)]
    )
    responder = GroundedResponder(openai_client=client)

    texto, handoff = await responder.generate(
        user_message="oi",
        caminho="hg360-sp",
        etapa="duvidas",
        knowledge_context="Base.",
        idioma="pt",
    )

    assert texto == "Recuperado."
    assert handoff is False
    assert client.chat_reasoning_json.await_count == 2


@pytest.mark.asyncio
async def test_generate_campo_extra_no_json_e_tratado_como_malformado():
    """Campo extra (extra="forbid") invalida o pacote mesmo com JSON bem-formado."""
    payload_com_extra = (
        '{"texto": "Resposta.", "fontes": [], "precisa_handoff": false, '
        '"confianca": 0.9, "idioma": "pt", "destino_handoff": "consultores"}'
    )
    client = AsyncMock()
    client.chat_reasoning_json = AsyncMock(
        side_effect=[payload_com_extra, payload_com_extra]
    )
    responder = GroundedResponder(openai_client=client)

    _texto, handoff = await responder.generate(
        user_message="oi",
        caminho="hg360-sp",
        etapa="duvidas",
        knowledge_context="Base.",
        idioma="pt",
    )

    assert handoff is True
    assert client.chat_reasoning_json.await_count == 2


@pytest.mark.asyncio
async def test_generate_idioma_divergente_conta_como_invalido():
    """Pacote com idioma diferente do esperado e tratado como invalido (FR-005)."""
    client = AsyncMock()
    client.chat_reasoning_json = AsyncMock(
        side_effect=[
            _pacote_json(idioma="en"),  # conversa esta em pt -> diverge
            _pacote_json(idioma="en"),
        ]
    )
    responder = GroundedResponder(openai_client=client)

    _texto, handoff = await responder.generate(
        user_message="oi",
        caminho="hg360-sp",
        etapa="duvidas",
        knowledge_context="Base.",
        idioma="pt",
    )

    assert handoff is True
    assert client.chat_reasoning_json.await_count == 2


@pytest.mark.asyncio
async def test_generate_idioma_convergente_na_2a_tentativa_recupera():
    """1a tentativa com idioma divergente, 2a com idioma correto -> sucesso."""
    client = AsyncMock()
    client.chat_reasoning_json = AsyncMock(
        side_effect=[
            _pacote_json(idioma="en", texto="Wrong language."),
            _pacote_json(idioma="pt", texto="Idioma correto."),
        ]
    )
    responder = GroundedResponder(openai_client=client)

    texto, handoff = await responder.generate(
        user_message="oi",
        caminho="hg360-sp",
        etapa="duvidas",
        knowledge_context="Base.",
        idioma="pt",
    )

    assert texto == "Idioma correto."
    assert handoff is False


@pytest.mark.asyncio
async def test_generate_usa_temperatura_baixa_em_contexto_factual():
    """Etapa/mensagem sobre preco/condicao comercial usa temperatura 0-0.2 (FR-004)."""
    client = _make_openai_mock()
    responder = GroundedResponder(openai_client=client)

    await responder.generate(
        user_message="qual o preço e as condições de parcelamento?",
        caminho="hg360-sp",
        etapa="duvidas",
        knowledge_context="Base.",
        idioma="pt",
    )

    kwargs = client.chat_reasoning_json.call_args.kwargs
    assert 0.0 <= kwargs["temperature"] <= 0.2


@pytest.mark.asyncio
async def test_generate_usa_temperatura_padrao_fora_de_contexto_factual():
    """Mensagem sem gatilho factual mantem a temperatura conversacional padrao (0.3)."""
    client = _make_openai_mock()
    responder = GroundedResponder(openai_client=client)

    await responder.generate(
        user_message="oi, bom dia!",
        caminho="hg360-sp",
        etapa="duvidas",
        knowledge_context="Base.",
        idioma="pt",
    )

    kwargs = client.chat_reasoning_json.call_args.kwargs
    assert kwargs["temperature"] == 0.3


@pytest.mark.asyncio
async def test_generate_erro_de_api_e_tratado_como_tentativa_malformada():
    """Excecao do client (ex: erro de API) na 1a tentativa nao propaga — retry
    ocorre normalmente e pode recuperar na 2a tentativa."""
    client = AsyncMock()
    client.chat_reasoning_json = AsyncMock(
        side_effect=[Exception("rate limit"), _pacote_json(texto="Recuperado.")]
    )
    responder = GroundedResponder(openai_client=client)

    texto, handoff = await responder.generate(
        user_message="oi",
        caminho="hg360-sp",
        etapa="duvidas",
        knowledge_context="Base.",
        idioma="pt",
    )

    assert texto == "Recuperado."
    assert handoff is False
