"""
Testes de timeout de inatividade e reengajamento (US2, FASE 5).

Cobertura:
- 5.1.1/5.1.2/5.1.3: `_bump_ultima_interacao` (webhook.py) grava a marca
  atual e calcula o delta em horas desde a anterior; fail-open em erro de
  Redis, timestamp corrompido ou ausencia de marca anterior (1o turno).
- 5.2.1/5.2.2/5.2.3: gap moderado (> REENGAJAMENTO_HORAS, <= EXPIRA_SESSAO
  _HORAS) -> retomada cordial prefixada, etapa/caminho INTACTOS (sem
  perder contexto de fluxo nem reapresentar o menu do zero).
- 5.2.4: gap abaixo de ambos os limiares -> nenhuma retomada, comportamento
  inalterado.
- 5.3.1/5.3.2/5.3.3/5.3.4: gap > EXPIRA_SESSAO_HORAS -> etapa/caminho
  resetados (sessao tratada como nova), mas TODOS os campos de perfil do
  Contato preservados (nunca re-perguntados).

Abordagem: FakeHashRedis em memoria para o helper de webhook; chamadas
diretas aos metodos `_aplicar_reengajamento_pre/_pos` do FlowEngine para a
logica de decisao (sem I/O real).
"""
from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.core.flow import FlowEngine, FlowResult, _perfil_conhecido
from app.core.memory import SessionContext


class FakeHashRedis:
    """Mesma fake minimalista de test_turnos_contadores.py (hget/hset)."""

    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, bytes]] = {}

    async def hget(self, key: str, field: str) -> Optional[bytes]:
        return self._hashes.get(key, {}).get(field)

    async def hset(self, key: str, field: str, value: Any) -> int:
        h = self._hashes.setdefault(key, {})
        is_new = field not in h
        h[field] = str(value).encode("utf-8")
        return 1 if is_new else 0

    async def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        h = self._hashes.setdefault(key, {})
        current = int(h.get(field, b"0"))
        new_val = current + amount
        h[field] = str(new_val).encode("utf-8")
        return new_val


class BrokenRedis:
    async def hget(self, *a, **kw):
        raise ConnectionError("redis indisponivel")

    async def hset(self, *a, **kw):
        raise ConnectionError("redis indisponivel")


def _engine() -> FlowEngine:
    """FlowEngine com dependencias dummy — os metodos de reengajamento
    testados aqui nao tocam DB/intent/memory/responder."""
    return FlowEngine(
        db_session=None,  # type: ignore[arg-type]
        intent_classifier=None,  # type: ignore[arg-type]
        memory_manager=None,  # type: ignore[arg-type]
        responder=None,  # type: ignore[arg-type]
    )


def _ctx(**overrides) -> SessionContext:
    base = dict(
        ticket_id=1, chamado_id=1, contato_id=1,
        caminho=3, etapa="qualif_medico", idioma="pt",
        eh_medico=True, especialidade="Cirurgia", experiencia_corporal=True,
        produto_interesse="Curso Online HG", nome="Ana",
        perfil={"cidade": "SP"}, etapa_funil='{"et": "qualif_medico", "n": 2}',
    )
    base.update(overrides)
    return SessionContext(**base)


# ---------------------------------------------------------------------------
# 5.1 — _bump_ultima_interacao (webhook.py)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bump_ultima_interacao_primeiro_turno_retorna_none():
    """1o turno da sessao (sem marca anterior) -> None (fail-open, task 5.1.2)."""
    from app.api.webhook import _bump_ultima_interacao

    fake = FakeHashRedis()
    with patch("app.api.webhook._get_redis", return_value=fake):
        delta = await _bump_ultima_interacao(chamado_id=1)

    assert delta is None
    # A marca ATUAL deve ter sido gravada mesmo no 1o turno.
    assert fake._hashes["estado:1"]["ultima_interacao"] is not None


@pytest.mark.asyncio
async def test_bump_ultima_interacao_calcula_delta_em_horas():
    """Marca anterior presente -> retorna o gap em horas corretamente."""
    from app.api.webhook import _bump_ultima_interacao

    fake = FakeHashRedis()
    agora = 1_000_000_000.0
    dez_horas_atras = agora - 10 * 3600

    with patch("app.api.webhook.time") as mock_time:
        mock_time.time.return_value = dez_horas_atras
        with patch("app.api.webhook._get_redis", return_value=fake):
            # Grava a marca "antiga" (simula um turno 10h atras).
            await _bump_ultima_interacao(chamado_id=2)

    with patch("app.api.webhook.time") as mock_time:
        mock_time.time.return_value = agora
        with patch("app.api.webhook._get_redis", return_value=fake):
            delta = await _bump_ultima_interacao(chamado_id=2)

    assert delta is not None
    assert 9.9 <= delta <= 10.1


@pytest.mark.asyncio
async def test_bump_ultima_interacao_fail_open_erro_redis():
    from app.api.webhook import _bump_ultima_interacao

    with patch("app.api.webhook._get_redis", return_value=BrokenRedis()):
        delta = await _bump_ultima_interacao(chamado_id=3)

    assert delta is None


@pytest.mark.asyncio
async def test_bump_ultima_interacao_timestamp_corrompido_fail_open():
    """Valor corrompido/nao numerico no hash -> fail-open (None)."""
    from app.api.webhook import _bump_ultima_interacao

    fake = FakeHashRedis()
    fake._hashes["estado:4"] = {"ultima_interacao": b"nao-e-um-epoch"}
    with patch("app.api.webhook._get_redis", return_value=fake):
        delta = await _bump_ultima_interacao(chamado_id=4)

    assert delta is None


# ---------------------------------------------------------------------------
# 5.2 — Retomada cordial (gap moderado)
# ---------------------------------------------------------------------------

def test_reengajamento_pre_gap_moderado_e_retomada_sem_mudar_etapa():
    """AS1/task 5.2.1: gap > REENGAJAMENTO_HORAS e <= EXPIRA_SESSAO_HORAS
    -> estado "retomada", etapa/caminho NAO sao alterados (5.2.2)."""
    engine = _engine()
    gap = (settings.reengajamento_horas + settings.expira_sessao_horas) / 2
    ctx = _ctx(horas_inatividade=gap)

    estado = engine._aplicar_reengajamento_pre(ctx)

    assert estado == "retomada"
    assert ctx.etapa == "qualif_medico"
    assert ctx.caminho == 3
    assert ctx.etapa_funil == '{"et": "qualif_medico", "n": 2}'  # anti-loop intacto


def test_reengajamento_pos_retomada_prefixa_mensagem_sem_perder_texto_original():
    """5.2.3: a resposta normal do turno permanece, com o prefixo cordial
    adicionado ANTES — sem perda de contexto de fluxo."""
    engine = _engine()
    ctx = _ctx()
    result = FlowResult(
        response_text="Qual sua especialidade?", action="continue",
        caminho=3, etapa="qualif_medico",
    )

    out = engine._aplicar_reengajamento_pos(ctx, result, "retomada")

    assert out.turno_acao == "retomada"
    assert "Qual sua especialidade?" in out.response_text
    assert out.response_text.index("Qual sua especialidade?") > 0  # prefixo vem antes
    # A pergunta pendente (etapa/menu) nao foi reapresentada do zero — o
    # texto original do turno continua sendo a UNICA pergunta no resultado.
    assert out.response_text.count("?") == 1


def test_reengajamento_gap_curto_e_normal_sem_mensagem_extra():
    """AS3/task 5.2.4: gap abaixo de ambos os limiares -> nenhuma retomada."""
    engine = _engine()
    ctx = _ctx(horas_inatividade=settings.reengajamento_horas - 1)

    estado = engine._aplicar_reengajamento_pre(ctx)
    assert estado == "normal"

    result = FlowResult(
        response_text="resposta normal", action="continue", caminho=3, etapa="qualif_medico",
    )
    out = engine._aplicar_reengajamento_pos(ctx, result, estado)
    assert out.response_text == "resposta normal"
    assert out.turno_acao is None


def test_reengajamento_horas_inatividade_none_e_normal():
    """1o turno / leitura ausente (None) -> tratado como recente (5.1.2)."""
    engine = _engine()
    ctx = _ctx(horas_inatividade=None)

    estado = engine._aplicar_reengajamento_pre(ctx)
    assert estado == "normal"


# ---------------------------------------------------------------------------
# 5.3 — Expiração de sessão preservando perfil do Contato
# ---------------------------------------------------------------------------

def test_reengajamento_pre_gap_longo_e_sessao_nova_reseta_etapa_caminho():
    """AS2/task 5.3.1: gap > EXPIRA_SESSAO_HORAS -> etapa/caminho resetados
    (tratado como sessao nova / retorna a saudacao inicial)."""
    engine = _engine()
    ctx = _ctx(horas_inatividade=settings.expira_sessao_horas + 1)

    estado = engine._aplicar_reengajamento_pre(ctx)

    assert estado == "sessao_nova"
    assert ctx.etapa is None
    assert ctx.caminho is None
    assert ctx.etapa_funil is None  # anti-loop tambem zerado (nova jornada)


def test_reengajamento_pre_sessao_nova_preserva_100_por_cento_do_perfil():
    """5.3.2/5.3.3/SC-004: elegibilidade medica, idioma, especialidade,
    experiencia e interesse permanecem intactos apos expiracao de sessao."""
    engine = _engine()
    ctx = _ctx(horas_inatividade=settings.expira_sessao_horas + 100)

    engine._aplicar_reengajamento_pre(ctx)

    assert ctx.eh_medico is True
    assert ctx.especialidade == "Cirurgia"
    assert ctx.experiencia_corporal is True
    assert ctx.produto_interesse == "Curso Online HG"
    assert ctx.idioma == "pt"
    assert ctx.nome == "Ana"
    assert ctx.perfil == {"cidade": "SP"}


def test_reengajamento_pos_sessao_nova_marca_turno_acao_sem_alterar_texto():
    """A resposta ja vem da saudacao/menu (gerada por _process_core com
    caminho/etapa resetados) — aqui so marcamos a observabilidade."""
    engine = _engine()
    ctx = _ctx()
    result = FlowResult(
        response_text="Ola! Como posso ajudar?", action="continue",
        caminho=None, etapa="menu",
    )

    out = engine._aplicar_reengajamento_pos(ctx, result, "sessao_nova")

    assert out.turno_acao == "sessao_nova"
    assert out.response_text == "Ola! Como posso ajudar?"


def test_reengajamento_sessao_nova_nao_repete_perguntas_ja_respondidas():
    """5.3.4 (regressao): apos expiracao, o bloco de fatos ja conhecidos
    (`_perfil_conhecido`, usado para instruir o LLM a NAO re-perguntar)
    continua completo — a expiracao nunca apaga os fatos do Contato."""
    engine = _engine()
    ctx = _ctx(horas_inatividade=settings.expira_sessao_horas + 1)

    bloco_antes = _perfil_conhecido(ctx)
    engine._aplicar_reengajamento_pre(ctx)
    bloco_depois = _perfil_conhecido(ctx)

    assert bloco_antes == bloco_depois
    assert "medico" in bloco_depois.lower()
    assert "Cirurgia" in bloco_depois
    assert "cidade" in bloco_depois.lower()


# ---------------------------------------------------------------------------
# Precedencia — reengajamento nunca sobrescreve handoff ja decidido
# ---------------------------------------------------------------------------

def test_reengajamento_pos_nunca_aplica_sobre_handoff():
    engine = _engine()
    ctx = _ctx()
    handoff_result = FlowResult(
        response_text="Vou te conectar com um especialista.",
        action="handoff", caminho=3, etapa="handoff",
        handoff_destino="consultores",
    )

    out = engine._aplicar_reengajamento_pos(ctx, handoff_result, "retomada")

    assert out.response_text == "Vou te conectar com um especialista."
    assert out.turno_acao is None


# ---------------------------------------------------------------------------
# process() wrapper — wiring completo (pre -> _process_core -> pos -> orcamento)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_wrapper_aplica_retomada_de_ponta_a_ponta():
    """`_process_core` roda com etapa/caminho INTACTOS quando o estado e
    retomada, e o prefixo cordial e aplicado ao resultado final."""
    engine = _engine()
    ctx = _ctx(
        horas_inatividade=(settings.reengajamento_horas + settings.expira_sessao_horas) / 2,
        turnos_sessao=0, turnos_no_no=0,
    )
    seen_etapa_caminho = {}

    async def fake_process_core(ticket_id, user_message, context):
        seen_etapa_caminho["etapa"] = context.etapa
        seen_etapa_caminho["caminho"] = context.caminho
        return FlowResult(
            response_text="Continuando de onde paramos.", action="continue",
            caminho=context.caminho, etapa=context.etapa,
        )

    with patch.object(engine, "_process_core", fake_process_core):
        result = await engine.process(1, "oi de novo", ctx)

    # _process_core viu a etapa/caminho ORIGINAIS (nao resetados).
    assert seen_etapa_caminho == {"etapa": "qualif_medico", "caminho": 3}
    assert result.turno_acao == "retomada"
    assert "Continuando de onde paramos." in result.response_text


@pytest.mark.asyncio
async def test_process_wrapper_aplica_sessao_nova_de_ponta_a_ponta():
    """`_process_core` roda com caminho/etapa JA resetados (None) quando o
    gap ultrapassa o limiar de expiracao."""
    engine = _engine()
    ctx = _ctx(horas_inatividade=settings.expira_sessao_horas + 1)
    seen_etapa_caminho = {}

    async def fake_process_core(ticket_id, user_message, context):
        seen_etapa_caminho["etapa"] = context.etapa
        seen_etapa_caminho["caminho"] = context.caminho
        return FlowResult(
            response_text="Ola! Bem-vindo de volta.", action="continue",
            caminho=None, etapa="menu",
        )

    with patch.object(engine, "_process_core", fake_process_core):
        result = await engine.process(1, "oi", ctx)

    assert seen_etapa_caminho == {"etapa": None, "caminho": None}
    assert result.turno_acao == "sessao_nova"
    # Perfil continua intacto no contexto apos o wrapper completo.
    assert ctx.eh_medico is True
    assert ctx.especialidade == "Cirurgia"


@pytest.mark.asyncio
async def test_process_wrapper_normal_nao_aplica_reengajamento():
    engine = _engine()
    ctx = _ctx(horas_inatividade=None)

    async def fake_process_core(ticket_id, user_message, context):
        return FlowResult(
            response_text="resposta comum", action="continue",
            caminho=context.caminho, etapa=context.etapa,
        )

    with patch.object(engine, "_process_core", fake_process_core):
        result = await engine.process(1, "oi", ctx)

    assert result.turno_acao is None
    assert result.response_text == "resposta comum"


# ---------------------------------------------------------------------------
# Regressao de plumbing — webhook seta context.horas_inatividade ANTES de
# engine.process() (mesmo padrao dos contadores de orcamento de turnos)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_engine_seta_horas_inatividade_antes_de_process():
    from unittest.mock import MagicMock

    from app.api.webhook import _handle_engine

    fake_redis = FakeHashRedis()
    agora = 2_000_000_000.0
    doze_horas_atras = agora - 12 * 3600
    fake_redis._hashes["estado:900600"] = {
        "ultima_interacao": str(int(doze_horas_atras)).encode(),
    }

    fake_result = FlowResult(
        response_text="ok", action="continue", caminho=None, etapa="menu", updates={},
    )
    seen_context: dict = {}

    async def spy_process(self_engine, ticket_id, user_message, context):
        seen_context["horas_inatividade"] = context.horas_inatividade
        return fake_result

    mock_context = SessionContext(
        ticket_id=1, chamado_id=900600, contato_id=10,
        caminho=None, etapa=None, idioma="pt",
        resumo_rolante=None, historico_recente=[], sessao_id=100,
    )

    mock_db_session = AsyncMock()
    mock_db_session.execute.return_value.scalar_one.side_effect = [10, "aberto"]
    mock_db_session.flush = AsyncMock()
    mock_db_session.commit = AsyncMock()
    mock_db_session.rollback = AsyncMock()
    mock_db_session.__aenter__ = AsyncMock(return_value=mock_db_session)
    mock_db_session.__aexit__ = AsyncMock(return_value=False)
    mock_session_factory = MagicMock(return_value=mock_db_session)

    messages_payload = [
        {
            "chamadoId": 900600,
            "sender": "5511999990600",
            "nome": "Lead",
            "mensagem": [{"type": "text", "text": "oi"}],
            "ticketData": None,
            "queueId": 78,
        }
    ]

    with (
        patch("app.main.get_session_factory", return_value=mock_session_factory),
        patch("app.api.webhook._get_redis", return_value=fake_redis),
        patch("app.api.webhook.time") as mock_time,
        patch("app.core.flow.FlowEngine.process", spy_process),
        patch(
            "app.core.memory.MemoryManager.load_context",
            AsyncMock(return_value=mock_context),
        ),
        patch("app.core.memory.MemoryManager.update_qualification_variables", AsyncMock()),
        patch("app.core.memory.MemoryManager.update_ticket_state", AsyncMock()),
        patch("app.core.memory.MemoryManager.save_message", AsyncMock()),
        patch("app.integrations.openai_client.OpenAIClient.__init__", return_value=None),
        patch("app.integrations.chatmaster.make_chatmaster_client"),
        patch("app.core.intent.IntentClassifier.__init__", return_value=None),
        patch("app.core.responder.GroundedResponder.__init__", return_value=None),
    ):
        mock_time.time.return_value = agora
        mock_time.monotonic.side_effect = __import__("time").monotonic
        await _handle_engine(900600, messages_payload)

    assert seen_context["horas_inatividade"] is not None
    assert 11.9 <= seen_context["horas_inatividade"] <= 12.1
