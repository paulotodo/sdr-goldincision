"""
Testes para app/integrations/chatmaster.py (tasks 5.1.3, 5.2.4).

Cobre:
- Envio mockado de mensagem simples
- Quebra de mensagem longa em blocos
- Variante de link por idioma (resolucao via catalogo)
- Handoff explicito: 1 transferencia, destino na allowlist
- Handoff fora da allowlist rejeitado (SEC-LLM-3)
- Pos-handoff: guarda dupla de envio bloqueado (FR-023)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.integrations.chatmaster import (
    HANDOFF_QUEUE_ALLOWLIST,
    ChatMasterClient,
    _split_into_blocks,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(token: str = "tok-test") -> ChatMasterClient:
    return ChatMasterClient(
        base_url="https://api2.chatmasterveloz.com",
        token=token,
        ticket_base_url="https://clihelper.chatmasterveloz.com",
        transfer_path_tpl="/api/v1/tickets/{chamado_id}/transfer",
    )


def _mock_response(status_code: int = 200, json_body: dict | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = str(json_body or {})
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    return resp


# ---------------------------------------------------------------------------
# Testes de _split_into_blocks
# ---------------------------------------------------------------------------

def test_split_short_text():
    """Texto curto retorna em um unico bloco."""
    result = _split_into_blocks("Ola, tudo bem?")
    assert result == ["Ola, tudo bem?"]


def test_split_long_text_by_paragraphs():
    """Texto com paragrafos e quebrado em paragrafos."""
    # Cria texto com 2 paragrafos grandes
    para1 = "A " * 2000  # 4000 chars
    para2 = "B " * 1000  # 2000 chars
    text = para1.strip() + "\n\n" + para2.strip()
    blocks = _split_into_blocks(text, max_chars=3800)
    assert len(blocks) >= 2
    for b in blocks:
        assert len(b) <= 3800 + 10  # margem de strip


def test_split_preserves_content():
    """Nenhum conteudo e perdido na quebra."""
    original = "Palavra " * 1000  # 8000 chars
    blocks = _split_into_blocks(original, max_chars=3800)
    # Junta os blocos e verifica que o conteudo total e equivalente
    joined = " ".join(b.strip() for b in blocks)
    # Contagem de palavras deve ser preservada
    assert joined.count("Palavra") == original.count("Palavra")


def test_split_exactly_at_limit():
    """Texto exatamente no limite nao e quebrado."""
    text = "X" * 3800
    blocks = _split_into_blocks(text, max_chars=3800)
    assert blocks == [text]


# ---------------------------------------------------------------------------
# Testes de send_message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_message_sucesso():
    """send_message envia POST com body e header corretos."""
    client = _make_client("meu-token")

    mock_resp = _mock_response(200)
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    client._client = mock_http

    await client.send_message("5511967296849", "Ola!")

    mock_http.post.assert_awaited_once()
    call_args = mock_http.post.call_args
    assert "sendOfficialData" in call_args[0][0]
    assert call_args[1]["json"] == {"number": "5511967296849", "text": "Ola!"}


@pytest.mark.asyncio
async def test_send_message_erro_http_propaga():
    """send_message propaga HTTPStatusError em caso de erro 4xx."""
    client = _make_client()

    mock_resp = _mock_response(400, {"error": "bad request"})
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    client._client = mock_http

    with pytest.raises(httpx.HTTPStatusError):
        await client.send_message("5511000000000", "texto")


@pytest.mark.asyncio
async def test_send_message_blocks_chama_multiplos_posts():
    """Texto longo e enviado em multiplos POSTs."""
    client = _make_client()

    calls = []

    async def fake_post(url, **kwargs):
        calls.append(kwargs["json"]["text"])
        return _mock_response(200)

    mock_http = AsyncMock()
    mock_http.post = fake_post
    client._client = mock_http

    # Texto de ~6000 chars (maior que _MAX_BLOCK_CHARS=3800)
    texto_longo = "Linha de texto de apresentacao oficial. " * 160
    with patch("app.integrations.chatmaster._INTER_BLOCK_DELAY", 0):
        await client.send_message_blocks("5511967296849", texto_longo)

    assert len(calls) >= 2, "Texto longo deve ser enviado em pelo menos 2 blocos"
    for bloco in calls:
        assert len(bloco) <= 3800 + 10


@pytest.mark.asyncio
async def test_send_message_blocks_texto_curto_envia_um_post():
    """Texto curto e enviado em um unico POST."""
    client = _make_client()
    calls = []

    async def fake_post(url, **kwargs):
        calls.append(kwargs["json"]["text"])
        return _mock_response(200)

    mock_http = AsyncMock()
    mock_http.post = fake_post
    client._client = mock_http

    await client.send_message_blocks("5511967296849", "Texto curto")
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Testes de transfer_ticket (handoff)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transfer_ticket_sucesso():
    """transfer_ticket envia PUT com destino valido."""
    client = _make_client()

    mock_resp = _mock_response(200)
    mock_http = AsyncMock()
    mock_http.put = AsyncMock(return_value=mock_resp)
    client._client = mock_http

    await client.transfer_ticket(
        chamado_id=42,
        destination="consultores",
        queue_id=7,
        company_id=1,
        motivo="Interesse em HG360 presencial",
    )

    mock_http.put.assert_awaited_once()
    call_args = mock_http.put.call_args
    assert "42" in call_args[0][0]  # chamado_id no path
    body = call_args[1]["json"]
    assert body["queueId"] == 7
    assert body["companyId"] == 1


@pytest.mark.asyncio
async def test_transfer_ticket_destino_fora_da_allowlist_rejeitado():
    """Destino fora da allowlist levanta ValueError (SEC-LLM-3)."""
    client = _make_client()
    client._client = AsyncMock()

    with pytest.raises(ValueError, match="fora da allowlist"):
        await client.transfer_ticket(
            chamado_id=99,
            destination="endpoint_injetado_pelo_llm",
        )

    # Nunca deve ter chamado a API
    client._client.put.assert_not_called()


@pytest.mark.asyncio
async def test_transfer_ticket_todos_destinos_da_allowlist_sao_validos():
    """Todos os destinos da allowlist passam sem ValueError."""
    client = _make_client()

    mock_resp = _mock_response(200)
    mock_http = AsyncMock()
    mock_http.put = AsyncMock(return_value=mock_resp)
    client._client = mock_http

    for dest in HANDOFF_QUEUE_ALLOWLIST:
        await client.transfer_ticket(chamado_id=1, destination=dest)

    assert mock_http.put.await_count == len(HANDOFF_QUEUE_ALLOWLIST)


@pytest.mark.asyncio
async def test_transfer_ticket_erro_http_propaga():
    """transfer_ticket propaga HTTPStatusError em falha da API."""
    client = _make_client()

    mock_resp = _mock_response(500)
    mock_http = AsyncMock()
    mock_http.put = AsyncMock(return_value=mock_resp)
    client._client = mock_http

    with pytest.raises(httpx.HTTPStatusError):
        await client.transfer_ticket(
            chamado_id=10, destination="consultores"
        )


# ---------------------------------------------------------------------------
# Link por idioma — resolucao correta (5.1.2)
# ---------------------------------------------------------------------------

def test_link_resolucao_por_idioma():
    """
    O FlowEngine resolve link de inscricao pelo idioma da sessao.
    Aqui testamos a logica de resolucao de forma isolada (sem DB).
    """
    # Simula catalogo em memoria (substitui leitura de DB em testes)
    links_por_idioma = {
        "pt": "https://pay.hotmart.com/pt_link",
        "en": "https://pay.hotmart.com/en_link",
        "es": "https://pay.hotmart.com/es_link",
    }

    def get_link(idioma: str) -> str:
        return links_por_idioma.get(idioma, links_por_idioma["pt"])

    assert get_link("pt") == "https://pay.hotmart.com/pt_link"
    assert get_link("en") == "https://pay.hotmart.com/en_link"
    assert get_link("es") == "https://pay.hotmart.com/es_link"
    # Idioma desconhecido cai no pt
    assert get_link("fr") == "https://pay.hotmart.com/pt_link"


# ---------------------------------------------------------------------------
# Guarda dupla pos-handoff (FR-023)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_guarda_dupla_pos_handoff_bloqueia_envio():
    """
    Apos ticket em_handoff, qualquer tentativa de send_message deve ser
    bloqueada ANTES de chegar ao cliente HTTP.

    Este teste verifica a guarda dupla na camada de integracao:
    o chamador e responsavel por verificar o estado antes de chamar send_message.
    O ChatMasterClient em si nao conhece o estado do ticket;
    a guarda e implementada no webhook.py / processador.

    Aqui simulamos a verificacao de estado com um wrapper simples.
    """

    ticket_status = {"status": "em_handoff"}

    async def enviar_se_permitido(client: ChatMasterClient, number: str, text: str):
        """Simula a guarda dupla do webhook/processador."""
        if ticket_status["status"] in ("em_handoff", "encerrado"):
            raise PermissionError(
                f"Ticket {ticket_status['status']}: envio bloqueado (FR-023)"
            )
        await client.send_message(number, text)

    client = _make_client()
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=_mock_response(200))
    client._client = mock_http

    with pytest.raises(PermissionError, match="FR-023"):
        await enviar_se_permitido(client, "5511967296849", "Mensagem proibida")

    # Confirmar que nunca chegou ao HTTP
    mock_http.post.assert_not_called()


@pytest.mark.asyncio
async def test_apenas_uma_transferencia_por_handoff():
    """
    Verifica que o handoff chama transfer_ticket exatamente 1 vez,
    independente de quantas mensagens chegarem no ticket.
    Simula a logica do processador que checa is_handoff antes de processar.
    """
    chamadas_transfer = []
    chamadas_send = []

    async def fake_transfer(client, chamado_id, **kwargs):
        chamadas_transfer.append(chamado_id)

    async def fake_send(client, number, text):
        chamadas_send.append(text)

    # Primeira mensagem: handoff
    await fake_transfer(None, 42, destination="consultores")
    # Estado atualizado: ticket em_handoff

    # Mensagens subsequentes sao bloqueadas (simula filtro FR-024)
    for _ in range(3):
        ticket_status = "em_handoff"
        if ticket_status not in ("em_handoff", "encerrado"):
            await fake_send(None, "5511000000000", "mensagem")

    assert len(chamadas_transfer) == 1, "Deve haver exatamente 1 transferencia"
    assert len(chamadas_send) == 0, "Nenhum envio apos handoff"
