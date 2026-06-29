"""
Reset de jornada (#reset) para numeros de teste autorizados.

Permite reiniciar TODA a memoria do agente para um contato/ticket — util em
testes de ponta a ponta. So afeta o proprio contato do remetente e somente se o
numero estiver na allowlist (tabela numero_teste, semeada do env + admin API).

Limpa:
  Postgres: mensagem (da sessao), sessao_conversa (resumo/variaveis), campos de
            qualificacao do contato, estado de fluxo do ticket.
  Redis:    janela quente, debounce, lock, idempotencia (best-effort) do chamado.
NAO toca no ChatMaster nem em outros contatos.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select, text

from app.core import redis_keys
from app.repository.models import NumeroTeste, Ticket

logger = logging.getLogger(__name__)


async def is_numero_teste(session, numero: str) -> bool:
    """True se o numero esta na allowlist de teste (ativo)."""
    if not numero:
        return False
    stmt = select(NumeroTeste.id).where(
        NumeroTeste.numero == str(numero), NumeroTeste.ativo.is_(True)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def _reset_postgres(session, chamado_id: int) -> bool:
    """Limpa estado do ticket/contato no Postgres. Retorna True se havia ticket."""
    stmt = select(Ticket).where(Ticket.chamado_id == chamado_id)
    ticket = (await session.execute(stmt)).scalar_one_or_none()
    if ticket is None:
        return False

    # Apagar mensagens das sessoes do ticket + as sessoes
    await session.execute(
        text(
            "DELETE FROM mensagem WHERE sessao_id IN "
            "(SELECT id FROM sessao_conversa WHERE ticket_id = :tid)"
        ),
        {"tid": ticket.id},
    )
    await session.execute(
        text("DELETE FROM sessao_conversa WHERE ticket_id = :tid"),
        {"tid": ticket.id},
    )
    # Resetar variaveis de qualificacao do contato
    await session.execute(
        text(
            "UPDATE contato SET eh_medico=NULL, especialidade=NULL, "
            "experiencia_corporal=NULL, produto_interesse=NULL, etapa_funil=NULL, "
            "idioma=NULL WHERE id = :cid"
        ),
        {"cid": ticket.contato_id},
    )
    # Resetar estado de fluxo do ticket
    ticket.caminho_atual = None
    ticket.etapa_mapa_mestre = None
    ticket.status = "aberto"
    ticket.handoff_motivo = None
    ticket.handoff_destino = None
    await session.commit()
    return True


async def _reset_redis(redis: Any, chamado_id: int) -> None:
    """Apaga as chaves Redis da sessao/ticket (best-effort)."""
    if redis is None:
        return
    chaves = [
        redis_keys.hot_window_key(chamado_id),
        redis_keys.estado_key(chamado_id),
        redis_keys.debounce_key(chamado_id),
        redis_keys.lock_key(chamado_id),
    ]
    try:
        await redis.delete(*chaves)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("reset: falha ao apagar chaves Redis chamado_id=%s: %s", chamado_id, exc)
    # Idempotencia: idemp:{chamado_id}:* (scan best-effort)
    try:
        padrao = f"{redis_keys.idemp_key(chamado_id, '')}*"
        cursor = 0
        while True:
            cursor, lote = await redis.scan(cursor=cursor, match=padrao, count=200)
            if lote:
                await redis.delete(*lote)
            if cursor == 0:
                break
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("reset: falha no scan idemp chamado_id=%s: %s", chamado_id, exc)


async def reset_conversa(
    session, redis: Any, chamado_id: int
) -> bool:
    """
    Reinicia toda a memoria do agente para o ticket/contato.
    Retorna True se algo foi resetado (sempre limpa Redis; DB se houver ticket).
    """
    tinha_ticket = False
    if session is not None:
        try:
            tinha_ticket = await _reset_postgres(session, chamado_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("reset: falha no reset Postgres chamado_id=%s: %s", chamado_id, exc)
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001
                pass
    await _reset_redis(redis, chamado_id)
    logger.info("reset: jornada reiniciada chamado_id=%s (ticket=%s)", chamado_id, tinha_ticket)
    return True


# Mensagem de confirmacao por idioma (texto fixo — nao passa pelo LLM)
_CONFIRMACAO = {
    "pt": "🔄 Memória reiniciada. Pode iniciar o teste do zero.",
    "en": "🔄 Memory reset. You can start the test from scratch.",
    "es": "🔄 Memoria reiniciada. Puedes iniciar la prueba desde cero.",
}


def confirmacao_reset(idioma: Optional[str] = "pt") -> str:
    return _CONFIRMACAO.get(idioma or "pt", _CONFIRMACAO["pt"])
