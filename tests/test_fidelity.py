"""
Testes para app/core/fidelity.py (Pilar 7 — Portao de Fidelidade, FR-008..FR-012).

Cobre:
- VeredictoFidelidade: schema valido, invariante fiel=True apenas sem afirmacoes.
- gatilho_condicao_comercial: aciona por categoria de condicao comercial (dec-010);
  nao aciona para saudacao/rapport/duvida neutra.
- FidelityGate.verificar(): fiel=true; fiel=false com afirmacoes; timeout ->
  fail-closed; erro do client -> fail-closed.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.fidelity import (
    INDISPONIVEL,
    FidelityGate,
    VeredictoFidelidade,
    gatilho_condicao_comercial,
)

# ---------------------------------------------------------------------------
# VeredictoFidelidade — schema + invariante
# ---------------------------------------------------------------------------


def test_veredito_fiel_sem_afirmacoes_e_valido():
    v = VeredictoFidelidade(fiel=True, afirmacoes_nao_sustentadas=[])
    assert v.fiel is True
    assert v.afirmacoes_nao_sustentadas == []


def test_veredito_nao_fiel_com_afirmacoes_e_valido():
    v = VeredictoFidelidade(
        fiel=False, afirmacoes_nao_sustentadas=["curso custa R$ 500"]
    )
    assert v.fiel is False
    assert v.afirmacoes_nao_sustentadas == ["curso custa R$ 500"]


def test_veredito_defaults_afirmacoes_vazia():
    v = VeredictoFidelidade(fiel=True)
    assert v.afirmacoes_nao_sustentadas == []


def test_veredito_inconsistente_e_forcado_fail_closed():
    """fiel=true COM afirmacoes listadas e uma inconsistencia do LLM — o
    modelo forca fiel=False defensivamente (fail-closed)."""
    v = VeredictoFidelidade(
        fiel=True, afirmacoes_nao_sustentadas=["afirmacao suspeita"]
    )
    assert v.fiel is False


def test_veredito_campo_extra_e_rejeitado():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        VeredictoFidelidade(fiel=True, campo_extra="nao permitido")


def test_veredito_model_validate_json_roundtrip():
    original = VeredictoFidelidade(fiel=False, afirmacoes_nao_sustentadas=["x"])
    reconstruido = VeredictoFidelidade.model_validate_json(original.model_dump_json())
    assert reconstruido == original


# ---------------------------------------------------------------------------
# gatilho_condicao_comercial — dec-010
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "O valor do curso é R$ 5.000 à vista.",
        "Temos 10% de desconto para pagamento à vista.",
        "A próxima turma tem data prevista para agosto.",
        "Ainda há vagas disponíveis para esta turma.",
        "É necessário ter CRM ativo para se inscrever.",
        "Você pode parcelar em até 12x.",
        "Corra, promoção válida até sexta-feira.",
        "O prazo de inscrição termina em breve.",
    ],
)
def test_gatilho_aciona_por_condicao_comercial(texto):
    assert gatilho_condicao_comercial(texto) is True


@pytest.mark.parametrize(
    "texto",
    [
        "Olá! Seja bem-vindo à GoldIncision 😊",
        "Que bom que você tem interesse no curso!",
        "O curso aborda técnicas avançadas de harmonização glútea.",
        "Fico à disposição para outras dúvidas.",
        "",
    ],
)
def test_gatilho_nao_aciona_fora_de_condicao_comercial(texto):
    assert gatilho_condicao_comercial(texto) is False


# ---------------------------------------------------------------------------
# FidelityGate.verificar() — fiel / nao-fiel / fail-closed
# ---------------------------------------------------------------------------


def _make_client(raw_json: str | None = None, side_effect=None) -> AsyncMock:
    client = AsyncMock()
    if side_effect is not None:
        client.chat_cheap_json = AsyncMock(side_effect=side_effect)
    else:
        client.chat_cheap_json = AsyncMock(return_value=raw_json)
    return client


@pytest.mark.asyncio
async def test_verificar_fiel_true_quando_sustentado():
    veredito_json = VeredictoFidelidade(
        fiel=True, afirmacoes_nao_sustentadas=[]
    ).model_dump_json()
    client = _make_client(raw_json=veredito_json)
    gate = FidelityGate(openai_client=client)

    veredito = await gate.verificar(
        texto="O curso custa R$ 5.000.",
        knowledge_context="O curso HG360 custa R$ 5.000 à vista.",
    )

    assert veredito.fiel is True
    assert veredito.afirmacoes_nao_sustentadas == []
    client.chat_cheap_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_verificar_fiel_false_lista_afirmacoes_nao_sustentadas():
    veredito_json = VeredictoFidelidade(
        fiel=False, afirmacoes_nao_sustentadas=["curso custa R$ 3.000"]
    ).model_dump_json()
    client = _make_client(raw_json=veredito_json)
    gate = FidelityGate(openai_client=client)

    veredito = await gate.verificar(
        texto="O curso custa R$ 3.000.",
        knowledge_context="O curso HG360 custa R$ 5.000 à vista.",
    )

    assert veredito.fiel is False
    assert veredito.afirmacoes_nao_sustentadas == ["curso custa R$ 3.000"]


@pytest.mark.asyncio
async def test_verificar_timeout_e_fail_closed():
    """Timeout do client (VERIFY_TIMEOUT_SECONDS) -> fiel=False, nunca aprovacao
    por omissao (FR-012)."""

    async def _lento(*args, **kwargs):
        await asyncio.sleep(10)
        return VeredictoFidelidade(fiel=True).model_dump_json()

    client = AsyncMock()
    client.chat_cheap_json = _lento
    gate = FidelityGate(openai_client=client, timeout_seconds=0.05)

    veredito = await gate.verificar(texto="O curso custa R$ 5.000.", knowledge_context="Base.")

    assert veredito.fiel is False
    assert veredito.afirmacoes_nao_sustentadas == [INDISPONIVEL]


@pytest.mark.asyncio
async def test_verificar_erro_do_client_e_fail_closed():
    """Excecao do client (API indisponivel/erro de rede) -> fiel=False (FR-012)."""
    client = _make_client(side_effect=Exception("api indisponivel"))
    gate = FidelityGate(openai_client=client)

    veredito = await gate.verificar(texto="O curso custa R$ 5.000.", knowledge_context="Base.")

    assert veredito.fiel is False
    assert veredito.afirmacoes_nao_sustentadas == [INDISPONIVEL]


@pytest.mark.asyncio
async def test_verificar_json_malformado_e_fail_closed():
    """Payload nao-parseavel do LLM -> fiel=False (fail-closed), nao propaga excecao."""
    client = _make_client(raw_json="not-json-at-all")
    gate = FidelityGate(openai_client=client)

    veredito = await gate.verificar(texto="O curso custa R$ 5.000.", knowledge_context="Base.")

    assert veredito.fiel is False
    assert veredito.afirmacoes_nao_sustentadas == [INDISPONIVEL]


@pytest.mark.asyncio
async def test_verificar_usa_timeout_configurado():
    """FidelityGate respeita o timeout_seconds passado no construtor (VERIFY_TIMEOUT_SECONDS)."""
    veredito_json = VeredictoFidelidade(fiel=True).model_dump_json()
    client = _make_client(raw_json=veredito_json)
    gate = FidelityGate(openai_client=client, timeout_seconds=3.0)

    assert gate._timeout_seconds == 3.0
    veredito = await gate.verificar(texto="preço do curso", knowledge_context="Base.")
    assert veredito.fiel is True
