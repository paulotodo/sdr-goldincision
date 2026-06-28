"""
Testes do classificador de intencao e idioma (task 4.1.4).

Cenarios:
- Intencao clara → fluxo direto (sem menu)
- Intencao ambigua → menu
- Troca de idioma persiste no contexto
- Fallback gracioso em caso de falha do LLM
- JSON invalido retorna AMBIGUA/PT
"""
from __future__ import annotations

import json

import pytest

from app.core.intent import (
    INTENCAO_PARA_CAMINHO,
    ClassificacaoIntencao,
    Idioma,
    IntentClassifier,
    _parse_classify_response,
    _parse_idioma,
    _parse_intencao,
)

# ---------------------------------------------------------------------------
# Mock do OpenAI client
# ---------------------------------------------------------------------------

class MockOpenAIClient:
    """Mock do cliente OpenAI para testes (sem chamadas reais)."""

    def __init__(self, response_json: dict = None, should_raise: bool = False):
        self._response_json = response_json or {}
        self._should_raise = should_raise
        self.last_messages: list[dict] = []

    async def chat_cheap(self, messages: list[dict], **kwargs) -> str:
        self.last_messages = messages
        if self._should_raise:
            raise RuntimeError("OpenAI API error simulada")
        return json.dumps(self._response_json)


# ---------------------------------------------------------------------------
# Testes de classificacao de intencao
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_intencao_clara_curso_online():
    """Intencao clara para curso online → ClassificacaoIntencao.CURSO_ONLINE."""
    client = MockOpenAIClient(
        response_json={"intencao": "curso_online", "idioma": "pt", "confianca": "alta"}
    )
    classifier = IntentClassifier(client)

    intencao, idioma = await classifier.classify("Quero saber sobre o curso online de harmonizacao")

    assert intencao == ClassificacaoIntencao.CURSO_ONLINE
    assert idioma == Idioma.PT


@pytest.mark.asyncio
async def test_intencao_clara_hg360_sp():
    """Intencao clara para HG360 SP."""
    client = MockOpenAIClient(
        response_json={"intencao": "hg360_sp", "idioma": "pt", "confianca": "alta"}
    )
    classifier = IntentClassifier(client)

    intencao, idioma = await classifier.classify("Preciso de informações sobre o HG360 em São Paulo")

    assert intencao == ClassificacaoIntencao.HG360_SP


@pytest.mark.asyncio
async def test_intencao_clara_barcelona():
    """Intencao clara para HG360 Barcelona."""
    client = MockOpenAIClient(
        response_json={"intencao": "hg360_barcelona", "idioma": "es", "confianca": "alta"}
    )
    classifier = IntentClassifier(client)

    intencao, idioma = await classifier.classify("Información sobre el curso en Barcelona")

    assert intencao == ClassificacaoIntencao.HG360_BARCELONA
    assert idioma == Idioma.ES


@pytest.mark.asyncio
async def test_intencao_ambigua_gera_menu():
    """Intencao ambigua retorna AMBIGUA → menu de opcoes."""
    client = MockOpenAIClient(
        response_json={"intencao": "ambigua", "idioma": "pt", "confianca": "baixa"}
    )
    classifier = IntentClassifier(client)

    intencao, idioma = await classifier.classify("Olá, quero saber mais")

    assert intencao == ClassificacaoIntencao.AMBIGUA
    # AMBIGUA nao tem caminho mapeado
    assert classifier.get_caminho(intencao) is None


@pytest.mark.asyncio
async def test_confianca_baixa_rebaixa_para_ambigua():
    """Confianca baixa em intencao especifica → rebaixada para AMBIGUA."""
    client = MockOpenAIClient(
        response_json={"intencao": "curso_online", "idioma": "pt", "confianca": "baixa"}
    )
    classifier = IntentClassifier(client)

    intencao, _ = await classifier.classify("talvez o curso online ou outra coisa")

    assert intencao == ClassificacaoIntencao.AMBIGUA


@pytest.mark.asyncio
async def test_troca_de_idioma_persiste():
    """Lead muda de idioma no meio: novo idioma e retornado."""
    # Primeira classificacao em PT
    client_pt = MockOpenAIClient(
        response_json={"intencao": "curso_online", "idioma": "pt", "confianca": "alta"}
    )
    classifier = IntentClassifier(client_pt)
    _, idioma_1 = await classifier.classify("Quero o curso")
    assert idioma_1 == Idioma.PT

    # Segunda classificacao em EN (mudanca de idioma)
    client_en = MockOpenAIClient(
        response_json={"intencao": "curso_online", "idioma": "en", "confianca": "alta"}
    )
    classifier2 = IntentClassifier(client_en)
    _, idioma_2 = await classifier2.classify(
        "I want the course", session_context={"idioma": "pt"}
    )
    assert idioma_2 == Idioma.EN


@pytest.mark.asyncio
async def test_fallback_gracioso_em_erro():
    """Falha no LLM retorna AMBIGUA/PT como fallback, sem propagar excecao."""
    client = MockOpenAIClient(should_raise=True)
    classifier = IntentClassifier(client)

    # Nao deve propagar excecao
    intencao, idioma = await classifier.classify("qualquer mensagem")

    assert intencao == ClassificacaoIntencao.AMBIGUA
    assert idioma == Idioma.PT


@pytest.mark.asyncio
async def test_json_invalido_retorna_ambigua():
    """JSON invalido na resposta do LLM → fallback AMBIGUA/PT."""
    client = MockOpenAIClient()
    client._response_json = None  # type: ignore

    # Patch para retornar texto nao-JSON
    async def bad_cheap(messages, **kwargs):
        return "isso nao e json valido"

    client.chat_cheap = bad_cheap  # type: ignore
    classifier = IntentClassifier(client)

    intencao, idioma = await classifier.classify("teste")
    assert intencao == ClassificacaoIntencao.AMBIGUA
    assert idioma == Idioma.PT


# ---------------------------------------------------------------------------
# Testes de mapeamento intencao → caminho
# ---------------------------------------------------------------------------

def test_mapeamento_intencao_para_caminho():
    """Todas as intencoes claras mapeiam para caminhos validos 1-6."""
    mapa = {
        ClassificacaoIntencao.CURSO_ONLINE: 1,
        ClassificacaoIntencao.HG_MODULO_1: 2,
        ClassificacaoIntencao.HG360_SP: 3,
        ClassificacaoIntencao.HG360_BARCELONA: 4,
        ClassificacaoIntencao.PACIENTE_MODELO: 5,
        ClassificacaoIntencao.LICENCIAMENTO_FRANQUIA: 6,
    }
    for intencao, esperado in mapa.items():
        assert INTENCAO_PARA_CAMINHO[intencao] == esperado


def test_ambigua_nao_tem_caminho():
    """AMBIGUA nao esta mapeada."""
    assert ClassificacaoIntencao.AMBIGUA not in INTENCAO_PARA_CAMINHO


# ---------------------------------------------------------------------------
# Testes dos helpers internos
# ---------------------------------------------------------------------------

def test_parse_intencao_valores_validos():
    assert _parse_intencao("curso_online") == ClassificacaoIntencao.CURSO_ONLINE
    assert _parse_intencao("hg360_barcelona") == ClassificacaoIntencao.HG360_BARCELONA
    assert _parse_intencao("paciente_modelo") == ClassificacaoIntencao.PACIENTE_MODELO


def test_parse_intencao_valor_invalido():
    assert _parse_intencao("inexistente") == ClassificacaoIntencao.AMBIGUA


def test_parse_idioma_valores_validos():
    assert _parse_idioma("pt") == Idioma.PT
    assert _parse_idioma("en") == Idioma.EN
    assert _parse_idioma("es") == Idioma.ES


def test_parse_idioma_valor_invalido():
    assert _parse_idioma("fr") == Idioma.PT  # fallback PT


def test_parse_classify_response_json_com_code_fence():
    """Remove markdown code fence antes de parsear."""
    raw = '```json\n{"intencao": "curso_online", "idioma": "pt", "confianca": "alta"}\n```'
    result = _parse_classify_response(raw)
    assert result.get("intencao") == "curso_online"


def test_parse_classify_response_json_puro():
    raw = '{"intencao": "ambigua", "idioma": "es", "confianca": "baixa"}'
    result = _parse_classify_response(raw)
    assert result.get("idioma") == "es"


def test_parse_classify_response_invalido_retorna_vazio():
    result = _parse_classify_response("texto qualquer sem json")
    assert result == {}
