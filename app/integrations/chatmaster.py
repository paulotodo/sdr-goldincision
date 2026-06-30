"""
Integracao com a API do ChatMaster.

Outbound (FR-016/017):
- POST {CHATMASTER_BASE_URL}/api/messages/sendOfficialData
  Body: {"number": "...", "text": "..."}
  Auth: Bearer CHATMASTER_TOKEN (via secret)
- Mensagens longas quebradas em blocos; 1 pergunta por envio.
- IMPORTANTE: a base e api.chatmasterveloz.com (a conta/webhook deste deploy).
  Existe tambem api2.chatmasterveloz.com (outra instancia) — usar a base errada
  causa 400 "Cannot read properties of null" (numero/conexao inexistente nela).

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
import re
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Tamanho maximo de um bloco de texto (caracteres).
# WhatsApp limita a 4096; usamos 3800 como teto duro de seguranca.
_MAX_BLOCK_CHARS = 3800

# Alvo conversacional por mensagem: respostas longas sao fragmentadas em varias
# mensagens curtas (UX de WhatsApp; Regra 13 "prefira respostas curtas").
# Cada paragrafo vira uma mensagem; paragrafos acima deste alvo sao divididos por frase.
_SOFT_BLOCK_CHARS = 400

# Pausa default entre blocos consecutivos (evitar reordenamento no WhatsApp).
# Configuravel por instancia (settings.inter_block_delay_seconds).
_INTER_BLOCK_DELAY = 1.0  # segundos

# Defaults de pacing/anti-rajada (sobrescreviveis por settings/env).
_DEFAULT_MIN_INTERVAL_MS = 1000   # intervalo minimo entre envios (Meta/BSP)
_DEFAULT_MAX_MSGS_PER_TURN = 4    # teto de mensagens por turno
_DEFAULT_MAX_RETRIES = 3          # tentativas em 429/5xx (backoff 1s,2s,4s)

# Convite curto usado quando o split excede o teto por turno: em vez de despejar
# todos os blocos restantes, consolida num unico bloco com convite (anti-rajada).
_OVERFLOW_NOTICE = {
    "pt": (
        "Tem bastante coisa para detalhar por aqui 😊 Posso continuar explicando o "
        "restante ou te conectar com um especialista que apresenta tudo "
        "pessoalmente — o que prefere?"
    ),
    "en": (
        "There's quite a bit more to cover 😊 I can keep explaining the rest here "
        "or connect you with a specialist who walks you through everything — which "
        "do you prefer?"
    ),
    "es": (
        "Hay bastante más para detallar 😊 Puedo seguir explicando el resto aquí o "
        "conectarte con un especialista que te lo presenta todo — ¿qué prefieres?"
    ),
}


def overflow_notice(idioma: str = "pt") -> str:
    """Convite curto (anti-rajada) no idioma do lead (fallback PT)."""
    return _OVERFLOW_NOTICE.get(idioma) or _OVERFLOW_NOTICE["pt"]


class _Pacer:
    """
    Garante um intervalo minimo entre envios consecutivos, por destinatario e
    global, respeitando o pacing/rate limit da WhatsApp Cloud API (Meta/BSP).

    Usa relogio monotonico + lock async: chamadas concorrentes serializam a
    espera, de modo que nunca dois envios partam mais proximos que o intervalo.
    """

    def __init__(self, min_interval_ms: int) -> None:
        self._min_interval = max(0.0, min_interval_ms / 1000.0)
        self._last_global = 0.0
        self._last_by_number: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, number: str) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            last = max(self._last_global, self._last_by_number.get(number, 0.0))
            delay = self._min_interval - (now - last)
            if delay > 0:
                await asyncio.sleep(delay)
                now = time.monotonic()
            self._last_global = now
            self._last_by_number[number] = now


def _parse_retry_after(resp: httpx.Response) -> Optional[float]:
    """Le o header Retry-After (segundos) quando presente e numerico."""
    raw = resp.headers.get("Retry-After") if resp is not None else None
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None

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


def _fragmentar_paragrafo(p: str, soft: int) -> list[str]:
    """Divide um paragrafo longo por frases (agrupando ate `soft`); se uma frase
    ainda exceder `soft`, corta em espacos. Nunca corta palavras."""
    out: list[str] = []
    buf = ""
    for s in re.split(r"(?<=[.!?])\s+", p):
        if len(s) > soft:
            if buf:
                out.append(buf)
                buf = ""
            while len(s) > soft:
                cut = s.rfind(" ", 0, soft)
                cut = cut if cut > 0 else soft
                out.append(s[:cut].rstrip())
                s = s[cut:].lstrip()
            buf = s
        elif not buf:
            buf = s
        elif len(buf) + 1 + len(s) <= soft:
            buf = f"{buf} {s}"
        else:
            out.append(buf)
            buf = s
    if buf:
        out.append(buf)
    return out


def _split_into_blocks(
    text: str,
    max_chars: int = _MAX_BLOCK_CHARS,
    soft_chars: int = _SOFT_BLOCK_CHARS,
) -> list[str]:
    """
    Divide o texto em mensagens curtas e naturais, sem cortar palavras.

    Estrategia (UX de WhatsApp):
    1. Cada paragrafo (linhas em branco) vira uma mensagem — respostas longas com
       varios paragrafos chegam como varias mensagens curtas.
    2. Paragrafos acima de `soft_chars` sao fragmentados por frase/espaco.
    3. Teto duro `max_chars` por seguranca do WhatsApp.

    Linhas separadas por uma unica quebra (ex.: opcoes de um menu) permanecem juntas
    na mesma mensagem. Apresentacoes verbatim (FR-010): o texto NAO e reescrito;
    apenas entregue em varias mensagens preservando o conteudo.
    """
    text = (text or "").strip()
    if not text:
        return []

    # 1-2. paragrafos → fragmentos <= soft (um paragrafo = uma mensagem)
    chunks: list[str] = []
    for p in (p.strip() for p in re.split(r"\n[ \t]*\n", text) if p.strip()):
        if len(p) <= soft_chars:
            chunks.append(p)
        else:
            chunks.extend(_fragmentar_paragrafo(p, soft_chars))

    # 3. teto duro de seguranca
    final: list[str] = []
    for m in chunks:
        while len(m) > max_chars:
            cut = m.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            final.append(m[:cut].rstrip())
            m = m[cut:].lstrip()
        if m:
            final.append(m)
    return final


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
        min_interval_ms: int = _DEFAULT_MIN_INTERVAL_MS,
        max_msgs_per_turn: int = _DEFAULT_MAX_MSGS_PER_TURN,
        inter_block_delay_seconds: float = _INTER_BLOCK_DELAY,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ):
        self._base_url = base_url.rstrip("/")
        self._token = token
        # Mapa destino-logico -> queueId e fila padrao (config do operador).
        self._handoff_queue_ids = handoff_queue_ids or {}
        self._default_queue_id = default_queue_id
        self._timeout = timeout_seconds
        self._client: Optional[httpx.AsyncClient] = None
        # Pacing / anti-rajada (respeito ao rate limit da Meta/BSP).
        self._max_msgs_per_turn = max(0, max_msgs_per_turn)
        self._inter_block_delay = max(0.0, inter_block_delay_seconds)
        self._max_retries = max(0, max_retries)
        self._pacer = _Pacer(min_interval_ms)

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
        Envia UM bloco de texto ao numero, respeitando o pacing da Meta/BSP.

        - Pacing: aguarda o intervalo minimo desde o ultimo envio (_Pacer).
        - 429/5xx: retry com backoff exponencial (1s, 2s, 4s), honrando o header
          `Retry-After` quando presente. Em falha persistente, aborta com log
          (NAO levanta excecao — evitar floodar/derrubar o restante do turno).
        - 4xx nao-429: falha rapido (raise_for_status).

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

        for attempt in range(self._max_retries + 1):
            await self._pacer.wait(number)
            resp = await client.post(url, json=payload)
            if resp.status_code < 400:
                return

            transient = resp.status_code == 429 or resp.status_code >= 500
            if transient and attempt < self._max_retries:
                retry_after = _parse_retry_after(resp)
                backoff = retry_after if retry_after is not None else float(2 ** attempt)
                logger.warning(
                    "chatmaster: send_message status=%d (tentativa %d/%d) — "
                    "backoff %.1fs%s",
                    resp.status_code,
                    attempt + 1,
                    self._max_retries,
                    backoff,
                    " (Retry-After)" if retry_after is not None else "",
                )
                await asyncio.sleep(backoff)
                continue

            if transient:
                # 429/5xx persistente: abortar sem floodar (NAO levanta excecao).
                logger.error(
                    "chatmaster: send_message status=%d persistente apos %d tentativas "
                    "— abortando envio (sem flood)",
                    resp.status_code,
                    self._max_retries,
                )
                return

            # 4xx nao-429: erro definitivo, falha rapido.
            logger.error(
                "chatmaster: send_message falhou status=%d body=%s",
                resp.status_code,
                resp.text[:200],
            )
            resp.raise_for_status()
            return

    async def send_message_blocks(
        self, number: str, text: str, idioma: str = "pt"
    ) -> None:
        """
        Divide texto em blocos curtos e envia cada um em ordem, com pacing.

        Anti-rajada: nunca envia mais que `max_msgs_per_turn` mensagens por turno.
        Se o split gerar mais blocos que o teto, envia os primeiros N-1 e consolida
        o restante num unico bloco curto com convite (em vez de despejar todos).

        Uma pausa de `inter_block_delay_seconds` e inserida entre blocos para
        preservar a ordem de entrega no WhatsApp; o _Pacer garante o intervalo
        minimo global da Meta/BSP.
        Apresentacoes verbatim (FR-010): texto nunca e reescrito, apenas fragmentado.
        """
        blocks = _split_into_blocks(text)
        if not blocks:
            return

        cap = self._max_msgs_per_turn
        if cap and len(blocks) > cap:
            # Mantem os primeiros N-1 blocos e substitui o restante por um convite
            # curto (anti-rajada). Nunca excede o teto por turno.
            blocks = blocks[: cap - 1] + [overflow_notice(idioma)]

        for i, block in enumerate(blocks):
            await self.send_message(number, block)
            if i < len(blocks) - 1 and self._inter_block_delay > 0:
                await asyncio.sleep(self._inter_block_delay)

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
    - settings.whatsapp_min_interval_ms : intervalo minimo entre envios (pacing)
    - settings.max_msgs_per_turn        : teto de mensagens por turno (anti-rajada)
    - settings.inter_block_delay_seconds: pausa entre blocos consecutivos
    """
    queue_ids = getattr(settings, "handoff_queue_ids", {}) or {}
    default_qid = getattr(settings, "handoff_queue_id_default", None)
    return ChatMasterClient(
        base_url=settings.chatmaster_base_url,
        token=settings.chatmaster_token,
        handoff_queue_ids=queue_ids,
        default_queue_id=default_qid,
        min_interval_ms=getattr(
            settings, "whatsapp_min_interval_ms", _DEFAULT_MIN_INTERVAL_MS
        ),
        max_msgs_per_turn=getattr(
            settings, "max_msgs_per_turn", _DEFAULT_MAX_MSGS_PER_TURN
        ),
        inter_block_delay_seconds=getattr(
            settings, "inter_block_delay_seconds", _INTER_BLOCK_DELAY
        ),
    )
