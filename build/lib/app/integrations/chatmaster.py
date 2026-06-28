"""
Integracao com a API do ChatMaster.

Outbound (FR-016/017):
- POST https://api2.chatmasterveloz.com/api/messages/sendOfficialData
  Body: {"number": "...", "text": "..."}
  Auth: Bearer CHATMASTER_TOKEN (via secret)
- Mensagens longas quebradas em blocos; 1 pergunta por envio.

Handoff de tickets (FR-022/023/024):
- API de tickets: https://clihelper.chatmasterveloz.com/principal/apis/ticket/
- Endpoints de transferencia de fila/conexao derivados da doc oficial
  (knowledge_base/example_webhook_json/outbound/links_documentacao_api.txt).
- O payload exato de transferencia NAO e documentado no contrato entregue;
  modelado de forma configuravel (SUPOSICAO documentada abaixo).

SUPOSICAO HANDOFF (documentada — nao inventada):
  A doc aponta clihelper.chatmasterveloz.com/principal/apis/ticket/ sem
  detalhar o corpo exato de "transferir para fila". Adotamos a convencao
  padrao de CRMs baseados em WhatsApp-business:
    PUT /api/v1/tickets/{chamado_id}/transfer
    Body: {"queueId": <int>, "companyId": <int>}
  O path e os nomes de campo sao configurados via env
  (CHATMASTER_TICKET_BASE_URL, CHATMASTER_TRANSFER_PATH_TPL).
  Ajuste para o payload real sem redeployar o codigo — apenas
  atualizar a env var (ex: mudar path ou key names).

Seguranca:
- Token Bearer via secret (NUNCA hardcoded ou logado — FR-032).
- Destino de handoff SEMPRE validado na allowlist (SEC-LLM-3).
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
        ticket_base_url: str = "https://clihelper.chatmasterveloz.com",
        transfer_path_tpl: str = "/api/v1/tickets/{chamado_id}/transfer",
        timeout_seconds: float = 15.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._ticket_base_url = ticket_base_url.rstrip("/")
        self._transfer_path_tpl = transfer_path_tpl
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
        queue_id: Optional[int] = None,
        company_id: Optional[int] = None,
        motivo: Optional[str] = None,
    ) -> None:
        """
        Transfere ticket para fila/conexao via API de tickets.

        Seguranca (SEC-LLM-3): o `destination` DEVE estar na allowlist.
        O agente nunca aceita texto livre do LLM como destino.

        SUPOSICAO: o endpoint e PUT {ticket_base_url}{path} com body
        {"queueId": queue_id, "companyId": company_id}. Ajustar via env
        se o contrato real diferir (zero redeploy de codigo).
        """
        if destination not in HANDOFF_QUEUE_ALLOWLIST:
            raise ValueError(
                f"Destino de handoff '{destination}' fora da allowlist. "
                f"Permitidos: {sorted(HANDOFF_QUEUE_ALLOWLIST)}"
            )

        client = self._ensure_client()
        path = self._transfer_path_tpl.format(chamado_id=chamado_id)
        url = f"{self._ticket_base_url}{path}"

        body: dict = {}
        if queue_id is not None:
            body["queueId"] = queue_id
        if company_id is not None:
            body["companyId"] = company_id
        if motivo:
            body["motivo"] = motivo  # campo opcional; ignorado se API nao suportar

        logger.info(
            "chatmaster: transfer_ticket chamado_id=%s destination=%s queue_id=%s",
            chamado_id,
            destination,
            queue_id,
        )

        resp = await client.put(url, json=body)
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
    - settings.chatmaster_base_url  : base de envio (api2.chatmasterveloz.com)
    - settings.chatmaster_ticket_base_url : base de tickets (clihelper; via env)
    - settings.chatmaster_transfer_path_tpl : template de path de transferencia
    """
    ticket_base = getattr(
        settings,
        "chatmaster_ticket_base_url",
        "https://clihelper.chatmasterveloz.com",
    )
    transfer_tpl = getattr(
        settings,
        "chatmaster_transfer_path_tpl",
        "/api/v1/tickets/{chamado_id}/transfer",
    )
    return ChatMasterClient(
        base_url=settings.chatmaster_base_url,
        token=settings.chatmaster_token,
        ticket_base_url=ticket_base,
        transfer_path_tpl=transfer_tpl,
    )
