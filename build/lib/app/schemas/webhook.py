"""
Schemas Pydantic tolerantes para o payload do webhook ChatMaster (via n8n).

Design: extra=ignore em todos os modelos — novos campos do ChatMaster
nao quebram o parser. Apenas campos usados pelo motor conversacional sao
declarados; o restante e silenciosamente ignorado.

Fontes:
- knowledge_base/example_webhook_json/json_message,json  (mensagem: list)
- knowledge_base/example_webhook_json/json_audio,json    (mensagem: dict)
- knowledge_base/example_webhook_json/json_video,json    (mensagem: dict)
- knowledge_base/example_webhook_json/json_document,json (mensagem: dict)

Nota de contrato (anti-drift 8.1.2):
  Payloads de texto tem `mensagem` como List[dict].
  Payloads de midia (audio/video/document) tem `mensagem` como dict
  com campos {mediaType, mediaUrl, fromMe, body, ...}.
  O pre-validator `_normalise_mensagem` normaliza o dict → [MensagemItem].
"""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MensagemItem(BaseModel):
    """Item de mensagem (pode ser text, audio, video, image, document)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: str = "text"
    # Conteudo de texto
    text: Optional[str] = None
    # URL de midia (audio/video/image/document)
    url: Optional[str] = None
    # Campos alternativos de midia observados no payload real
    mediaUrl: Optional[str] = Field(default=None, alias="mediaUrl")
    filename: Optional[str] = None
    mimetype: Optional[str] = None

    @property
    def media_url(self) -> Optional[str]:
        """Retorna a URL de midia disponivel (url ou mediaUrl)."""
        return self.url or self.mediaUrl


class TicketVariables(BaseModel):
    """Variaveis do ticket (subset dos dados do lead)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    nome_lead: Optional[str] = None
    numero_lead: Optional[str] = None


class ContactData(BaseModel):
    """Dados do contato extraidos de ticketData.contact."""

    model_config = ConfigDict(extra="ignore")

    id: Optional[int] = None
    name: Optional[str] = None
    number: Optional[str] = None
    email: Optional[str] = None
    profilePicUrl: Optional[str] = None
    disableBot: Optional[bool] = False


class TicketData(BaseModel):
    """Dados do ticket encaminhados pelo ChatMaster."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    status: str = "open"
    lastMessage: Optional[str] = None
    protocolo: Optional[str] = None
    userId: Optional[int] = None
    contactId: Optional[int] = None
    whatsappId: Optional[int] = None
    queueId: Optional[int] = None
    companyId: Optional[int] = None
    channel: Optional[str] = None
    flowStatus: Optional[str] = None
    variables: Optional[TicketVariables] = None
    contact: Optional[ContactData] = None

    @property
    def is_handoff(self) -> bool:
        """True se o ticket esta em handoff ou encerrado (nao processar)."""
        return self.status in {"em_handoff", "encerrado", "closed", "resolved"}


class WebhookPayload(BaseModel):
    """
    Payload principal recebido do ChatMaster via n8n.

    Campos tolerantes: extra=ignore descarta tudo que nao e declarado.
    Todos os campos sao opcionais exceto chamadoId (necessario para
    idempotencia, debounce e lock).
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    # Identificador do ticket — obrigatorio para processamento
    chamadoId: int = Field(alias="chamadoId")

    # Numero do remetente no formato E.164
    sender: Optional[str] = None

    # Indica se a mensagem e do proprio agente (ignorar — FR-002)
    fromMe: bool = False

    # Nome do contato
    name: Optional[str] = None

    # Acao do evento (start, message, etc)
    acao: Optional[str] = None

    # Grupo (ignorar — fora do escopo)
    isGroup: bool = False

    companyId: Optional[int] = None
    queueId: Optional[int] = None
    defaultWhatsapp_x: Optional[int] = None

    # Lista de itens de mensagem
    mensagem: List[MensagemItem] = Field(default_factory=list)

    # Dados completos do ticket
    ticketData: Optional[TicketData] = None

    @model_validator(mode="before")
    @classmethod
    def _normalise_mensagem(cls, values: Any) -> Any:
        """
        Normaliza o campo `mensagem` para sempre ser uma lista.

        Payloads de texto: mensagem = [{type, text}, ...]  → sem mudanca.
        Payloads de midia (audio/video/document): mensagem = {mediaType, mediaUrl, ...}
          → converte para [{type: mediaType, mediaUrl: mediaUrl, ...}].

        Anti-drift guard (task 8.1.2): todos os 4 exemplos reais de
        knowledge_base/example_webhook_json/ devem ser parseados sem erro.
        """
        if not isinstance(values, dict):
            return values
        raw_msg = values.get("mensagem")
        if isinstance(raw_msg, dict):
            # Payload de midia: mensagem e um objeto unico
            media_type = raw_msg.get("mediaType") or "unknown"
            normalised: dict[str, Any] = {
                "type": media_type,
                "url": raw_msg.get("mediaUrl") or raw_msg.get("remoteUrl"),
                "mediaUrl": raw_msg.get("mediaUrl") or raw_msg.get("remoteUrl"),
                "filename": raw_msg.get("filename"),
                "mimetype": raw_msg.get("mimetype"),
                # Texto de transcricao ou caption pode estar em "body"
                "text": raw_msg.get("body") or raw_msg.get("text"),
            }
            values = dict(values)
            values["mensagem"] = [normalised]
        return values

    @model_validator(mode="after")
    def validate_mensagem_size(self) -> "WebhookPayload":
        """Limita itens em mensagem[] (SEC-WH-4)."""
        MAX_ITEMS = 50
        if len(self.mensagem) > MAX_ITEMS:
            self.mensagem = self.mensagem[:MAX_ITEMS]
        return self

    @property
    def ticket_status(self) -> Optional[str]:
        """Status do ticket se disponivel nos dados aninhados."""
        if self.ticketData:
            return self.ticketData.status
        return None

    @property
    def contact_number(self) -> str:
        """Numero de contato (sender ou numero_lead das variaveis)."""
        if self.sender:
            return self.sender
        if self.ticketData and self.ticketData.variables:
            return self.ticketData.variables.numero_lead or ""
        return ""

    @property
    def contact_name(self) -> Optional[str]:
        """Nome do contato."""
        if self.ticketData and self.ticketData.contact:
            return self.ticketData.contact.name
        return self.name

    @property
    def first_text(self) -> Optional[str]:
        """Texto da primeira mensagem de texto, se houver."""
        for item in self.mensagem:
            if item.type == "text" and item.text:
                return item.text
        return None
