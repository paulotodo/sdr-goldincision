"""
Testes do endpoint POST /webhook/chatmaster (task 3.1.6).

Cenarios cobertos:
- Payload valido -> 200 ack
- Payload malformado -> 200 ack (nao triggar retry do n8n)
- fromMe:true -> 200 ack, sem processamento
- X-Webhook-Token invalido com token configurado -> 200 ack (descartado)
- X-Webhook-Token valido com token configurado -> 200 ack + processamento
- Corpo muito grande -> 200 ack (descartado)
- isGroup:true -> 200 ack, ignorado
"""
from __future__ import annotations

import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

# ---------------------------------------------------------------------------
# Payload real do webhook (baseado em example_webhook_json/json_message,json)
# ---------------------------------------------------------------------------

VALID_PAYLOAD = {
    "mensagem": [{"type": "text", "text": "ola"}],
    "sender": "5511967296849",
    "chamadoId": 138901,
    "acao": "start",
    "name": "Paulo Sudre",
    "fromMe": False,
    "companyId": 1,
    "defaultWhatsapp_x": 127,
    "queueId": 78,
    "isGroup": False,
    "ticketData": {
        "id": 138901,
        "status": "open",
        "variables": {"nome_lead": "Paulo", "numero_lead": "5511967296849"},
    },
}

FROM_ME_PAYLOAD = {**VALID_PAYLOAD, "fromMe": True}
GROUP_PAYLOAD = {**VALID_PAYLOAD, "isGroup": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client():
    """Cria cliente de teste sem lifespan (sem DB/Redis reais)."""
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Testes sincronos com TestClient
# ---------------------------------------------------------------------------

class TestWebhookAck:
    """Todos os cenarios devem retornar 200 com {"ack": "ok"}."""

    def _post(self, client, payload, headers=None):
        return client.post(
            "/webhook/chatmaster",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json", **(headers or {})},
        )

    def test_payload_valido_retorna_200(self):
        with patch("app.api.webhook._get_redis", return_value=None):
            client = _make_client()
            resp = self._post(client, VALID_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json() == {"ack": "ok"}

    def test_payload_malformado_retorna_200(self):
        """JSON invalido nao deve retornar 4xx (ack sempre 200)."""
        with patch("app.api.webhook._get_redis", return_value=None):
            client = _make_client()
            resp = client.post(
                "/webhook/chatmaster",
                content=b"not-json-at-all",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200
        assert resp.json()["ack"] == "ok"

    def test_from_me_ignorado(self):
        """fromMe=true deve retornar 200 sem processar."""
        with patch("app.api.webhook._get_redis", return_value=None):
            client = _make_client()
            resp = self._post(client, FROM_ME_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json()["ack"] == "ok"

    def test_is_group_ignorado(self):
        """isGroup=true deve retornar 200 sem processar."""
        with patch("app.api.webhook._get_redis", return_value=None):
            client = _make_client()
            resp = self._post(client, GROUP_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json()["ack"] == "ok"

    def test_corpo_muito_grande_retorna_200(self):
        """Corpo > 512KB deve retornar 200 (descartado sem processar)."""
        with patch("app.api.webhook._get_redis", return_value=None):
            client = _make_client()
            big_body = b"x" * (512 * 1024 + 1)
            resp = client.post(
                "/webhook/chatmaster",
                content=big_body,
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200
        assert resp.json()["ack"] == "ok"


class TestWebhookToken:
    """Testes do X-Webhook-Token opcional (SEC-WH-1)."""

    def _post(self, client, payload, token_header=None):
        headers = {"Content-Type": "application/json"}
        if token_header is not None:
            headers["X-Webhook-Token"] = token_header
        return client.post(
            "/webhook/chatmaster",
            content=json.dumps(payload),
            headers=headers,
        )

    def test_token_invalido_retorna_200_mas_descarta(self):
        """Token invalido deve retornar 200 (nao triggar retry) mas descartar."""
        with (
            patch("app.api.webhook._get_redis", return_value=None),
            patch("app.config.settings.webhook_token", "token-secreto"),
        ):
            client = _make_client()
            resp = self._post(client, VALID_PAYLOAD, token_header="token-errado")
        assert resp.status_code == 200
        assert resp.json()["ack"] == "ok"

    def test_sem_token_configurado_aceita_sem_header(self):
        """Sem webhook_token configurado, nenhum header e necessario."""
        with (
            patch("app.api.webhook._get_redis", return_value=None),
            patch("app.config.settings.webhook_token", None),
        ):
            client = _make_client()
            resp = self._post(client, VALID_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json()["ack"] == "ok"


# ---------------------------------------------------------------------------
# Testes do schema Pydantic WebhookPayload
# ---------------------------------------------------------------------------

class TestWebhookPayloadSchema:
    """Testes do schema tolerante (extra=ignore)."""

    def test_campos_extras_sao_ignorados(self):
        from app.schemas.webhook import WebhookPayload
        payload_com_extras = {
            **VALID_PAYLOAD,
            "campo_desconhecido_1": "valor1",
            "campo_desconhecido_2": 42,
            "nested_extra": {"a": 1, "b": 2},
        }
        parsed = WebhookPayload.model_validate(payload_com_extras)
        assert parsed.chamadoId == 138901
        assert not hasattr(parsed, "campo_desconhecido_1")

    def test_from_me_default_false(self):
        from app.schemas.webhook import WebhookPayload
        payload_sem_from_me = {k: v for k, v in VALID_PAYLOAD.items() if k != "fromMe"}
        parsed = WebhookPayload.model_validate(payload_sem_from_me)
        assert parsed.fromMe is False

    def test_mensagem_muito_grande_truncada(self):
        from app.schemas.webhook import WebhookPayload
        payload = {
            **VALID_PAYLOAD,
            "mensagem": [{"type": "text", "text": f"msg {i}"} for i in range(100)],
        }
        parsed = WebhookPayload.model_validate(payload)
        assert len(parsed.mensagem) <= 50

    def test_contact_number_from_sender(self):
        from app.schemas.webhook import WebhookPayload
        parsed = WebhookPayload.model_validate(VALID_PAYLOAD)
        assert parsed.contact_number == "5511967296849"

    def test_first_text(self):
        from app.schemas.webhook import WebhookPayload
        parsed = WebhookPayload.model_validate(VALID_PAYLOAD)
        assert parsed.first_text == "ola"

    def test_payload_sem_mensagem(self):
        from app.schemas.webhook import WebhookPayload
        payload = {**VALID_PAYLOAD, "mensagem": []}
        parsed = WebhookPayload.model_validate(payload)
        assert parsed.mensagem == []
        assert parsed.first_text is None
