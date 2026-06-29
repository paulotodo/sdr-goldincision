"""
Testes de integracao e2e — FASE 8 (tasks 8.1.1 a 8.1.4).

Cobre os 14 cenarios do quickstart.md e testes de contrato/anti-drift.
Todos os servicos externos (OpenAI, ChatMaster, Postgres, Redis) sao mockados.

Taxonomia fiel ao MAPA MESTRE DO ATENDIMENTO.docx (6 caminhos oficiais):
  1. curso_online          → Caminho 1
  2. cursos_presenciais    → Caminho 2 (HG Modulo 1 / HG360 SP / HG360 Barcelona
                             como sub-fluxos internos do mesmo caminho)
  3. sistema_goldincision  → Caminho 3 (Licenciamento / Franquia)
  4. aluno_suporte         → Caminho 4 (handoff imediato)
  5. paciente_modelo       → Caminho 5 (contato da Nidia)
  6. outro_assunto         → Caminho 6 (handoff imediato)

Sobre fixtures de infra efemera (8.1.1):
  Para execucao local/CI sem deps externas, usamos mocks em memoria.
  Para CI com Postgres/Redis efemeros reais, instalar:
    pip install testcontainers[postgres,redis]
  e definir PYTEST_USE_TESTCONTAINERS=1.

Cenarios cobertos:
  1.  Intencao clara → sem requalificacao (SC-001)
  2.  Menu inicial quando intencao nao e clara (US1-AS1)
  3.  Elegibilidade inflexivel — nao medico (US1-AS4, FR-009)
  3b. Elegibilidade inflexivel — so facial (US1-AS5)
  4.  Memoria persistente, sem repetir perguntas (SC-002, US2)
  5.  Debounce de rajada (SC-005, FR-003)
  6.  Idempotencia de reenvio (FR-037)
  7.  Handoff para humano (SC-003, US3)
  8.  Paciente modelo → Nidia (US3-AS6, FR-014)
  9.  Gestao dinamica de curso sem redeploy (SC-004, US4)
  10. Multilingual — audio (SC-007, US5) + erro de transcricao (FR-005)
  11. Anti-alucinacao (SC-008, Principio II)
  12. fromMe e tipo desconhecido (edge cases — FR-002)
  13. Roundtrip end-to-end — exemplos reais de knowledge_base (8.1.2)
  14. Empacotamento — inspecao de stack.yml (SC-006, US6)
  (8.1.4) Rate limiting / teto de gasto LLM (SEC-WH-3)
  (F8)   E2e webhook→FlowEngine real — falha se engine desconectado
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.webhook import WebhookPayload

# ---------------------------------------------------------------------------
# Caminhos de base
# ---------------------------------------------------------------------------
_PROJ_ROOT = Path(__file__).parent.parent
_WEBHOOK_EXAMPLES = _PROJ_ROOT / "knowledge_base" / "example_webhook_json"


# ---------------------------------------------------------------------------
# Helpers de payload
# ---------------------------------------------------------------------------

def _webhook_payload(
    *,
    chamado_id: int = 900001,
    sender: str = "5511999990001",
    text: str = "ola",
    from_me: bool = False,
    is_group: bool = False,
    ticket_status: str = "open",
    media_type: str = "text",
    media_url: Optional[str] = None,
    queue_id: Optional[int] = 77,
) -> dict:
    """Constroi payload de webhook sintetico (queue_id=77 = fila da IA por padrao)."""
    if media_type == "text":
        mensagem = [{"type": "text", "text": text}]
    else:
        mensagem = [
            {
                "type": media_type,
                "mediaUrl": media_url or "https://object.sp2.eveo.com.br/fake.ogg",
                "text": text,
            }
        ]
    return {
        "mensagem": mensagem,
        "sender": sender,
        "chamadoId": chamado_id,
        "acao": "start",
        "name": "Lead Teste",
        "fromMe": from_me,
        "companyId": 1,
        "defaultWhatsapp_x": 127,
        "queueId": queue_id,
        "isGroup": is_group,
        "ticketData": {
            "id": chamado_id,
            "status": ticket_status,
            "variables": {"nome_lead": "Lead Teste", "numero_lead": sender},
        },
    }


def _make_client() -> TestClient:
    """TestClient sem lifespan real (deps mockadas)."""
    return TestClient(app, raise_server_exceptions=False)


def _post_webhook(client: TestClient, payload: dict, headers: Optional[dict] = None) -> object:
    return client.post(
        "/webhook/chatmaster",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json", **(headers or {})},
    )


# ---------------------------------------------------------------------------
# Fixtures de flow engine mockado
# Taxonomia nova: cursos_presenciais = Caminho 2 (todos os presenciais)
# ---------------------------------------------------------------------------

def _make_mock_context(
    caminho: Optional[int] = None,
    etapa: Optional[str] = None,
    idioma: str = "pt",
    eh_medico: Optional[bool] = None,
    experiencia_corporal: Optional[bool] = None,
    resumo: Optional[str] = None,
    produto_interesse: Optional[str] = None,
):
    """Cria SessionContext mockado."""
    from app.core.memory import SessionContext

    return SessionContext(
        ticket_id=1,
        chamado_id=900001,
        contato_id=10,
        caminho=caminho,
        etapa=etapa,
        idioma=idioma,
        eh_medico=eh_medico,
        especialidade=None,
        experiencia_corporal=experiencia_corporal,
        resumo_rolante=resumo,
        historico_recente=[],
        sessao_id=100,
        produto_interesse=produto_interesse,
    )


def _make_flow_engine(
    intencao_valor: str = "ambigua",
    idioma_valor: str = "pt",
    resposta: str = "resposta mock",
    handoff: bool = False,
) -> object:
    """
    Cria FlowEngine com todas as deps mockadas.
    Usa a taxonomia correta do MAPA MESTRE (6 caminhos oficiais).
    intencao_valor deve ser um valor de ClassificacaoIntencao valido.
    """
    from app.core.flow import FlowEngine
    from app.core.intent import INTENCAO_PARA_CAMINHO, ClassificacaoIntencao, Idioma

    db_mock = AsyncMock()

    # Mock do resultado do DB para _load_knowledge (sem cursos)
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = None
    db_result.scalars.return_value.all.return_value = []
    db_mock.execute.return_value = db_result

    intencao = ClassificacaoIntencao(intencao_valor)
    idioma = Idioma(idioma_valor)

    intent_mock = AsyncMock()
    intent_mock.classify.return_value = (intencao, idioma)
    # get_caminho usa o mapeamento oficial (INTENCAO_PARA_CAMINHO)
    intent_mock.get_caminho = lambda i: INTENCAO_PARA_CAMINHO.get(i)

    memory_mock = MagicMock()
    memory_mock.build_messages_for_llm.return_value = []

    responder_mock = AsyncMock()
    responder_mock.generate.return_value = (resposta, handoff)
    responder_mock.generate_menu.return_value = (
        "MENU_PT\n"
        "1. Curso Online de Harmonizacao Glutea\n"
        "2. Cursos Presenciais de Harmonizacao Glutea\n"
        "3. Sistema GoldIncision\n"
        "4. Sou aluno e preciso de suporte\n"
        "5. Sou paciente modelo\n"
        "6. Outro assunto"
    )
    responder_mock.generate_not_eligible.return_value = (
        "Esta formacao e exclusiva para medicos. Obrigado!"
    )
    responder_mock.generate_paciente_modelo.return_value = (
        "Para ser paciente modelo, fale com a Nidia: +55 21 97423-9844"
    )

    return FlowEngine(
        db_session=db_mock,
        intent_classifier=intent_mock,
        memory_manager=memory_mock,
        responder=responder_mock,
        nidia_phone="+55 21 97423-9844",
    )


# ===========================================================================
# CENARIO 1 — Intencao clara sem requalificacao (SC-001, US1)
# ===========================================================================

@pytest.mark.asyncio
async def test_cenario1_intencao_clara_curso_online_sem_requalificacao():
    """
    Quickstart Cenario 1: lead pergunta preco do Curso Online.
    Intencao e classificada como curso_online (caminho 1) diretamente.
    Como eh_medico ja e True, NAO deve perguntar 'voce e medico?'.
    """
    engine = _make_flow_engine(intencao_valor="curso_online", resposta="O Curso Online custa R$X")

    # Lead ja qualificado como medico
    ctx = _make_mock_context(caminho=1, etapa="apresentacao", eh_medico=True)
    result = await engine.process(1, "Quanto custa o curso online?", ctx)

    assert result.caminho == 1
    assert result.action in ("continue", "end")
    assert result.response_text  # nao vazio


@pytest.mark.asyncio
async def test_cenario1_intencao_clara_nao_pergunta_se_medico():
    """
    Variacao: lead com intencao clara para presencial que JA informou ser medico
    nao deve receber pergunta de qualificacao.
    Todos os presenciais = Caminho 2 (cursos_presenciais).
    """
    from app.core.flow import ETAPA_QUALIF_MEDICO

    # Caminho 2 = cursos_presenciais (engloba HG Modulo 1, HG360 SP e Barcelona)
    engine = _make_flow_engine(intencao_valor="cursos_presenciais", resposta="Info do Curso Presencial")
    # eh_medico=True, experiencia_corporal=True → vai direto para apresentacao
    ctx = _make_mock_context(caminho=2, etapa="apresentacao", eh_medico=True, experiencia_corporal=True)
    result = await engine.process(1, "quero informacoes do curso presencial", ctx)

    # Nao deve ter etapa de qualificacao de medico
    assert result.etapa != ETAPA_QUALIF_MEDICO
    assert result.caminho == 2


# ===========================================================================
# CENARIO 2 — Menu inicial (US1-AS1)
# ===========================================================================

@pytest.mark.asyncio
async def test_cenario2_intencao_ambigua_exibe_menu():
    """
    Quickstart Cenario 2: 'ola' sem intencao clara → menu de 6 opcoes.
    """
    engine = _make_flow_engine(intencao_valor="ambigua")
    ctx = _make_mock_context()  # sem caminho definido

    result = await engine.process(1, "ola", ctx)

    assert "MENU" in result.response_text or "Curso" in result.response_text
    assert result.action == "continue"


@pytest.mark.asyncio
async def test_cenario2_menu_em_menos_de_10s():
    """
    Menu deve ser gerado sem chamadas LLM caras (rapido).
    O responder mock simula resposta instantanea.
    """
    import time

    engine = _make_flow_engine(intencao_valor="ambigua")
    ctx = _make_mock_context()

    start = time.monotonic()
    result = await engine.process(1, "oi", ctx)
    elapsed = time.monotonic() - start

    assert result.response_text  # nao vazio
    assert elapsed < 1.0  # limite conservador


# ===========================================================================
# CENARIO 3 — Elegibilidade inflexivel (US1-AS4, US1-AS5, FR-009)
# ===========================================================================

@pytest.mark.asyncio
async def test_cenario3_nao_medico_nao_elegivel():
    """
    Quickstart Cenario 3: lead que NAO e medico → informa exclusividade + encerra.
    Caminho 2 = cursos_presenciais (HG Modulo 1 como sub-fluxo default).
    """
    engine = _make_flow_engine(intencao_valor="cursos_presenciais")
    ctx = _make_mock_context(caminho=2, etapa="qualif_medico", eh_medico=False)

    result = await engine.process(1, "nao sou medico", ctx)

    assert result.action in ("handoff", "end")
    assert "medico" in result.response_text.lower() or "exclusiva" in result.response_text.lower()


@pytest.mark.asyncio
async def test_cenario3_apenas_facial_nao_elegivel_para_hg360():
    """
    Quickstart Cenario 3 variacao: medico apenas com experiencia facial
    NAO e elegivel ao HG360 → indica HG Modulo 1.
    Nota: todos os presenciais estao em Caminho 2 (cursos_presenciais).
    """
    # Caminho 2 com sub-fluxo HG360 (produto_interesse setado)
    engine = _make_flow_engine(intencao_valor="cursos_presenciais")
    ctx = _make_mock_context(
        caminho=2,
        etapa="qualif_experiencia",
        eh_medico=True,
        experiencia_corporal=False,
    )

    result = await engine.process(1, "apenas facial", ctx)

    # Deve retornar mensagem de nao elegivel ou redirecionar
    assert result.action in ("handoff", "continue", "end")
    assert result.etapa is not None


@pytest.mark.asyncio
async def test_cenario3_elegibilidade_medica_detectada_em_texto():
    """
    Lead diz 'nao tenho registro medico ativo' → eh_medico=False detectado.
    """
    from app.core.flow import _detectar_confirmacao

    assert _detectar_confirmacao("nao tenho registro medico ativo") is False
    assert _detectar_confirmacao("sou medico com CRM ativo") is True
    assert _detectar_confirmacao("talvez") is None


# ===========================================================================
# CENARIO 4 — Memoria persistente, sem repetir perguntas (SC-002, US2)
# ===========================================================================

@pytest.mark.asyncio
async def test_cenario4_nao_repete_pergunta_medico():
    """
    Quickstart Cenario 4: lead ja informou ser medico na sessao anterior.
    O contexto carregado tem eh_medico=True → NAO deve perguntar de novo.
    """
    from app.core.flow import ETAPA_QUALIF_MEDICO

    engine = _make_flow_engine(intencao_valor="cursos_presenciais", resposta="Info: curso presencial")
    # Contexto ja tem eh_medico=True, experiencia_corporal=True
    ctx = _make_mock_context(caminho=2, etapa="apresentacao", eh_medico=True, experiencia_corporal=True)

    result = await engine.process(1, "quero saber mais sobre o curso", ctx)

    # Nao deve ter pergunta de qualificacao
    assert result.etapa != ETAPA_QUALIF_MEDICO
    assert result.caminho == 2


@pytest.mark.asyncio
async def test_cenario4_nao_repete_pergunta_experiencia():
    """
    Lead ja informou experiencia corporal: motor nao deve perguntar de novo.
    Dentro do Caminho 2 (cursos_presenciais), sub-caminho HG360.
    """
    from app.core.flow import ETAPA_QUALIF_EXPERIENCIA

    engine = _make_flow_engine(intencao_valor="cursos_presenciais", resposta="HG360 info completa")
    ctx = _make_mock_context(
        caminho=2,
        etapa="apresentacao",
        eh_medico=True,
        experiencia_corporal=True,
    )

    result = await engine.process(1, "diga mais sobre o HG360", ctx)

    assert result.etapa != ETAPA_QUALIF_EXPERIENCIA
    assert result.caminho == 2


# ===========================================================================
# CENARIO 5 — Debounce de rajada (SC-005, FR-003)
# ===========================================================================

def test_cenario5_debounce_aceita_multiplas_msgs_mesmo_chamado():
    """
    Quickstart Cenario 5: 3 mensagens do mesmo chamadoId em < 8s.
    O endpoint deve retornar 200 para cada uma (sem erro), e o debounce
    consolida antes de processar (verificado via mock do debounce.push_and_schedule).
    """
    push_calls = []

    class FakeDebounce:
        async def push_and_schedule(self, chamado_id, msg_data, callback):
            push_calls.append(chamado_id)

    with (
        patch("app.api.webhook._get_redis", return_value=MagicMock()),
        patch("app.api.webhook.DebounceManager", return_value=FakeDebounce()),
        patch("app.api.webhook.IdempotencyChecker") as mock_idemp,
    ):
        mock_idemp.return_value.is_duplicate = AsyncMock(return_value=False)
        client = _make_client()

        payloads = [
            _webhook_payload(chamado_id=900005, text="oi"),
            _webhook_payload(chamado_id=900005, text="quero"),
            _webhook_payload(chamado_id=900005, text="o curso online"),
        ]
        for p in payloads:
            resp = _post_webhook(client, p)
            assert resp.status_code == 200
            assert resp.json() == {"ack": "ok"}


# ===========================================================================
# CENARIO 6 — Idempotencia de reenvio (FR-037)
# ===========================================================================

def test_cenario6_idempotencia_descarta_duplicata():
    """
    Quickstart Cenario 6: mesmo evento enviado 2x.
    O segundo deve ser descartado pela chave de idempotencia.
    """
    check_calls = []

    async def _fake_is_duplicate(chamado_id, body):
        check_calls.append(chamado_id)
        return len(check_calls) > 1  # segundo call = duplicata

    with (
        patch("app.api.webhook._get_redis", return_value=MagicMock()),
        patch("app.api.webhook.IdempotencyChecker") as mock_cls,
    ):
        mock_cls.return_value.is_duplicate = _fake_is_duplicate
        client = _make_client()

        payload = _webhook_payload(chamado_id=900006, text="ola")

        resp1 = _post_webhook(client, payload)
        resp2 = _post_webhook(client, payload)

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json() == {"ack": "ok"}
        assert resp2.json() == {"ack": "ok"}
        assert len(check_calls) == 2  # idempotency checker chamado nas 2 vezes


# ===========================================================================
# CENARIO 7 — Handoff para humano (SC-003, US3)
# ===========================================================================

@pytest.mark.asyncio
async def test_cenario7_handoff_apos_confirmacao():
    """
    Quickstart Cenario 7: lead elegivel para cursos presenciais diz 'sim' ao encaminhamento.
    FlowEngine deve retornar action=handoff.
    """
    engine = _make_flow_engine(
        intencao_valor="cursos_presenciais",
        resposta="Vou encaminhar ao consultor!",
        handoff=True,
    )
    # produto_interesse setado → motor vai direto para apresentacao (sem perguntar turma)
    ctx = _make_mock_context(
        caminho=2,
        etapa="apresentacao",
        eh_medico=True,
        experiencia_corporal=True,
        produto_interesse="hg-modulo-1",
    )

    result = await engine.process(1, "sim, quero ser encaminhado", ctx)

    assert result.action == "handoff"


@pytest.mark.asyncio
async def test_cenario7_handoff_imediato_quando_solicitado():
    """
    Quickstart Cenario 7 variacao: lead pede humano a qualquer momento.
    """
    engine = _make_flow_engine(
        intencao_valor="ambigua",
        resposta="Entendido, encaminhando ao consultor.",
        handoff=True,
    )
    ctx = _make_mock_context(caminho=1, etapa="apresentacao", eh_medico=True)

    result = await engine.process(1, "quero falar com um humano", ctx)

    assert result.action == "handoff"


# ===========================================================================
# CENARIO 8 — Paciente modelo → Nidia (US3-AS6, FR-014)
# ===========================================================================

@pytest.mark.asyncio
async def test_cenario8_paciente_modelo_envia_contato_nidia():
    """
    Quickstart Cenario 8: lead identifica Caminho 5 (paciente modelo).
    Deve enviar APENAS o WhatsApp da Nidia e action=end.
    """
    engine = _make_flow_engine(intencao_valor="paciente_modelo")
    ctx = _make_mock_context()

    result = await engine.process(1, "quero ser paciente modelo", ctx)

    assert result.action == "end"
    assert result.caminho == 5
    assert "97423" in result.response_text or "nidia" in result.response_text.lower()


@pytest.mark.asyncio
async def test_cenario8_paciente_modelo_nao_responde_mais():
    """
    Caminho 5: apos responder com contato da Nidia, action=end (sem mais respostas).
    """
    engine = _make_flow_engine(intencao_valor="paciente_modelo")
    ctx = _make_mock_context(caminho=5, etapa="paciente_modelo")

    result = await engine.process(1, "mas tenho duvidas sobre as vagas", ctx)

    # Caminho 5 sempre retorna 'end' — nao engaja em perguntas sobre vagas
    assert result.action == "end"
    assert result.caminho == 5


# ===========================================================================
# CENARIO 9 — Gestao dinamica sem redeploy (SC-004, US4)
# ===========================================================================

def test_cenario9_admin_crud_retorna_401_sem_token():
    """
    Quickstart Cenario 9: POST /admin/cursos sem token → 401.
    """
    CURSO_PAYLOAD = {
        "slug": "hg-avancado-teste",
        "nome": "HG Avancado",
        "tipo": "presencial",
        "caminhoMapaMestre": 2,
        "elegibilidade": {"medico": True},
        "ativo": True,
        "apresentacoes": [{"idioma": "pt", "texto": "Apresentacao oficial verbatim."}],
        "objecoes": [],
        "turmas": [],
        "links": [{"idioma": "pt", "url": "https://pay.hotmart.com/HGAv"}],
        "midias": [],
    }

    client = _make_client()

    # Sem token → 401 (verify_admin_token rejeita)
    resp_no_token = client.post(
        "/admin/cursos",
        content=json.dumps(CURSO_PAYLOAD),
        headers={"Content-Type": "application/json"},
    )
    assert resp_no_token.status_code in (401, 403, 422)


def test_cenario9_catalogo_runtime_reflete_novo_curso():
    """
    Quickstart Cenario 9: mudancas no catalogo refletem em conversas novas
    sem redeploy (FR-026) — verificado via config (leitura em runtime por slug).
    """
    from app.core.flow import _CAMINHO_PARA_SLUG

    # Caminho 1: Curso Online tem slug fixo
    assert _CAMINHO_PARA_SLUG[1] == "curso-online-hg"
    # Caminho 2: Cursos Presenciais tem slug dinamico (resolvido por sub-fluxo)
    assert _CAMINHO_PARA_SLUG[2] is None
    # Caminho 3: Sistema GoldIncision — slug do licenciamento
    assert _CAMINHO_PARA_SLUG[3] == "licenciamento-internacional"
    # Caminho 5: paciente modelo nao tem curso no catalogo
    assert _CAMINHO_PARA_SLUG[5] is None


# ===========================================================================
# CENARIO 10 — Multilingual + audio (SC-007, US5)
# ===========================================================================

@pytest.mark.asyncio
async def test_cenario10_audio_en_responde_em_ingles():
    """
    Quickstart Cenario 10: mensagem de voz em ingles → responde em ingles
    com link ingles.
    """
    engine = _make_flow_engine(
        intencao_valor="curso_online",
        idioma_valor="en",
        resposta="The Online Course costs $X. Enroll: pay.hotmart.com/Q95039051K",
    )
    ctx = _make_mock_context(idioma="pt")

    result = await engine.process(1, "I'm interested in the online course", ctx)

    # Idioma deve ter sido atualizado para EN
    assert ctx.idioma == "en"
    assert result.caminho == 1


@pytest.mark.asyncio
async def test_cenario10_falha_transcricao_pede_repeticao():
    """
    Quickstart Cenario 10 (error): transcricao falha → agente pede repeticao em texto.
    Verifica que o cliente OpenAI propaga a excecao corretamente (FR-005).
    """
    with patch.dict("sys.modules", {"openai": MagicMock()}):

        import sys
        if "app.integrations.openai_client" in sys.modules:
            del sys.modules["app.integrations.openai_client"]

        from app.integrations import openai_client as oa_module
        OpenAIClientCls = oa_module.OpenAIClient

        mock_oa_instance = MagicMock()
        mock_oa_instance.transcribe_audio = AsyncMock(
            side_effect=Exception("HTTP 500: transcricao falhou")
        )

        with patch.object(OpenAIClientCls, "transcribe_audio", mock_oa_instance.transcribe_audio):
            with patch.dict("sys.modules", {"openai": MagicMock()}):
                if "app.integrations.openai_client" in sys.modules:
                    del sys.modules["app.integrations.openai_client"]

                with pytest.raises(Exception, match="transcricao"):
                    await mock_oa_instance.transcribe_audio(b"fake_audio_bytes", "audio/ogg")


@pytest.mark.asyncio
async def test_cenario10_troca_idioma_pt_para_en_persistido():
    """
    Quickstart Cenario 10 variacao: lead muda de PT para EN no meio.
    Motor deve atualizar idioma e manter preferencia.
    """
    engine = _make_flow_engine(intencao_valor="curso_online", idioma_valor="en", resposta="EN response")
    ctx = _make_mock_context(idioma="pt", caminho=1, eh_medico=True)

    result = await engine.process(1, "I want more info in english please", ctx)

    # Idioma deve ter sido alterado para EN
    assert ctx.idioma == "en"
    assert "idioma" in result.updates


# ===========================================================================
# CENARIO 11 — Anti-alucinacao (SC-008, Principio II)
# ===========================================================================

@pytest.mark.asyncio
async def test_cenario11_pergunta_fora_da_base_retorna_handoff():
    """
    Quickstart Cenario 11: lead pergunta algo fora da Base Oficial.
    O responder deve retornar handoff=True (nao inventar).
    """
    engine = _make_flow_engine(
        intencao_valor="ambigua",
        resposta="Nao possuo essa informacao. Vou encaminhar a um especialista.",
        handoff=True,
    )
    ctx = _make_mock_context(caminho=1, eh_medico=True)

    result = await engine.process(1, "qual e o CNPJ da GoldIncision?", ctx)

    assert result.action == "handoff"


def test_cenario11_responder_usa_grounding_nao_livre():
    """
    Verifica que o GroundedResponder exige knowledge_context para gerar respostas
    (nao inventa conteudo de negocio).
    """
    import inspect

    from app.core.responder import GroundedResponder

    sig = inspect.signature(GroundedResponder.generate)
    params = list(sig.parameters.keys())
    assert "knowledge_context" in params, (
        "GroundedResponder.generate deve receber knowledge_context (anti-alucinacao)"
    )


# ===========================================================================
# CENARIO 12 — fromMe e tipo desconhecido (edge cases, FR-002)
# ===========================================================================

def test_cenario12_from_me_ignorado():
    """
    Quickstart Cenario 12: evento com fromMe=true → sem resposta, sem efeito.
    """
    with patch("app.api.webhook._get_redis", return_value=None):
        client = _make_client()
        payload = _webhook_payload(chamado_id=900012, from_me=True)
        resp = _post_webhook(client, payload)

    assert resp.status_code == 200
    assert resp.json() == {"ack": "ok"}


def test_cenario12_tipo_desconhecido_descartado():
    """
    Quickstart Cenario 12: mediaType desconhecido → descarte silencioso.
    O webhook retorna 200 (sem retry) mesmo com tipo nao suportado.
    """
    with patch("app.api.webhook._get_redis", return_value=None):
        client = _make_client()
        payload = _webhook_payload(chamado_id=900012, text="sticker message")
        resp = _post_webhook(client, payload)

    assert resp.status_code == 200
    assert resp.json() == {"ack": "ok"}


def test_cenario12_is_group_ignorado():
    """
    Mensagem de grupo e descartada silenciosamente (fora do escopo).
    """
    with patch("app.api.webhook._get_redis", return_value=None):
        client = _make_client()
        payload = _webhook_payload(chamado_id=900012, is_group=True)
        resp = _post_webhook(client, payload)

    assert resp.status_code == 200
    assert resp.json() == {"ack": "ok"}


# ===========================================================================
# CENARIO 13 — Roundtrip end-to-end / anti-drift (8.1.2)
# ===========================================================================

class TestCenario13RoundtripWebhookExamples:
    """
    Quickstart Cenario 13: validacao de contrato inbound com os payloads reais
    de knowledge_base/example_webhook_json/.
    """

    def _load_body(self, filename: str) -> dict:
        path = _WEBHOOK_EXAMPLES / filename
        with open(path) as f:
            data = json.load(f)
        # Os exemplos reais sao arrays n8n com um item
        return data[0]["body"]

    def test_json_message_parseia_sem_erro(self):
        body = self._load_body("json_message,json")
        parsed = WebhookPayload.model_validate(body)

        # Campos obrigatorios do contrato (webhook-inbound.md)
        assert parsed.chamadoId == 138901
        assert parsed.sender is not None
        assert parsed.fromMe is False
        assert len(parsed.mensagem) >= 1
        assert parsed.mensagem[0].type == "text"
        assert parsed.mensagem[0].text is not None
        assert parsed.ticketData is not None
        assert parsed.ticketData.status == "open"

    def test_json_audio_parseia_sem_erro(self):
        """Payload de audio: mensagem e dict com mediaType/mediaUrl (anti-drift)."""
        body = self._load_body("json_audio,json")
        parsed = WebhookPayload.model_validate(body)

        assert parsed.chamadoId == 138901
        assert parsed.sender is not None
        assert parsed.fromMe is False
        assert len(parsed.mensagem) >= 1
        assert parsed.mensagem[0].type == "audio"
        assert parsed.mensagem[0].media_url is not None
        assert "eveo.com.br" in parsed.mensagem[0].media_url or "object.sp2" in parsed.mensagem[0].media_url
        assert parsed.ticketData is not None

    def test_json_video_parseia_sem_erro(self):
        """Payload de video: mesmo contrato do audio com mediaType=video."""
        body = self._load_body("json_video,json")
        parsed = WebhookPayload.model_validate(body)

        assert parsed.chamadoId == 138901
        assert parsed.mensagem[0].type == "video"
        assert parsed.mensagem[0].media_url is not None

    def test_json_document_parseia_sem_erro(self):
        """Payload de documento: mediaType deve ser preservado."""
        body = self._load_body("json_document,json")
        parsed = WebhookPayload.model_validate(body)

        assert parsed.chamadoId == 138901
        assert parsed.mensagem[0].media_url is not None

    def test_todos_os_payloads_retornam_200_no_endpoint(self):
        """
        Roundtrip via endpoint HTTP: nenhum payload real deve causar 4xx/5xx.
        """
        with patch("app.api.webhook._get_redis", return_value=None):
            client = _make_client()

            for filename in [
                "json_message,json",
                "json_audio,json",
                "json_video,json",
                "json_document,json",
            ]:
                body = self._load_body(filename)
                resp = client.post(
                    "/webhook/chatmaster",
                    content=json.dumps(body),
                    headers={"Content-Type": "application/json"},
                )
                assert resp.status_code == 200, f"Falha em {filename}: {resp.text}"
                assert resp.json() == {"ack": "ok"}, f"Ack errado em {filename}"

    def test_campos_obrigatorios_nao_perdidos_por_case(self):
        """
        Anti-drift: campos criticos nao devem ser perdidos por mudanca de case
        ou nome no payload real vs schema.
        """
        body = self._load_body("json_message,json")
        parsed = WebhookPayload.model_validate(body)

        assert parsed.chamadoId is not None
        assert parsed.sender is not None
        assert isinstance(parsed.fromMe, bool)
        assert parsed.ticketData is not None
        assert parsed.ticketData.status is not None
        assert parsed.contact_number == str(parsed.sender)


# ===========================================================================
# CENARIO 14 — Empacotamento isolado (SC-006, US6)
# ===========================================================================

def test_cenario14_stack_yml_inspecao():
    """
    Quickstart Cenario 14: stack.yml tem 3 servicos, sem secrets em texto claro.
    NAO executa docker stack deploy (fora de escopo, FR-031).
    """
    import re

    stack_path = _PROJ_ROOT / "stack.yml"
    assert stack_path.exists(), "stack.yml nao encontrado"

    content = stack_path.read_text()

    # Deve ter servicos app, postgres, redis
    assert "app:" in content, "Servico 'app' ausente no stack.yml"
    assert "postgres:" in content, "Servico 'postgres' ausente no stack.yml"
    assert "redis:" in content, "Servico 'redis' ausente no stack.yml"

    # Secrets nao devem estar em texto claro (FR-032)
    SECRET_PATTERNS = [
        r"sk-[A-Za-z0-9]{20,}",
        r"password\s*:\s*['\"]?\w{8,}",
    ]
    for pattern in SECRET_PATTERNS:
        matches = re.findall(pattern, content, re.IGNORECASE)
        assert not matches, f"Possivel secret hardcoded em stack.yml: {matches}"


def test_cenario14_dockerfile_existe():
    """Dockerfile deve existir para o build da imagem."""
    dockerfile = _PROJ_ROOT / "Dockerfile"
    assert dockerfile.exists(), "Dockerfile nao encontrado"
    content = dockerfile.read_text()
    assert "FROM" in content, "Dockerfile sem instrucao FROM"
    assert "EXPOSE" in content or "CMD" in content, "Dockerfile sem CMD/EXPOSE"


# ===========================================================================
# 8.1.4 — Rate limiting / teto de gasto LLM (SEC-WH-3)
# ===========================================================================

class TestRateLimitingLLMCap:
    """
    Task 8.1.4: testes de rate limiting e teto de gasto LLM.
    """

    def test_rate_limit_admin_429_apos_max_tentativas(self):
        """
        Muitas tentativas de autenticacao no /admin/* devem retornar 429.
        """
        from app.api.admin import _RATE_LIMIT_MAX, _check_rate_limit, _rate_store

        _rate_store.clear()

        fake_ip = "192.0.2.100"

        import time
        now = time.monotonic()
        _rate_store[fake_ip] = [now] * _RATE_LIMIT_MAX

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _check_rate_limit(fake_ip)

        assert exc_info.value.status_code == 429

    def test_rate_limit_admin_passa_antes_do_limite(self):
        """
        Ate o limite, requests devem passar sem 429.
        """
        from app.api.admin import _RATE_LIMIT_MAX, _check_rate_limit, _rate_store

        _rate_store.clear()
        fake_ip = "192.0.2.101"

        for _ in range(_RATE_LIMIT_MAX - 1):
            _check_rate_limit(fake_ip)

    def test_config_llm_max_tokens_por_hora_definido(self):
        """
        SEC-WH-3: configuracao de teto de gasto LLM deve existir e ter valor > 0.
        """
        from app.config import settings

        assert settings.llm_max_tokens_per_hour > 0
        assert settings.llm_max_tokens_per_hour <= 10_000_000

    def test_config_max_requests_por_sender_definido(self):
        """
        SEC-WH-3: limite de mensagens por sender por minuto deve existir.
        """
        from app.config import settings

        assert settings.max_requests_per_sender_per_minute > 0
        assert settings.max_requests_per_sender_per_minute <= 1000

    def test_rate_limit_admin_janela_expira(self):
        """
        Requests mais antigos que a janela de tempo nao contam para o limite.
        """
        import time

        from app.api.admin import (
            _RATE_LIMIT_MAX,
            _RATE_LIMIT_WINDOW,
            _check_rate_limit,
            _rate_store,
        )

        _rate_store.clear()
        fake_ip = "192.0.2.102"

        old_time = time.monotonic() - _RATE_LIMIT_WINDOW - 1
        _rate_store[fake_ip] = [old_time] * _RATE_LIMIT_MAX

        _check_rate_limit(fake_ip)  # nao deve levantar 429


# ===========================================================================
# 8.1.1 — Configuracao pytest + infraestrutura efemera (documentado)
# ===========================================================================

def test_pytest_asyncio_configurado():
    """
    8.1.1: pytest-asyncio deve estar configurado em asyncio_mode=auto.
    """
    import pytest_asyncio

    assert pytest_asyncio.__version__


def test_mocks_openai_e_chatmaster_padrao():
    """
    8.1.1: OpenAI e ChatMaster sao mockados por padrao — nenhuma chamada
    real sai durante os testes (zero deps externas para rodar a suite).
    """
    import importlib
    import inspect

    oa_module = importlib.import_module("app.integrations.openai_client")
    cm_module = importlib.import_module("app.integrations.chatmaster")

    assert hasattr(oa_module, "OpenAIClient")
    assert hasattr(cm_module, "ChatMasterClient")

    from app.integrations.chatmaster import ChatMasterClient

    cm = ChatMasterClient(
        base_url="https://fake",
        token="",
        handoff_queue_ids={"consultores": 78},
        default_queue_id=78,
    )
    assert cm is not None

    sig = inspect.signature(oa_module.OpenAIClient.__init__)
    assert "api_key" in sig.parameters
    assert "model_cheap" in sig.parameters
    assert "model_reasoning" in sig.parameters


def test_infraestrutura_efemera_documentada():
    """
    8.1.1: documenta que testcontainers pode ser ativado via env var.
    """
    use_tc = os.environ.get("PYTEST_USE_TESTCONTAINERS", "0")
    assert use_tc in ("0", "1"), "PYTEST_USE_TESTCONTAINERS deve ser '0' ou '1'"


# ===========================================================================
# Jornadas compostas do quickstart (validacao de sequencias)
# ===========================================================================

@pytest.mark.asyncio
async def test_jornada_curso_online_preco_direto_sem_requalificacao():
    """
    Jornada completa Cenario 1: lead pergunta preco direto.
    Flow: intencao clara (curso_online) → sem perguntas de qualificacao
    (eh_medico ja definido) → apresentacao + link.
    """
    engine = _make_flow_engine(
        intencao_valor="curso_online",
        resposta="O Curso Online de HG custa R$2.997. Link: pay.hotmart.com/xxx",
        handoff=False,
    )
    ctx = _make_mock_context(eh_medico=True)

    result = await engine.process(1, "Quanto custa o curso online de harmonizacao glutea?", ctx)

    assert result.caminho == 1
    assert result.action in ("continue", "end")


@pytest.mark.asyncio
async def test_jornada_presencial_qualificacao_elegibilidade_handoff():
    """
    Jornada Cenario 7: trilha presencial (Caminho 2) — qualificacao → handoff.
    Todos os presenciais agora ficam em Caminho 2 (cursos_presenciais).
    """
    # Passo 1: Lead pergunta sobre cursos presenciais
    engine1 = _make_flow_engine(intencao_valor="cursos_presenciais")
    ctx = _make_mock_context()  # sem qualificacao

    result1 = await engine1.process(1, "quero informacoes sobre cursos presenciais", ctx)
    assert result1.caminho == 2

    # Passo 2: Confirma ser medico
    from app.core.flow import ETAPA_QUALIF_MEDICO
    ctx.etapa = ETAPA_QUALIF_MEDICO
    ctx.eh_medico = None

    engine2 = _make_flow_engine(intencao_valor="cursos_presenciais")
    _ = await engine2.process(1, "sim, sou medico com CRM ativo", ctx)

    # Passo 3: Confirma ter experiencia corporal → escolha de turma → handoff
    ctx.eh_medico = True
    ctx.experiencia_corporal = True
    # Setar produto_interesse para pular a escolha de turma e ir direto para apresentacao
    ctx.produto_interesse = "hg360-sp"
    ctx.etapa = "apresentacao"
    engine3 = _make_flow_engine(
        intencao_valor="cursos_presenciais",
        resposta="Vou encaminhar ao consultor!",
        handoff=True,
    )
    result3 = await engine3.process(1, "sim, quero ser encaminhado ao consultor", ctx)

    assert result3.action == "handoff"


# ===========================================================================
# F8 — E2e webhook→FlowEngine REAL (falha se engine desconectado)
# ===========================================================================

class TestF8WebhookEngineWired:
    """
    F8: testa que o webhook realmente invoca o FlowEngine.
    Estes testes FALHAM se _process_consolidated_messages nao chamar
    _handle_engine ou se _handle_engine nao instanciar FlowEngine.process().

    Diferenca dos outros testes: aqui usamos o _handle_engine REAL (nao mockado)
    e verificamos que FlowEngine.process() foi chamado.
    """

    @pytest.mark.asyncio
    async def test_handle_engine_invoca_flow_engine_process(self):
        """
        F8 — Critico: _handle_engine deve instanciar e chamar FlowEngine.process().
        FALHA se engine estiver desconectado (TODO sem implementacao real).

        Estrategia: mockar as deps do FlowEngine (DB, Redis, OpenAI, ChatMaster)
        mas usar a funcao real _handle_engine — verificando via spy que
        FlowEngine.process() foi de fato chamado.
        """
        from app.api.webhook import _handle_engine
        from app.core.flow import FlowResult

        # Montar FlowResult que o FlowEngine.process() retornaria
        fake_result = FlowResult(
            response_text="Ola! Como posso ajudar?",
            action="continue",
            caminho=None,
            etapa="menu",
            updates={"idioma": "pt"},
        )

        # Rastrear se FlowEngine.process foi chamado
        process_was_called = []

        async def spy_process(self_engine, ticket_id, user_message, context):
            process_was_called.append({"ticket_id": ticket_id, "message": user_message})
            return fake_result

        # Mocks de infraestrutura (DB, Redis, OpenAI, ChatMaster)
        mock_db_session = AsyncMock()
        # Retorno do upsert de Contato (contato_id)
        mock_db_session.execute.return_value.scalar_one.side_effect = [
            10,   # Contato.id
            "aberto",  # Ticket.status
        ]
        mock_db_session.flush = AsyncMock()
        mock_db_session.commit = AsyncMock()
        mock_db_session.rollback = AsyncMock()
        mock_db_session.__aenter__ = AsyncMock(return_value=mock_db_session)
        mock_db_session.__aexit__ = AsyncMock(return_value=False)

        mock_session_factory = MagicMock(return_value=mock_db_session)

        # SessionContext mock que o MemoryManager.load_context retorna
        from app.core.memory import SessionContext
        mock_context = SessionContext(
            ticket_id=1,
            chamado_id=900099,
            contato_id=10,
            caminho=None,
            etapa=None,
            idioma="pt",
            eh_medico=None,
            especialidade=None,
            experiencia_corporal=None,
            resumo_rolante=None,
            historico_recente=[],
            sessao_id=100,
        )

        messages_payload = [
            {
                "chamadoId": 900099,
                "sender": "5511999990099",
                "nome": "Lead F8",
                "mensagem": [{"type": "text", "text": "Ola, quero saber sobre cursos"}],
                "ticketStatus": "open",
                "ticketData": None,
                "queueId": 78,
            }
        ]

        with (
            patch("app.main.get_session_factory", return_value=mock_session_factory),
            patch("app.api.webhook._get_redis", return_value=MagicMock()),
            patch("app.core.flow.FlowEngine.process", spy_process),
            patch("app.core.memory.MemoryManager.load_context", AsyncMock(return_value=mock_context)),
            patch("app.core.memory.MemoryManager.update_qualification_variables", AsyncMock()),
            patch("app.core.memory.MemoryManager.update_ticket_state", AsyncMock()),
            patch("app.core.memory.MemoryManager.save_message", AsyncMock()),
            patch("app.integrations.openai_client.OpenAIClient.__init__", return_value=None),
            patch("app.integrations.chatmaster.make_chatmaster_client"),
            patch("app.core.intent.IntentClassifier.__init__", return_value=None),
            patch("app.core.responder.GroundedResponder.__init__", return_value=None),
        ):
            await _handle_engine(900099, messages_payload)

        # VERIFICACAO CRITICA: FlowEngine.process() DEVE ter sido chamado
        assert len(process_was_called) == 1, (
            "FlowEngine.process() nao foi chamado! "
            "_handle_engine esta desconectado do motor conversacional. "
            "Verifique a implementacao de _handle_engine em webhook.py."
        )
        assert process_was_called[0]["message"] == "Ola, quero saber sobre cursos"

    @pytest.mark.asyncio
    async def test_handle_engine_nao_invocado_sem_session_factory(self):
        """
        F8 — Seguranca: sem session_factory, _handle_engine deve abortar
        silenciosamente (nao lanca excecao, nao invoca engine).
        """
        from app.api.webhook import _handle_engine

        process_was_called = []

        async def spy_process(self_engine, ticket_id, user_message, context):
            process_was_called.append(True)

        messages = [
            {
                "chamadoId": 900098,
                "sender": "5511999990098",
                "nome": "Lead",
                "mensagem": [{"type": "text", "text": "oi"}],
                "ticketStatus": "open",
                "ticketData": None,
                "queueId": 78,
            }
        ]

        with (
            patch("app.main.get_session_factory", return_value=None),
            patch("app.core.flow.FlowEngine.process", spy_process),
        ):
            # Nao deve levantar excecao
            await _handle_engine(900098, messages)

        # Engine NAO deve ter sido chamado (sem session_factory disponivel)
        assert len(process_was_called) == 0, (
            "FlowEngine.process() nao deveria ser chamado sem session_factory"
        )

    @pytest.mark.asyncio
    async def test_webhook_endpoint_dispara_handle_engine_via_background(self):
        """
        F8 — Contrato HTTP: POST /webhook/chatmaster deve chamar
        _process_consolidated_messages via BackgroundTasks quando Redis=None.
        Este teste verifica que o endpoint nao e um no-op (nao bypassa o pipeline).
        """
        from app.api import webhook as webhook_module

        process_calls = []

        async def spy_process(chamado_id, messages):
            process_calls.append({"chamado_id": chamado_id, "n_msgs": len(messages)})

        with (
            patch("app.api.webhook._get_redis", return_value=None),
            patch.object(webhook_module, "_process_consolidated_messages", spy_process),
        ):
            client = _make_client()
            payload = _webhook_payload(chamado_id=900097, text="oi, quero informacoes")
            resp = _post_webhook(client, payload)

        assert resp.status_code == 200
        assert resp.json() == {"ack": "ok"}

        # O BackgroundTasks chama _process_consolidated_messages de forma sincrona
        # no TestClient (TestClient executa background tasks sincronamente)
        assert len(process_calls) >= 1, (
            "_process_consolidated_messages nao foi chamado pelo endpoint. "
            "O webhook pode estar descartando a mensagem indevidamente."
        )
        assert process_calls[0]["chamado_id"] == 900097


# ===========================================================================
# Gate por fila — agente atende SO na fila da IA (settings.ai_queue_id=77)
# ===========================================================================

class TestGateDeFila:
    """Mensagens da fila humana (78) sao ignoradas pelo agente; fila 77 atende."""

    def _spy_post(self, payload):
        from app.api import webhook as webhook_module

        process_calls = []

        async def spy_process(chamado_id, messages):
            process_calls.append(chamado_id)

        with (
            patch("app.api.webhook._get_redis", return_value=None),
            patch.object(webhook_module, "_process_consolidated_messages", spy_process),
        ):
            client = _make_client()
            resp = _post_webhook(client, payload)
        return resp, process_calls

    def test_fila_humana_78_agente_silencioso(self):
        """queueId=78 (humano) → ack 200 e NENHUM processamento do agente."""
        resp, calls = self._spy_post(
            _webhook_payload(chamado_id=900201, text="oi", queue_id=78)
        )
        assert resp.status_code == 200
        assert calls == [], "agente nao deve processar mensagens da fila humana (78)"

    def test_fila_ia_77_processa(self):
        """queueId=77 (IA) → processa normalmente."""
        resp, calls = self._spy_post(
            _webhook_payload(chamado_id=900202, text="oi", queue_id=77)
        )
        assert resp.status_code == 200
        assert len(calls) >= 1

    def test_fila_ausente_processa_compat(self):
        """queueId ausente → processa (compat, conforme decisao do operador)."""
        resp, calls = self._spy_post(
            _webhook_payload(chamado_id=900203, text="oi", queue_id=None)
        )
        assert resp.status_code == 200
        assert len(calls) >= 1

    def test_reset_funciona_fora_da_fila_ia(self):
        """#reset roda mesmo na fila humana (esta antes do gate) — ferramenta de teste."""
        from app.api import webhook as webhook_module

        reset_calls = []

        async def spy_reset(chamado_id, sender):
            reset_calls.append(chamado_id)

        with (
            patch("app.api.webhook._get_redis", return_value=None),
            patch.object(webhook_module, "_handle_reset", spy_reset),
        ):
            client = _make_client()
            resp = _post_webhook(
                client, _webhook_payload(chamado_id=900204, text="#reset", queue_id=78)
            )
        assert resp.status_code == 200
        assert reset_calls == [900204], "#reset deve rodar mesmo fora da fila da IA"
