"""
Integracao com a API do ChatMaster — envio de mensagens e handoff de tickets.

Outbound: POST https://api2.chatmasterveloz.com/api/messages/sendOfficialData
Body: {"number": "...", "text": "..."}
Auth: Bearer CHATMASTER_TOKEN (via secret)

Handoff de tickets via API de tickets (fila/conexao destino por allowlist).
Destino do handoff NUNCA e texto livre do LLM — resolvido por allowlist
de filas (SEC-LLM-3).

Implementacao completa: FASE 5 (tasks 5.1, 5.2).
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Allowlist de filas validas para handoff (SEC-LLM-3)
# Nunca aceitar destino de handoff como texto livre do LLM
HANDOFF_QUEUE_ALLOWLIST: set[str] = {
    "consultores",
    "vendas",
    "suporte",
}


class ChatMasterClient:
    """
    Cliente HTTP para API do ChatMaster.
    STUB: implementacao completa em FASE 5.
    """

    def __init__(self, base_url: str, token: str):
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client: Optional[httpx.AsyncClient] = None

    async def send_message(self, number: str, text: str) -> None:
        """
        Envia mensagem de texto ao numero via API oficial do ChatMaster.
        Quebra automaticamente mensagens longas (> 4096 chars).
        """
        # TODO (FASE 5): POST /api/messages/sendOfficialData
        # TODO (FASE 5): Bearer self._token
        # TODO (FASE 5): quebrar em blocos se len(text) > 4096
        raise NotImplementedError("ChatMasterClient.send_message implementado em FASE 5")

    async def transfer_ticket(
        self, ticket_id: int, destination: str, motivo: Optional[str] = None
    ) -> None:
        """
        Transfere ticket para fila/conexao via API de tickets.
        Destino DEVE estar na allowlist (SEC-LLM-3).
        """
        if destination not in HANDOFF_QUEUE_ALLOWLIST:
            raise ValueError(
                f"Destino de handoff '{destination}' fora da allowlist permitida. "
                f"Permitidos: {HANDOFF_QUEUE_ALLOWLIST}"
            )
        # TODO (FASE 5): chamar API de tickets do ChatMaster
        raise NotImplementedError("ChatMasterClient.transfer_ticket implementado em FASE 5")

    async def close(self) -> None:
        """Fecha o client HTTP."""
        if self._client:
            await self._client.aclose()
