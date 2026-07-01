"""
Testes para app/core/contracts.py (Pilar 6, FR-001..FR-007).

Cobre:
- Payload valido aceito.
- Campo extra rejeitado (extra="forbid").
- Campos obrigatorios ausentes rejeitados.
- Tipos/limites invalidos rejeitados (confianca fora de [0,1], idioma fora do enum).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.contracts import RespostaEstruturada


def test_payload_valido_e_aceito():
    """Payload completo e valido e aceito e preserva os campos."""
    pacote = RespostaEstruturada(
        texto="O curso tem duração de 3 dias.",
        fontes=["base-hg360"],
        precisa_handoff=False,
        confianca=0.92,
        idioma="pt",
    )

    assert pacote.texto == "O curso tem duração de 3 dias."
    assert pacote.fontes == ["base-hg360"]
    assert pacote.precisa_handoff is False
    assert pacote.confianca == 0.92
    assert pacote.idioma == "pt"


def test_defaults_fontes_e_precisa_handoff():
    """fontes e precisa_handoff tem defaults quando omitidos."""
    pacote = RespostaEstruturada(texto="Resposta.", confianca=0.5, idioma="en")

    assert pacote.fontes == []
    assert pacote.precisa_handoff is False


def test_campo_extra_e_rejeitado():
    """extra="forbid": campo nao previsto no schema invalida o pacote (FR-002)."""
    with pytest.raises(ValidationError):
        RespostaEstruturada(
            texto="Resposta.",
            confianca=0.5,
            idioma="pt",
            destino_handoff="consultores",  # campo NAO permitido (FR-006)
        )


def test_texto_vazio_e_rejeitado():
    """texto tem min_length=1 — string vazia invalida o pacote."""
    with pytest.raises(ValidationError):
        RespostaEstruturada(texto="", confianca=0.5, idioma="pt")


def test_texto_ausente_e_rejeitado():
    """texto e obrigatorio."""
    with pytest.raises(ValidationError):
        RespostaEstruturada(confianca=0.5, idioma="pt")


def test_confianca_ausente_e_rejeitada():
    """confianca e obrigatoria."""
    with pytest.raises(ValidationError):
        RespostaEstruturada(texto="Resposta.", idioma="pt")


@pytest.mark.parametrize("confianca", [-0.1, 1.1, 2.0])
def test_confianca_fora_do_intervalo_e_rejeitada(confianca):
    """confianca deve estar em [0.0, 1.0]."""
    with pytest.raises(ValidationError):
        RespostaEstruturada(texto="Resposta.", confianca=confianca, idioma="pt")


@pytest.mark.parametrize("idioma", ["fr", "de", "PT", ""])
def test_idioma_fora_do_enum_e_rejeitado(idioma):
    """idioma deve casar com o padrao ^(pt|en|es)$ (FR-005)."""
    with pytest.raises(ValidationError):
        RespostaEstruturada(texto="Resposta.", confianca=0.5, idioma=idioma)


def test_model_validate_json_roundtrip():
    """model_validate_json aceita o JSON produzido por model_dump_json (usado
    pelo GroundedResponder.generate() ao parsear o retorno do contrato)."""
    original = RespostaEstruturada(
        texto="Resposta.", fontes=["base"], confianca=0.8, idioma="es"
    )
    reconstruido = RespostaEstruturada.model_validate_json(original.model_dump_json())

    assert reconstruido == original
