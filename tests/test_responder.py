"""
Testes para app/core/responder.py (concisao das respostas geradas).

Cobre:
- generate() chama chat_reasoning com o teto de tokens configurado (concisao).
- O default (REASONING_MAX_TOKENS) e usado quando nao informado.
- O prompt de sistema reforca objetividade/resumo.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.responder import (
    _SYSTEM_BASE,
    REASONING_MAX_TOKENS,
    GroundedResponder,
)


def _make_openai_mock(text: str = "Resposta curta.") -> AsyncMock:
    client = AsyncMock()
    client.chat_reasoning = AsyncMock(return_value=text)
    return client


@pytest.mark.asyncio
async def test_generate_usa_max_tokens_configurado():
    """generate() repassa o max_tokens configurado ao chat_reasoning."""
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
    client.chat_reasoning.assert_awaited_once()
    kwargs = client.chat_reasoning.call_args.kwargs
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

    kwargs = client.chat_reasoning.call_args.kwargs
    assert kwargs["max_tokens"] == REASONING_MAX_TOKENS
    assert REASONING_MAX_TOKENS <= 320, "teto deve ser conciso (era 600 antes)"


def test_system_prompt_reforca_concisao():
    """O prompt base instrui respostas objetivas e resumidas, sem despejar tudo."""
    base_lower = _SYSTEM_BASE.lower()
    assert "objetivo" in base_lower and "resumid" in base_lower
    assert "apresentação inteira" in base_lower or "apresentacao inteira" in base_lower
