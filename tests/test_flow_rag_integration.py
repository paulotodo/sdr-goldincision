"""
Testes de integracao RAG hibrido nos 3 call-sites de ETAPA_DUVIDAS
(Onda 3, FASE 5, task 5.1.6 — `plan.md` flow.py:1641/1830/2046).

Diferente de `tests/test_flow.py` (StubFlowEngine STUBA
`_load_knowledge_by_slug` inteiramente), aqui o FlowEngine e exercitado com
a implementacao PRODUCAO de `_load_knowledge_by_slug`/`_load_knowledge`
(nao stubada) — o UNICO servico externo mockado e o `OpenAIClient`
(embeddings + chat_reasoning_json). `_get_curso`/`_scalar_idioma` sao
stubados (sem Postgres real, mesmo padrao de StubFlowEngine); a fronteira
mockavel do RAG (`ChunkRepository` Protocol, FASE 4) usa um fake em
memoria — nenhuma chamada de rede/DB real em nenhum dos dois casos.

Cobre:
- 5.1.6: os 3 call-sites (licenciamento-duvidas, curso-online-duvidas,
  presencial-duvidas) — abster=True curto-circuita SEM chamar
  `GroundedResponder.generate()`/o LLM; recuperacao bem-sucedida alimenta
  `knowledge_context` e `chunks_recuperados`.
- 5.3.2: `FidelityGate.verificar()` recebe o MESMO `knowledge_context`
  formado pelos chunks recuperados (nunca um conjunto mais amplo/diferente).
- 5.4.2: `fonte_ids` chega aditivamente a `FlowResult`/`log_turno` sem
  quebrar o payload existente.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.contracts import RespostaEstruturada
from app.core.fidelity import FidelityGate, VeredictoFidelidade
from app.core.flow import (
    _SLUG_HG_MODULO_1,
    _SLUG_LICENCIAMENTO,
    ETAPA_DUVIDAS,
    ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS,
    CaminhoMapaMestre,
    FlowEngine,
)
from app.core.intent import ClassificacaoIntencao, Idioma
from app.core.memory import SessionContext
from app.core.responder import GroundedResponder
from app.core.retrieval import ChunkCandidato, HybridRetriever
from app.observability.log import log_turno
from app.repository.models import Curso


class FakeChunkRepository:
    """Fake em memoria do `ChunkRepository` (Protocol, FASE 4) — mesma
    fronteira mockavel usada por `tests/test_retrieval.py`, aqui reusada
    localmente para manter este arquivo self-contained."""

    def __init__(
        self,
        vetorial: Optional[list[ChunkCandidato]] = None,
        textual: Optional[list[ChunkCandidato]] = None,
    ) -> None:
        self._vetorial = vetorial or []
        self._textual = textual or []

    async def buscar_vetorial(self, query_embedding, curso_id, idioma, k):
        return [
            c for c in self._vetorial
            if (c.curso_id == curso_id or c.curso_id is None) and c.idioma == idioma
        ][:k]

    async def buscar_textual(self, query, curso_id, idioma, k):
        return [
            c for c in self._textual
            if (c.curso_id == curso_id or c.curso_id is None) and c.idioma == idioma
        ][:k]


class MockIntent:
    """Classificador estavel: intencao AMBIGUA (mudanca de caminho
    nao interfere no roteamento — `context.caminho`/`context.etapa` ja
    posicionam diretamente no call-site alvo)."""

    async def classify(self, message: str, session_context=None):
        return ClassificacaoIntencao.AMBIGUA, Idioma.PT


class MockMemory:
    def build_messages_for_llm(self, context, max_msgs=10):
        return []


def _make_openai_client(pacote_texto: str = "Resposta ancorada na base.") -> AsyncMock:
    client = AsyncMock()
    client.embed = AsyncMock(return_value=[[0.1] * 1536])
    client.chat_reasoning_json = AsyncMock(
        return_value=RespostaEstruturada(
            texto=pacote_texto, fontes=[], precisa_handoff=False,
            confianca=0.9, idioma="pt",
        ).model_dump_json()
    )
    return client


class _RealKnowledgeEngine(FlowEngine):
    """FlowEngine REAL com `_load_knowledge_by_slug`/`_load_knowledge`
    PRODUCAO (NAO stubados) — exercita a integracao RAG de fato. Apenas
    `_get_curso`/`_scalar_idioma` sao stubados (sem Postgres real); Turmas
    vem de `self._db` (AsyncMock configurado para lista vazia)."""

    def __init__(self, *, curso, apres_texto, responder, retriever):
        super().__init__(
            db_session=AsyncMock(),
            intent_classifier=MockIntent(),
            memory_manager=MockMemory(),
            responder=responder,
            nidia_phone="+55 21 97423-9844",
            retriever=retriever,
        )
        # MagicMock (nao AsyncMock) explicito: evita que `.scalars()`/`.all()`
        # herdem AsyncMock recursivamente (o que faria `.scalars()` retornar
        # uma coroutine em vez de um resultado sincrono navegavel).
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        self._db.execute.return_value = db_result
        self._curso = curso
        self._apres_texto = apres_texto

    async def _get_curso(self, slug: str):
        return self._curso

    async def _scalar_idioma(self, model, curso_id, idioma):
        if getattr(model, "__name__", "") == "CursoApresentacao" and self._apres_texto:
            return SimpleNamespace(texto=self._apres_texto)
        return None


def _ctx(**overrides) -> SessionContext:
    base = dict(
        ticket_id=1, chamado_id=1, contato_id=10,
        eh_medico=True, especialidade="Cirurgia", experiencia_corporal=True,
        resumo_rolante=None, historico_recente=[], sessao_id=100, nome="Ana",
    )
    base.update(overrides)
    return SessionContext(**base)


# ---------------------------------------------------------------------------
# 5.1.6 — call-site Licenciamento (flow.py:1641/1656)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_licenciamento_duvidas_abster_curto_circuita_sem_chamar_llm():
    """abster=True (sem candidatos na base) -> retorno direto do bloco
    canonico + handoff, SEM chamar `GroundedResponder.generate()`/o LLM
    (research.md Decision 7)."""
    curso = Curso(id=1, slug=_SLUG_LICENCIAMENTO, nome="Licenciamento", tipo="licenciamento")
    openai_client = _make_openai_client()
    retriever = HybridRetriever(
        chunk_repository=FakeChunkRepository(vetorial=[], textual=[]),
        openai_client=openai_client,
    )
    responder = GroundedResponder(openai_client=openai_client)
    engine = _RealKnowledgeEngine(
        curso=curso, apres_texto="Apresentacao oficial.", responder=responder,
        retriever=retriever,
    )
    ctx = _ctx(
        caminho=CaminhoMapaMestre.SISTEMA_GOLDINCISION,
        etapa=ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS,
        idioma="pt",
    )

    result = await engine._handle_sistema_goldincision(
        ctx, "Como funciona a validacao internacional?", {}
    )

    assert result.action == "handoff"
    openai_client.chat_reasoning_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_licenciamento_duvidas_recuperacao_alimenta_knowledge_context():
    """Recuperacao bem-sucedida -> generate() e chamado com `knowledge_context`
    contendo o conteudo do chunk recuperado + `chunks_recuperados` -> `fonte_ids`."""
    curso = Curso(id=1, slug=_SLUG_LICENCIAMENTO, nome="Licenciamento", tipo="licenciamento")
    candidato = ChunkCandidato(
        chunk_id=42, conteudo="Reconhecido em todo o mercado internacional.",
        tipo="objecao", curso_id=1, idioma="pt", distancia_cosseno=0.05,
    )
    openai_client = _make_openai_client(pacote_texto="Sim, e reconhecido internacionalmente.")
    retriever = HybridRetriever(
        chunk_repository=FakeChunkRepository(vetorial=[candidato], textual=[]),
        openai_client=openai_client,
    )
    responder = GroundedResponder(openai_client=openai_client)
    engine = _RealKnowledgeEngine(
        curso=curso, apres_texto="Apresentacao oficial.", responder=responder,
        retriever=retriever,
    )
    ctx = _ctx(
        caminho=CaminhoMapaMestre.SISTEMA_GOLDINCISION,
        etapa=ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS,
        idioma="pt",
    )

    result = await engine._handle_sistema_goldincision(
        ctx, "Isso e reconhecido internacionalmente?", {}
    )

    openai_client.chat_reasoning_json.assert_awaited_once()
    kwargs = openai_client.chat_reasoning_json.call_args
    system_content = kwargs.args[0][0]["content"]
    assert "Reconhecido em todo o mercado internacional." in system_content
    assert result.action == "continue"
    assert responder.last_fonte_ids == ["42"]


# ---------------------------------------------------------------------------
# 5.1.6 — call-site Curso Online (flow.py:1830/1849)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_curso_online_duvidas_abster_curto_circuita_sem_chamar_llm():
    curso = Curso(id=2, slug="curso-online-hg", nome="Curso Online HG", tipo="online")
    openai_client = _make_openai_client()
    retriever = HybridRetriever(
        chunk_repository=FakeChunkRepository(vetorial=[], textual=[]),
        openai_client=openai_client,
    )
    responder = GroundedResponder(openai_client=openai_client)
    engine = _RealKnowledgeEngine(
        curso=curso, apres_texto="Apresentacao oficial.", responder=responder,
        retriever=retriever,
    )
    ctx = _ctx(
        caminho=CaminhoMapaMestre.CURSO_ONLINE_HG, etapa=ETAPA_DUVIDAS, idioma="pt",
    )

    result = await engine._responder_duvida_online(ctx, "Qual a carga horaria?", {})

    assert result.action == "handoff"
    assert result.response_text  # bloco canonico, nunca vazio
    openai_client.chat_reasoning_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_curso_online_duvidas_recuperacao_bem_sucedida_gera_resposta():
    curso = Curso(id=2, slug="curso-online-hg", nome="Curso Online HG", tipo="online")
    candidato = ChunkCandidato(
        chunk_id=99, conteudo="Carga horaria de 40 horas em video-aulas.",
        tipo="faq", curso_id=2, idioma="pt", distancia_cosseno=0.05,
    )
    openai_client = _make_openai_client(pacote_texto="A carga horaria e de 40h.")
    retriever = HybridRetriever(
        chunk_repository=FakeChunkRepository(vetorial=[candidato], textual=[]),
        openai_client=openai_client,
    )
    responder = GroundedResponder(openai_client=openai_client)
    engine = _RealKnowledgeEngine(
        curso=curso, apres_texto="Apresentacao oficial.", responder=responder,
        retriever=retriever,
    )
    ctx = _ctx(
        caminho=CaminhoMapaMestre.CURSO_ONLINE_HG, etapa=ETAPA_DUVIDAS, idioma="pt",
    )

    result = await engine._responder_duvida_online(ctx, "Qual a carga horaria?", {})

    assert result.action == "continue"
    assert result.response_text == "A carga horaria e de 40h."
    assert responder.last_fonte_ids == ["99"]


# ---------------------------------------------------------------------------
# 5.1.6 — call-site Presencial (flow.py:2046/2069)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_presencial_duvidas_abster_curto_circuita_sem_chamar_llm():
    curso = Curso(id=3, slug=_SLUG_HG_MODULO_1, nome="HG Modulo 1", tipo="presencial")
    openai_client = _make_openai_client()
    retriever = HybridRetriever(
        chunk_repository=FakeChunkRepository(vetorial=[], textual=[]),
        openai_client=openai_client,
    )
    responder = GroundedResponder(openai_client=openai_client)
    engine = _RealKnowledgeEngine(
        curso=curso, apres_texto="Apresentacao oficial.", responder=responder,
        retriever=retriever,
    )
    ctx = _ctx(
        caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS, etapa=ETAPA_DUVIDAS, idioma="pt",
    )

    result = await engine._responder_duvida_presencial(
        ctx, "Tem parcelamento?", _SLUG_HG_MODULO_1, {}
    )

    assert result.action == "handoff"
    openai_client.chat_reasoning_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_presencial_duvidas_recuperacao_bem_sucedida_gera_resposta():
    curso = Curso(id=3, slug=_SLUG_HG_MODULO_1, nome="HG Modulo 1", tipo="presencial")
    candidato = ChunkCandidato(
        chunk_id=7, conteudo="Parcelamos em ate 12x no cartao.",
        tipo="objecao", curso_id=3, idioma="pt", distancia_cosseno=0.05,
    )
    openai_client = _make_openai_client(pacote_texto="Sim, parcelamos em ate 12x.")
    retriever = HybridRetriever(
        chunk_repository=FakeChunkRepository(vetorial=[candidato], textual=[]),
        openai_client=openai_client,
    )
    responder = GroundedResponder(openai_client=openai_client)
    engine = _RealKnowledgeEngine(
        curso=curso, apres_texto="Apresentacao oficial.", responder=responder,
        retriever=retriever,
    )
    ctx = _ctx(
        caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS, etapa=ETAPA_DUVIDAS, idioma="pt",
    )

    result = await engine._responder_duvida_presencial(
        ctx, "Tem parcelamento?", _SLUG_HG_MODULO_1, {}
    )

    assert result.action == "continue"
    assert responder.last_fonte_ids == ["7"]


# ---------------------------------------------------------------------------
# 5.3.2 — FidelityGate.verificar() recebe o MESMO knowledge_context
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fidelity_gate_recebe_o_mesmo_knowledge_context_dos_chunks():
    """O portao de fidelidade (quando acionado) NUNCA valida contra um
    conjunto mais amplo/diferente do que efetivamente embasou a resposta —
    ele recebe o MESMO `knowledge_context` montado a partir dos chunks
    recuperados (FR-012, sem mudanca de assinatura de `FidelityGate`)."""
    curso = Curso(id=2, slug="curso-online-hg", nome="Curso Online HG", tipo="online")
    candidato = ChunkCandidato(
        chunk_id=55, conteudo="Parcelamos a mensalidade em ate 12x sem juros.",
        tipo="objecao", curso_id=2, idioma="pt", distancia_cosseno=0.05,
    )
    openai_client = _make_openai_client(pacote_texto="Sim, oferecemos parcelamento em 12x.")
    retriever = HybridRetriever(
        chunk_repository=FakeChunkRepository(vetorial=[candidato], textual=[]),
        openai_client=openai_client,
    )
    # Forcar fiel=True sem depender do LLM real do gate (evita 2a chamada
    # chat_cheap_json instavel neste teste de integracao): stub direto no
    # metodo, mas AINDA capturando os argumentos recebidos (spy via Mock).
    fidelity_gate = FidelityGate(openai_client=openai_client)
    fidelity_gate.verificar = AsyncMock(
        return_value=VeredictoFidelidade(fiel=True, afirmacoes_nao_sustentadas=[])
    )
    responder = GroundedResponder(openai_client=openai_client, fidelity_gate=fidelity_gate)
    engine = _RealKnowledgeEngine(
        curso=curso, apres_texto="Apresentacao oficial.", responder=responder,
        retriever=retriever,
    )
    ctx = _ctx(
        caminho=CaminhoMapaMestre.CURSO_ONLINE_HG, etapa=ETAPA_DUVIDAS, idioma="pt",
    )

    await engine._responder_duvida_online(ctx, "Tem parcelamento?", {})

    fidelity_gate.verificar.assert_awaited_once()
    texto_arg, knowledge_context_arg = fidelity_gate.verificar.call_args.args
    assert "Parcelamos a mensalidade em ate 12x sem juros." in knowledge_context_arg


# ---------------------------------------------------------------------------
# 5.4.2 — fonte_ids aditivo em FlowResult / log_turno
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fonte_ids_chega_ao_flow_result_via_process(capsys):
    """`process()` (topo do FlowEngine) propaga `fonte_ids` pos-hoc ao
    `FlowResult` final, a partir de `GroundedResponder.last_fonte_ids`."""
    curso = Curso(id=2, slug="curso-online-hg", nome="Curso Online HG", tipo="online")
    candidato = ChunkCandidato(
        chunk_id=17, conteudo="Emitimos certificado ao final do curso.",
        tipo="faq", curso_id=2, idioma="pt", distancia_cosseno=0.05,
    )
    openai_client = _make_openai_client(pacote_texto="Sim, emitimos certificado.")
    retriever = HybridRetriever(
        chunk_repository=FakeChunkRepository(vetorial=[candidato], textual=[]),
        openai_client=openai_client,
    )
    responder = GroundedResponder(openai_client=openai_client)
    engine = _RealKnowledgeEngine(
        curso=curso, apres_texto="Apresentacao oficial.", responder=responder,
        retriever=retriever,
    )
    ctx = _ctx(
        caminho=CaminhoMapaMestre.CURSO_ONLINE_HG, etapa=ETAPA_DUVIDAS, idioma="pt",
    )

    result = await engine.process(1, "Emite certificado?", ctx)

    assert result.fonte_ids == ["17"]


def test_log_turno_aditivo_com_fonte_ids(capsys):
    """`fonte_ids` e aditivo: uma chamada sem o kwarg produz o payload de
    sempre; com o kwarg, o campo aparece SEM quebrar nada existente
    (US4/FR-018)."""
    log_turno(
        chamado_id=1, turno_sessao=1, etapa_entrada="duvidas", etapa_saida="duvidas",
        idioma="pt", n_blocos_enviados=1, acao="resposta", duracao_ms=100,
        tentativas=0, fonte_ids=["17", "42"],
    )
    out = capsys.readouterr().out
    assert '"fonte_ids": ["17", "42"]' in out or '"fonte_ids":["17","42"]' in out


def test_log_turno_sem_fonte_ids_omite_o_campo(capsys):
    """None (default) -> campo OMITIDO do evento (contrato aditivo exato)."""
    log_turno(
        chamado_id=1, turno_sessao=1, etapa_entrada="menu", etapa_saida="menu",
        idioma="pt", n_blocos_enviados=1, acao="resposta", duracao_ms=100,
        tentativas=0,
    )
    out = capsys.readouterr().out
    assert "fonte_ids" not in out
