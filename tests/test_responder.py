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
- Portao de Fidelidade (Pilar 7, FASE 2): condicao comercial aciona o portao;
  duvida neutra nao aciona; reprovacao -> bloco canonico + handoff (FR-008..FR-012).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.contracts import RespostaEstruturada
from app.core.fidelity import FidelityGate, VeredictoFidelidade
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


# ---------------------------------------------------------------------------
# Portao de Fidelidade (FASE 2 — FR-008..FR-012, task 2.3)
# ---------------------------------------------------------------------------


def _make_fidelity_gate(fiel: bool, afirmacoes: list[str] | None = None) -> FidelityGate:
    """FidelityGate real cujo openai_client interno e mockado, para exercitar
    a integracao completa (gatilho -> verificar -> veredito) sem duplicar a
    logica do portao no teste do responder."""
    veredito_json = VeredictoFidelidade(
        fiel=fiel, afirmacoes_nao_sustentadas=afirmacoes or []
    ).model_dump_json()
    client = AsyncMock()
    client.chat_cheap_json = AsyncMock(return_value=veredito_json)
    return FidelityGate(openai_client=client)


@pytest.mark.asyncio
async def test_generate_condicao_comercial_aciona_portao_e_aprova():
    """Resposta que toca condicao comercial (dec-010) aciona o portao; fiel=true
    -> texto original e enviado normalmente (FR-008/FR-009)."""
    client = _make_openai_mock(
        _pacote_json(texto="O valor do curso é R$ 5.000 à vista.")
    )
    gate = _make_fidelity_gate(fiel=True)
    responder = GroundedResponder(openai_client=client, fidelity_gate=gate)

    texto, handoff = await responder.generate(
        user_message="qual o preço?",
        caminho="hg360-sp",
        etapa="duvidas",
        knowledge_context="O curso HG360 custa R$ 5.000 à vista.",
        idioma="pt",
    )

    assert texto == "O valor do curso é R$ 5.000 à vista."
    assert handoff is False
    gate._client.chat_cheap_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_portao_reprovado_cai_no_bloco_canonico_e_handoff():
    """fiel=false -> NAO envia o texto reprovado; cai no bloco canonico
    'informacao indisponivel' com precisa_handoff=True (fail-closed, FR-012)."""
    client = _make_openai_mock(
        _pacote_json(texto="O valor do curso é R$ 3.000 à vista.")
    )
    gate = _make_fidelity_gate(
        fiel=False, afirmacoes=["valor do curso é R$ 3.000"]
    )
    responder = GroundedResponder(openai_client=client, fidelity_gate=gate)

    texto, handoff = await responder.generate(
        user_message="qual o preço?",
        caminho="hg360-sp",
        etapa="duvidas",
        knowledge_context="O curso HG360 custa R$ 5.000 à vista.",
        idioma="pt",
    )

    assert texto != "O valor do curso é R$ 3.000 à vista."
    assert (
        "não tenho essa informação" in texto.lower()
        or "nao tenho essa informacao" in texto.lower()
    )
    assert handoff is True


@pytest.mark.asyncio
async def test_generate_duvida_neutra_nao_aciona_portao():
    """Resposta sem condicao comercial (FR-011) NAO aciona o portao — o client
    mockado do gate nunca e chamado."""
    client = _make_openai_mock(
        _pacote_json(texto="O curso aborda técnicas avançadas de harmonização.")
    )
    gate = _make_fidelity_gate(fiel=True)
    responder = GroundedResponder(openai_client=client, fidelity_gate=gate)

    texto, handoff = await responder.generate(
        user_message="do que se trata o curso?",
        caminho="hg360-sp",
        etapa="duvidas",
        knowledge_context="Base.",
        idioma="pt",
    )

    assert texto == "O curso aborda técnicas avançadas de harmonização."
    assert handoff is False
    gate._client.chat_cheap_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_sem_fidelity_gate_configurado_nao_verifica():
    """Retrocompatibilidade: fidelity_gate=None (default) desativa o portao —
    resposta com condicao comercial e enviada sem verificacao (instancias que
    nao cobrem este pilar, ex.: testes legados)."""
    client = _make_openai_mock(
        _pacote_json(texto="O valor do curso é R$ 5.000 à vista.")
    )
    responder = GroundedResponder(openai_client=client)  # sem fidelity_gate

    texto, handoff = await responder.generate(
        user_message="qual o preço?",
        caminho="hg360-sp",
        etapa="duvidas",
        knowledge_context="Base.",
        idioma="pt",
    )

    assert texto == "O valor do curso é R$ 5.000 à vista."
    assert handoff is False


@pytest.mark.asyncio
async def test_generate_verbatim_nunca_passa_pelo_portao():
    """Blocos verbatim (menu/apresentacao/paciente-modelo) sao gerados por
    metodos dedicados (generate_menu/generate_not_eligible/
    generate_paciente_modelo) que NUNCA chamam generate() nem o LLM — logo
    nunca passam pelo Portao de Fidelidade (mesma excecao do FR-007/FR-012)."""
    client = _make_openai_mock()
    gate = _make_fidelity_gate(fiel=False, afirmacoes=["qualquer coisa"])
    responder = GroundedResponder(openai_client=client, fidelity_gate=gate)

    menu = await responder.generate_menu(idioma="pt")
    nao_elegivel = await responder.generate_not_eligible(idioma="pt")
    paciente = await responder.generate_paciente_modelo(
        nidia_phone="5511999999999", idioma="pt"
    )

    assert "GoldIncision" in menu
    assert "médicos" in nao_elegivel or "medicos" in nao_elegivel
    assert "5511999999999" in paciente
    gate._client.chat_cheap_json.assert_not_awaited()
    client.chat_reasoning_json.assert_not_awaited()
