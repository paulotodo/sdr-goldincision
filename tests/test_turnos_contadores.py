"""
Testes dos contadores de orcamento de turnos (US1, FASE 3, task 3.1).

Cobertura:
- 3.1.2: HINCRBY incrementa turnos_sessao exatamente 1x por chamada.
- 3.1.3: turnos_no_no reseta para 1 ao detectar mudanca de etapa_mapa_mestre.
- 3.1.4/3.1.6: fail-open — HGET ausente ou erro de Redis nao bloqueia o
  turno (contador tratado como 0/1, nunca levanta excecao).
- 3.1.5: o contador de turnos desta feature e ortogonal ao contador
  anti-loop `_tent_count`/`_MAX_TENTATIVAS` (nao se fundem).
- Regressao de plumbing (design US1 FASE 3): os contadores sao
  incrementados ANTES de FlowEngine.process(), nao em `finally` — o motor
  precisa ver o valor do turno atual para decidir nudge/handoff.

Abordagem: Redis em memoria (hash) fake, sem Redis real.
"""
from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class FakeHashRedis:
    """Redis em memoria minimalista: apenas operacoes de HASH usadas pelos
    contadores de turno (hget/hset/hincrby) — decode_responses=False (bytes),
    fiel ao cliente real (app/main.py)."""

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
    """Redis que sempre falha (simula Redis reiniciado/indisponivel)."""

    async def hget(self, *a, **kw):
        raise ConnectionError("redis indisponivel")

    async def hincrby(self, *a, **kw):
        raise ConnectionError("redis indisponivel")

    async def hset(self, *a, **kw):
        raise ConnectionError("redis indisponivel")


# ---------------------------------------------------------------------------
# turnos_sessao — task 3.1.2 (HINCRBY) e 3.1.6 (fail-open)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bump_turnos_sessao_incrementa_exatamente_1x_por_chamada():
    from app.api.webhook import _bump_turnos_sessao

    fake = FakeHashRedis()
    with patch("app.api.webhook._get_redis", return_value=fake):
        v1 = await _bump_turnos_sessao(chamado_id=1)
        v2 = await _bump_turnos_sessao(chamado_id=1)
        v3 = await _bump_turnos_sessao(chamado_id=1)

    assert (v1, v2, v3) == (1, 2, 3)


@pytest.mark.asyncio
async def test_bump_turnos_sessao_e_por_chamado_id_isolado():
    from app.api.webhook import _bump_turnos_sessao

    fake = FakeHashRedis()
    with patch("app.api.webhook._get_redis", return_value=fake):
        a1 = await _bump_turnos_sessao(chamado_id=100)
        b1 = await _bump_turnos_sessao(chamado_id=200)
        a2 = await _bump_turnos_sessao(chamado_id=100)

    assert (a1, b1, a2) == (1, 1, 2)


@pytest.mark.asyncio
async def test_bump_turnos_sessao_fail_open_em_erro_de_redis():
    """3.1.6: contador ausente/erro de Redis nao bloqueia o atendimento —
    retorna 0 (turno nao contabilizado, nao turno zero real)."""
    from app.api.webhook import _bump_turnos_sessao

    with patch("app.api.webhook._get_redis", return_value=BrokenRedis()):
        val = await _bump_turnos_sessao(chamado_id=1)

    assert val == 0


# ---------------------------------------------------------------------------
# turnos_no_no — task 3.1.3 (reset por mudanca de etapa) e 3.1.6 (fail-open)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bump_turnos_no_no_incrementa_enquanto_etapa_nao_muda():
    from app.api.webhook import _bump_turnos_no_no

    fake = FakeHashRedis()
    with patch("app.api.webhook._get_redis", return_value=fake):
        v1 = await _bump_turnos_no_no(chamado_id=1, etapa="qualif_medico")
        v2 = await _bump_turnos_no_no(chamado_id=1, etapa="qualif_medico")
        v3 = await _bump_turnos_no_no(chamado_id=1, etapa="qualif_medico")

    assert (v1, v2, v3) == (1, 2, 3)


@pytest.mark.asyncio
async def test_bump_turnos_no_no_reseta_ao_mudar_etapa():
    """3.1.3: contador por-no reseta para 1 ao detectar mudanca de
    etapa_mapa_mestre (nao acumula entre nos diferentes)."""
    from app.api.webhook import _bump_turnos_no_no

    fake = FakeHashRedis()
    with patch("app.api.webhook._get_redis", return_value=fake):
        v1 = await _bump_turnos_no_no(chamado_id=1, etapa="qualif_medico")
        v2 = await _bump_turnos_no_no(chamado_id=1, etapa="qualif_medico")
        v3 = await _bump_turnos_no_no(chamado_id=1, etapa="qualif_experiencia")
        v4 = await _bump_turnos_no_no(chamado_id=1, etapa="qualif_experiencia")

    assert (v1, v2) == (1, 2)
    assert (v3, v4) == (1, 2)  # reset ao mudar de etapa; volta a incrementar


@pytest.mark.asyncio
async def test_bump_turnos_no_no_primeiro_turno_no_no_e_1():
    """1o turno num no (sem marca anterior no hash) inicia em 1, nao 0."""
    from app.api.webhook import _bump_turnos_no_no

    fake = FakeHashRedis()
    with patch("app.api.webhook._get_redis", return_value=fake):
        v1 = await _bump_turnos_no_no(chamado_id=1, etapa=None)

    assert v1 == 1


@pytest.mark.asyncio
async def test_bump_turnos_no_no_fail_open_em_erro_de_redis():
    from app.api.webhook import _bump_turnos_no_no

    with patch("app.api.webhook._get_redis", return_value=BrokenRedis()):
        val = await _bump_turnos_no_no(chamado_id=1, etapa="duvidas")

    assert val == 0


# ---------------------------------------------------------------------------
# Ortogonalidade — task 3.1.5 (Acceptance Scenario 5, US1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_turnos_no_no_e_ortogonal_ao_contador_anti_loop_tentativas():
    """
    O contador de turnos desta feature (turnos_no_no, Redis) e independente
    do contador anti-loop de respostas NAO reconhecidas (_tent_count,
    etapa_funil/Postgres). Um turno RECONHECIDO incrementa turnos_no_no mas
    NAO incrementa _tent_count; os dois nunca se fundem (FR-001).
    """
    from app.api.webhook import _bump_turnos_no_no
    from app.core.flow import _tent_count
    from app.core.memory import SessionContext

    fake = FakeHashRedis()
    ctx = SessionContext(
        ticket_id=1, chamado_id=1, contato_id=1,
        caminho=3, etapa="qualif_medico", etapa_funil=None,
    )

    with patch("app.api.webhook._get_redis", return_value=fake):
        # 3 turnos "reconhecidos" seguidos no mesmo no.
        for _ in range(3):
            ctx.turnos_no_no = await _bump_turnos_no_no(ctx.chamado_id, ctx.etapa)

    assert ctx.turnos_no_no == 3
    # etapa_funil nunca foi tocado por _bump_turnos_no_no — contador
    # anti-loop permanece zerado (turnos reconhecidos nao contam como
    # "tentativa nao reconhecida").
    assert _tent_count(ctx, "qualif_medico") == 0


# ---------------------------------------------------------------------------
# Regressao de plumbing — contadores incrementados ANTES de
# FlowEngine.process() (design US1 FASE 3; nao mais em `finally`).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_engine_incrementa_contadores_antes_de_process():
    """
    Regressao critica: `_handle_engine` DEVE incrementar
    context.turnos_sessao/turnos_no_no ANTES de chamar
    FlowEngine.process() — e nao apenas em `finally` (comportamento antigo,
    pre-FASE 3) — para que o MESMO turno possa decidir nudge/handoff com
    base no valor atualizado (FlowEngine._aplicar_orcamento_turnos).
    """
    from app.api.webhook import _handle_engine
    from app.core.flow import FlowResult
    from app.core.memory import SessionContext

    fake_redis = FakeHashRedis()
    # Simula sessao ja com 4 turnos anteriores no hash.
    fake_redis._hashes["estado:900500"] = {"turnos_sessao": b"4"}

    fake_result = FlowResult(
        response_text="ok", action="continue", caminho=None, etapa="menu",
        updates={},
    )
    seen_context: dict = {}

    async def spy_process(self_engine, ticket_id, user_message, context):
        # Capturar o valor do contador NO MOMENTO em que o motor e chamado.
        seen_context["turnos_sessao"] = context.turnos_sessao
        seen_context["turnos_no_no"] = context.turnos_no_no
        return fake_result

    mock_context = SessionContext(
        ticket_id=1, chamado_id=900500, contato_id=10,
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
            "chamadoId": 900500,
            "sender": "5511999990500",
            "nome": "Lead",
            "mensagem": [{"type": "text", "text": "oi"}],
            "ticketData": None,
            "queueId": 78,
        }
    ]

    with (
        patch("app.main.get_session_factory", return_value=mock_session_factory),
        patch("app.api.webhook._get_redis", return_value=fake_redis),
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
        await _handle_engine(900500, messages_payload)

    # O motor viu o contador JA incrementado (4 -> 5) no MESMO turno.
    assert seen_context["turnos_sessao"] == 5
    assert seen_context["turnos_no_no"] == 1
    # context.turnos_sessao permanece disponivel apos process() para o
    # evento de observabilidade reusar (sem novo HINCRBY em `finally`).
    assert mock_context.turnos_sessao == 5
