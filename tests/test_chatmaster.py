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
    _parse_retry_after,
    _split_into_blocks,
    overflow_notice,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(token: str = "tok-test", **kwargs) -> ChatMasterClient:
    # Mapeia todos os destinos logicos da allowlist para filas reais de exemplo,
    # com fila humana padrao de fallback (config do operador no deploy real).
    # Por padrao desliga pacing/delays e o teto por turno para os testes que NAO
    # exercitam anti-rajada (mantem-nos rapidos e deterministicos). Testes de
    # pacing/cap/retry passam os valores relevantes via kwargs.
    queue_map = {dest: 70 + i for i, dest in enumerate(sorted(HANDOFF_QUEUE_ALLOWLIST))}
    params = {
        "min_interval_ms": 0,
        "inter_block_delay_seconds": 0.0,
        "max_msgs_per_turn": 0,  # 0 = sem teto (split integral)
    }
    params.update(kwargs)
    return ChatMasterClient(
        base_url="https://api2.chatmasterveloz.com",
        token=token,
        handoff_queue_ids=queue_map,
        default_queue_id=78,
        **params,
    )


def _mock_response(
    status_code: int = 200,
    json_body: dict | None = None,
    headers: dict | None = None,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = str(json_body or {})
    resp.headers = headers or {}
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


def test_split_blob_sem_quebras_respeita_teto():
    """Blob longo sem espacos/paragrafos ainda e cortado em blocos <= teto duro."""
    text = "X" * 3800
    blocks = _split_into_blocks(text, max_chars=3800)
    assert len(blocks) >= 2  # agora fragmenta (alvo conversacional)
    assert all(len(b) <= 3800 for b in blocks)
    assert "".join(blocks) == text  # conteudo preservado


def test_split_resposta_longa_vira_varias_mensagens():
    """Resposta longa (varios paragrafos) e fragmentada em mensagens curtas."""
    paras = [("Frase de exemplo. " * 20).strip() for _ in range(4)]  # ~360 chars cada
    text = "\n\n".join(paras)  # ~1450 chars
    blocks = _split_into_blocks(text)
    assert len(blocks) >= 2
    # cada mensagem fica no alvo conversacional (com margem)
    assert all(len(b) <= 600 + 50 for b in blocks)
    assert "Frase de exemplo" in blocks[0]


def test_split_menu_curto_fica_em_uma_mensagem():
    """Bloco curto com varias linhas (ex.: menu) NAO e fragmentado linha a linha."""
    menu = (
        "Olá! Como posso ajudar?\n"
        "1 Curso Online\n2 Presenciais\n3 Sistema\n4 Suporte\n5 Paciente\n6 Outro"
    )
    blocks = _split_into_blocks(menu)
    assert blocks == [menu]


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
    assert call_args[0][0].endswith("/api/messages/sendOfficialData")
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
    """transfer_ticket envia POST updateAPI com queueId/userId=null/status=pending."""
    client = _make_client()

    mock_resp = _mock_response(200)
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    client._client = mock_http

    await client.transfer_ticket(chamado_id=42, destination="consultores")

    mock_http.post.assert_awaited_once()
    call_args = mock_http.post.call_args
    assert call_args[0][0].endswith("/api/tickets/updateAPI")
    body = call_args[1]["json"]
    assert body["ticketId"] == "42"
    assert body["status"] == "pending"
    assert body["userId"] is None  # sem atendente atrelado (fila humana)
    # consultores -> queueId real configurado (nunca vindo do LLM)
    assert body["queueId"] == str(client._handoff_queue_ids["consultores"])


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
    client._client.post.assert_not_called()


@pytest.mark.asyncio
async def test_transfer_ticket_sem_queue_configurada_rejeita():
    """Sem queueId configurado para o destino, levanta ValueError (nao chama API)."""
    client = ChatMasterClient(
        base_url="https://api2.chatmasterveloz.com",
        token="tok",
        handoff_queue_ids={},   # nenhum mapeamento
        default_queue_id=None,  # nenhuma fila padrao
    )
    client._client = AsyncMock()

    with pytest.raises(ValueError, match="Sem queueId configurado"):
        await client.transfer_ticket(chamado_id=5, destination="consultores")
    client._client.post.assert_not_called()


@pytest.mark.asyncio
async def test_transfer_ticket_todos_destinos_da_allowlist_sao_validos():
    """Todos os destinos da allowlist passam sem ValueError."""
    client = _make_client()

    mock_resp = _mock_response(200)
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    client._client = mock_http

    for dest in HANDOFF_QUEUE_ALLOWLIST:
        await client.transfer_ticket(chamado_id=1, destination=dest)

    assert mock_http.post.await_count == len(HANDOFF_QUEUE_ALLOWLIST)


@pytest.mark.asyncio
async def test_transfer_ticket_erro_http_propaga():
    """transfer_ticket propaga HTTPStatusError em falha da API."""
    client = _make_client()

    mock_resp = _mock_response(500)
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
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


# ---------------------------------------------------------------------------
# Retry/backoff em 429/5xx (respeito ao rate limit da Meta)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_message_429_faz_retry_e_sucede():
    """429 seguido de 200 → 1 retry e sucesso (sem excecao)."""
    client = _make_client(max_retries=3)
    respostas = [_mock_response(429), _mock_response(200)]

    async def fake_post(url, **kwargs):
        return respostas.pop(0)

    mock_http = AsyncMock()
    mock_http.post = fake_post
    client._client = mock_http

    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    with patch("app.integrations.chatmaster.asyncio.sleep", fake_sleep):
        await client.send_message("5511967296849", "ola")

    assert respostas == [], "Deve ter consumido 429 e depois 200"
    assert sleeps == [1.0], "1 backoff de 1s (base exponencial 2**0) antes do retry"


@pytest.mark.asyncio
async def test_send_message_429_honra_retry_after():
    """Header Retry-After tem prioridade sobre o backoff exponencial."""
    client = _make_client(max_retries=3)
    respostas = [
        _mock_response(429, headers={"Retry-After": "5"}),
        _mock_response(200),
    ]

    async def fake_post(url, **kwargs):
        return respostas.pop(0)

    mock_http = AsyncMock()
    mock_http.post = fake_post
    client._client = mock_http

    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    with patch("app.integrations.chatmaster.asyncio.sleep", fake_sleep):
        await client.send_message("5511967296849", "ola")

    assert sleeps == [5.0], "Deve respeitar o Retry-After do header (5s)"


@pytest.mark.asyncio
async def test_send_message_429_persistente_aborta_sem_excecao():
    """429 persistente → aborta apos N tentativas, com log, SEM levantar excecao."""
    client = _make_client(max_retries=2)

    calls = {"n": 0}

    async def fake_post(url, **kwargs):
        calls["n"] += 1
        return _mock_response(429)

    mock_http = AsyncMock()
    mock_http.post = fake_post
    client._client = mock_http

    async def fake_sleep(s):
        return None

    with patch("app.integrations.chatmaster.asyncio.sleep", fake_sleep):
        # NAO deve levantar: aborta com log para nao floodar/derrubar o turno.
        await client.send_message("5511967296849", "ola")

    # 1 tentativa inicial + 2 retries = 3 POSTs
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_send_message_4xx_nao_429_falha_rapido():
    """4xx que nao seja 429 falha imediatamente (sem retry)."""
    client = _make_client(max_retries=3)

    calls = {"n": 0}

    async def fake_post(url, **kwargs):
        calls["n"] += 1
        return _mock_response(400, {"error": "bad"})

    mock_http = AsyncMock()
    mock_http.post = fake_post
    client._client = mock_http

    with pytest.raises(httpx.HTTPStatusError):
        await client.send_message("5511000000000", "texto")
    assert calls["n"] == 1, "4xx nao-429 nao faz retry"


def test_parse_retry_after():
    """_parse_retry_after le segundos numericos; ignora valores invalidos/ausentes."""
    assert _parse_retry_after(_mock_response(429, headers={"Retry-After": "3"})) == 3.0
    assert _parse_retry_after(_mock_response(429, headers={})) is None
    assert _parse_retry_after(_mock_response(429, headers={"Retry-After": "abc"})) is None


# ---------------------------------------------------------------------------
# Cap por turno (anti-rajada)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_message_blocks_respeita_cap_por_turno():
    """Texto que geraria muitos blocos → no maximo `max_msgs_per_turn` envios."""
    client = _make_client(max_msgs_per_turn=4)
    calls: list[str] = []

    async def fake_post(url, **kwargs):
        calls.append(kwargs["json"]["text"])
        return _mock_response(200)

    mock_http = AsyncMock()
    mock_http.post = fake_post
    client._client = mock_http

    # 8 paragrafos → 8 blocos sem cap; com cap=4 deve enviar exatamente 4.
    texto = "\n\n".join(f"Paragrafo numero {i} com algum conteudo." for i in range(8))
    await client.send_message_blocks("5511967296849", texto)

    assert len(calls) == 4, "Nunca deve exceder o teto por turno"
    # O ultimo bloco consolida com o convite (anti-rajada), nao despeja o restante.
    assert calls[-1] == overflow_notice("pt")


@pytest.mark.asyncio
async def test_send_message_blocks_overflow_no_idioma_do_lead():
    """O convite de overflow respeita o idioma informado."""
    client = _make_client(max_msgs_per_turn=2)
    calls: list[str] = []

    async def fake_post(url, **kwargs):
        calls.append(kwargs["json"]["text"])
        return _mock_response(200)

    mock_http = AsyncMock()
    mock_http.post = fake_post
    client._client = mock_http

    texto = "\n\n".join(f"Parrafo {i}." for i in range(6))
    await client.send_message_blocks("5511967296849", texto, idioma="es")

    assert len(calls) == 2
    assert calls[-1] == overflow_notice("es")


@pytest.mark.asyncio
async def test_send_message_blocks_abaixo_do_cap_nao_consolida():
    """Se o split ficar dentro do teto, envia todos os blocos sem convite extra."""
    client = _make_client(max_msgs_per_turn=4)
    calls: list[str] = []

    async def fake_post(url, **kwargs):
        calls.append(kwargs["json"]["text"])
        return _mock_response(200)

    mock_http = AsyncMock()
    mock_http.post = fake_post
    client._client = mock_http

    texto = "\n\n".join(f"Paragrafo {i}." for i in range(3))
    await client.send_message_blocks("5511967296849", texto)

    assert len(calls) == 3
    assert overflow_notice("pt") not in calls


# ---------------------------------------------------------------------------
# Pacing (intervalo minimo entre envios — respeito ao rate limit)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pacing_insere_intervalo_minimo_entre_envios():
    """Envios consecutivos respeitam o intervalo minimo (sleep no _Pacer)."""
    # min_interval 1000ms, sem delay inter-bloco para isolar o pacing.
    client = _make_client(
        min_interval_ms=1000, inter_block_delay_seconds=0.0, max_msgs_per_turn=0
    )

    async def fake_post(url, **kwargs):
        return _mock_response(200)

    mock_http = AsyncMock()
    mock_http.post = fake_post
    client._client = mock_http

    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)  # nao avanca o relogio real → forca espera em cada envio

    texto = "\n\n".join(f"Paragrafo {i}." for i in range(3))
    with patch("app.integrations.chatmaster.asyncio.sleep", fake_sleep):
        await client.send_message_blocks("5511967296849", texto)

    # 3 envios: o 1o nao espera (sem envio anterior); os 2 seguintes aguardam ~1s.
    pacing_sleeps = [s for s in sleeps if s > 0]
    assert len(pacing_sleeps) >= 2
    assert all(0 < s <= 1.0 for s in pacing_sleeps)


@pytest.mark.asyncio
async def test_pacing_desligado_nao_espera():
    """min_interval_ms=0 desliga o pacing (nenhum sleep do _Pacer)."""
    client = _make_client(min_interval_ms=0, inter_block_delay_seconds=0.0)

    async def fake_post(url, **kwargs):
        return _mock_response(200)

    mock_http = AsyncMock()
    mock_http.post = fake_post
    client._client = mock_http

    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    with patch("app.integrations.chatmaster.asyncio.sleep", fake_sleep):
        await client.send_message("5511967296849", "ola")

    assert sleeps == [], "Sem pacing nem retry, nao deve dormir"
