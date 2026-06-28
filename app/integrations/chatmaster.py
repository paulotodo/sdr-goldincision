"""
Integracao com a API do ChatMaster.

Outbound (FR-016/017):
- POST https://api2.chatmasterveloz.com/api/messages/sendOfficialData
  Body: {"number": "...", "text": "..."}
  Auth: Bearer CHATMASTER_TOKEN (via secret)
- Mensagens longas quebradas em blocos; 1 pergunta por envio.

Handoff de tickets (FR-022/023/024) — CONTRATO REAL (API "Atualizar Ticket"):
- POST https://api2.chatmasterveloz.com/api/tickets/updateAPI
  Auth: Bearer CHATMASTER_TOKEN (via secret)
  Body: {"ticketId": "<id>", "status": "open|pending|closed",
         "userId": <id|null>, "queueId": <id|null>,
         "typebot_sessionId": "", "customA": "", "customB": ""}
  Ref: clihelper.chatmasterveloz.com/principal/apis/ticket/api-atualizar-ticket/
- Para transferir a uma FILA de atendimento humano: queueId = id da fila,
  userId = null (nenhum atendente atrelado), status = "pending".

Seguranca:
- Token Bearer via secret (NUNCA hardcoded ou logado — FR-032).
- O queueId vem SEMPRE da config do operador (handoff_queue_ids/
  handoff_queue_id_default), mapeado a partir do destino LOGICO do fluxo.
  O LLM nunca fornece um queueId arbitrario (SEC-LLM-3).
- Guarda dupla contra envio em ticket em_handoff (FR-023).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Tamanho maximo de um bloco de texto (caracteres).
# WhatsApp limita a 4096; usamos 3800 para margem.
_MAX_BLOCK_CHARS = 3800

# Pausa entre blocos consecutivos (evitar reordenamento no WhatsApp).
_INTER_BLOCK_DELAY = 0.4  # segundos

# Allowlist de destinos validos para handoff (SEC-LLM-3).
# Nunca aceitar destino como texto livre do LLM.
HANDOFF_QUEUE_ALLOWLIST: set[str] = {
    "consultores",
    "vendas",
    "suporte",
    "especialista",
    "presencial",
    "licenciamento",
    "franquia",
}


def _split_into_blocks(text: str, max_chars: int = _MAX_BLOCK_CHARS) -> list[str]:
    """
    Divide texto em blocos curtos sem cortar palavras.

    Estrategia:
    1. Se cabe num bloco: retorna [text].
    2. Tenta dividir em paragrafos (\n\n).
    3. Faz fallback para divisao por frases ('. ', '! ', '? ').
    4. Forca corte duro se nenhuma estrategia funcionar.

    Apresentacoes verbatim (FR-010): nao reescreve o texto; apenas
    fragmenta para respeitar o limite de tamanho.
    """
    if len(text) <= max_chars:
        return [text]

    blocks: list[str] = []
    remaining = text

    while len(remaining) > max_chars:
        # Tentar quebrar em paragrafo
        idx = remaining.rfind("\n\n", 0, max_chars)
        if idx > max_chars // 4:
            blocks.append(remaining[: idx + 2].rstrip())
            remaining = remaining[idx + 2 :].lstrip()
            continue

        # Tentar quebrar em fim de frase
        best = -1
        for sep in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
            pos = remaining.rfind(sep, 0, max_chars)
            if pos > best:
                best = pos + len(sep)

        if best > max_chars // 4:
            blocks.append(remaining[:best].rstrip())
            remaining = remaining[best:].lstrip()
            continue

        # Forca corte duro em espaco
        idx = remaining.rfind(" ", 0, max_chars)
        if idx > 0:
            blocks.append(remaining[:idx].rstrip())
            remaining = remaining[idx + 1 :]
        else:
            blocks.append(remaining[:max_chars])
            remaining = remaining[max_chars:]

    if remaining.strip():
        blocks.append(remaining.strip())

    return [b for b in blocks if b]


class ChatMasterClient:
    """
    Cliente HTTP para a API do ChatMaster.

    - send_message: envia texto ao numero via sendOfficialData.
    - send_message_blocks: divide e envia multiplos blocos.
    - transfer_ticket: handoff via API de tickets (destino por allowlist).

    Uso via context manager async e preferivel para reutilizar a conexao:
        async with ChatMasterClient(base_url, token) as client:
            await client.send_message(number, text)
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        handoff_queue_ids: Optional[dict[str, int]] = None,
        default_queue_id: Optional[int] = None,
        timeout_seconds: float = 15.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._token = token
        # Mapa destino-logico -> queueId e fila padrao (config do operador).
        self._handoff_queue_ids = handoff_queue_ids or {}
        self._default_queue_id = default_queue_id
        self._timeout = timeout_seconds
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "ChatMasterClient":
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    async def close(self) -> None:
        """Fecha o client HTTP subjacente."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Envio de mensagens (FR-016)
    # ------------------------------------------------------------------

    async def send_message(self, number: str, text: str) -> None:
        """
        Envia UM bloco de texto ao numero.

        O chamador e responsavel por garantir que text <= _MAX_BLOCK_CHARS.
        Para mensagens longas, use send_message_blocks.
        """
        client = self._ensure_client()
        url = f"{self._base_url}/api/messages/sendOfficialData"
        payload = {"number": number, "text": text}

        logger.debug(
            "chatmaster: send_message number=%s len=%d",
            number[:6] + "****",  # mascara parcial (nunca logar numero completo)
            len(text),
        )

        resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            logger.error(
                "chatmaster: send_message falhou status=%d body=%s",
                resp.status_code,
                resp.text[:200],
            )
            resp.raise_for_status()

    async def send_message_blocks(self, number: str, text: str) -> None:
        """
        Divide texto em blocos curtos e envia cada um em ordem.

        Uma pausa de _INTER_BLOCK_DELAY e inserida entre blocos para
        preservar a ordem de entrega no WhatsApp.
        Apresentacoes verbatim (FR-010): texto nunca e reescrito, apenas fragmentado.
        """
        blocks = _split_into_blocks(text)
        for i, block in enumerate(blocks):
            await self.send_message(number, block)
            if i < len(blocks) - 1:
                await asyncio.sleep(_INTER_BLOCK_DELAY)

    # ------------------------------------------------------------------
    # Handoff de ticket (FR-022/023/024)
    # ------------------------------------------------------------------

    async def transfer_ticket(
        self,
        chamado_id: int,
        destination: str,
        status: str = "pending",
    ) -> None:
        """
        Transfere o ticket para uma FILA de atendimento humano via a API
        oficial "Atualizar Ticket" (POST /api/tickets/updateAPI).

        Resolve o `destination` LOGICO (ex.: "consultores", "presencial",
        "licenciamento") para o queueId real configurado pelo operador
        (handoff_queue_ids -> fallback default_queue_id). O LLM nunca informa
        um queueId arbitrario (SEC-LLM-3): o destino so pode ser uma chave
        conhecida da allowlist/config; o id concreto vem sempre da config.

        Conforme a doc: para fila humana sem atendente atrelado, envia
        queueId=<id>, userId=null, status="pending".
        """
        if destination not in HANDOFF_QUEUE_ALLOWLIST:
            raise ValueError(
                f"Destino de handoff '{destination}' fora da allowlist. "
                f"Permitidos: {sorted(HANDOFF_QUEUE_ALLOWLIST)}"
            )

        queue_id = self._handoff_queue_ids.get(destination, self._default_queue_id)
        if queue_id is None:
            raise ValueError(
                f"Sem queueId configurado para destino '{destination}'. "
                "Defina HANDOFF_QUEUE_IDS_JSON e/ou HANDOFF_QUEUE_ID_DEFAULT."
            )
        if status not in ("open", "pending", "closed"):
            raise ValueError(f"status invalido: {status}")

        client = self._ensure_client()
        url = f"{self._base_url}/api/tickets/updateAPI"
        body: dict = {
            "ticketId": str(chamado_id),
            "status": status,
            "userId": None,        # sem atendente atrelado (fila humana)
            "queueId": str(queue_id),
            "typebot_sessionId": "",
            "customA": "",
            "customB": "",
        }

        logger.info(
            "chatmaster: transfer_ticket ticketId=%s destination=%s queueId=%s status=%s",
            chamado_id,
            destination,
            queue_id,
            status,
        )

        resp = await client.post(url, json=body)
        if resp.status_code >= 400:
            logger.error(
                "chatmaster: transfer_ticket falhou status=%d body=%s",
                resp.status_code,
                resp.text[:200],
            )
            resp.raise_for_status()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_client(self) -> httpx.AsyncClient:
        """Retorna o client ativo; cria um one-shot se nao houver contexto async."""
        if self._client is None:
            # Criacao one-shot (sem context manager): o chamador deve fechar via close()
            self._client = httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        return self._client


def make_chatmaster_client(settings) -> ChatMasterClient:
    """
    Factory conveniente que constroi ChatMasterClient a partir das settings.

    Variaveis relevantes:
    - settings.chatmaster_token     : Bearer token (via secret)
    - settings.chatmaster_base_url  : base de envio/tickets (api2.chatmasterveloz.com)
    - settings.handoff_queue_ids    : mapa destino-logico -> queueId (config)
    - settings.handoff_queue_id_default : fila humana padrao (fallback)
    """
    queue_ids = getattr(settings, "handoff_queue_ids", {}) or {}
    default_qid = getattr(settings, "handoff_queue_id_default", None)
    return ChatMasterClient(
        base_url=settings.chatmaster_base_url,
        token=settings.chatmaster_token,
        handoff_queue_ids=queue_ids,
        default_queue_id=default_qid,
    )
