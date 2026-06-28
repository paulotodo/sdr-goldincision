"""
Testes de defesa SSRF do MediaDownloader (task 3.3.3).

Cenarios cobertos:
- Host fora da allowlist -> SSRFError
- IP privado/metadata bloqueado -> SSRFError
- Esquema nao-https -> SSRFError
- Tipo de midia desconhecido -> None (nao SSRFError)
- Host valido com IP publico -> validacao passa
- Redirect malicioso barrado -> SSRFError
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.media import (
    SUPPORTED_MEDIA_TYPES,
    MediaDownloader,
    SSRFError,
    _is_ip_blocked,
    _validate_url,
)

ALLOWLIST = ["object.sp2.eveo.com.br"]


# ---------------------------------------------------------------------------
# Testes unitarios de _is_ip_blocked
# ---------------------------------------------------------------------------

class TestIsIpBlocked:

    def test_loopback_bloqueado(self):
        assert _is_ip_blocked("127.0.0.1") is True

    def test_private_10_bloqueado(self):
        assert _is_ip_blocked("10.0.0.1") is True
        assert _is_ip_blocked("10.255.255.255") is True

    def test_private_172_bloqueado(self):
        assert _is_ip_blocked("172.16.0.1") is True
        assert _is_ip_blocked("172.31.255.255") is True

    def test_private_192168_bloqueado(self):
        assert _is_ip_blocked("192.168.1.1") is True

    def test_metadata_169254_bloqueado(self):
        assert _is_ip_blocked("169.254.169.254") is True

    def test_ip_publico_nao_bloqueado(self):
        # 8.8.8.8 (Google DNS) nao e privado
        assert _is_ip_blocked("8.8.8.8") is False

    def test_ip_invalido_nao_raise(self):
        # IP invalido nao deve levantar excecao
        assert _is_ip_blocked("nao-um-ip") is False


# ---------------------------------------------------------------------------
# Testes unitarios de _validate_url
# ---------------------------------------------------------------------------

class TestValidateUrl:

    def test_host_fora_da_allowlist_rejeitado(self):
        with pytest.raises(SSRFError, match="fora da allowlist"):
            _validate_url("https://evil.example.com/file.mp3", ALLOWLIST)

    def test_esquema_http_rejeitado(self):
        with pytest.raises(SSRFError, match="Esquema nao permitido"):
            _validate_url("http://object.sp2.eveo.com.br/file.mp3", ALLOWLIST)

    def test_esquema_ftp_rejeitado(self):
        with pytest.raises(SSRFError, match="Esquema nao permitido"):
            _validate_url("ftp://object.sp2.eveo.com.br/file.mp3", ALLOWLIST)

    def test_url_vazia_rejeitada(self):
        with pytest.raises(SSRFError, match="URL vazia"):
            _validate_url("", ALLOWLIST)

    def test_host_resolvendo_para_privado_rejeitado(self):
        """Host na allowlist mas resolvendo para IP privado deve ser bloqueado."""
        with patch("socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("10.0.0.1", 0))]
            with pytest.raises(SSRFError, match="faixa bloqueada"):
                _validate_url("https://object.sp2.eveo.com.br/file.mp3", ALLOWLIST)

    def test_host_valido_com_ip_publico_passa(self):
        """Host na allowlist resolvendo para IP publico deve passar."""
        with patch("socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("203.0.113.1", 0))]
            # Nao deve levantar excecao
            host = _validate_url("https://object.sp2.eveo.com.br/file.mp3", ALLOWLIST)
            assert host == "object.sp2.eveo.com.br"

    def test_metadata_ip_bloqueado(self):
        """IP de metadata AWS (169.254.169.254) deve ser bloqueado."""
        with patch("socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("169.254.169.254", 0))]
            with pytest.raises(SSRFError, match="faixa bloqueada"):
                _validate_url("https://object.sp2.eveo.com.br/file.mp3", ALLOWLIST)


# ---------------------------------------------------------------------------
# Testes do MediaDownloader
# ---------------------------------------------------------------------------

class TestMediaDownloader:

    def _make_downloader(self):
        return MediaDownloader(allowlist=ALLOWLIST)

    @pytest.mark.asyncio
    async def test_tipo_desconhecido_retorna_none(self):
        """Tipo de midia desconhecido deve retornar None sem SSRFError."""
        downloader = self._make_downloader()
        result = await downloader.download(
            "https://object.sp2.eveo.com.br/file.xyz",
            "sticker",  # tipo nao suportado
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_host_fora_allowlist_raise_ssrf(self):
        """Host fora da allowlist deve levantar SSRFError."""
        downloader = self._make_downloader()
        with pytest.raises(SSRFError):
            await downloader.download(
                "https://evil.com/audio.mp3",
                "audio",
            )

    @pytest.mark.asyncio
    async def test_esquema_http_raise_ssrf(self):
        """Esquema HTTP deve levantar SSRFError."""
        downloader = self._make_downloader()
        with pytest.raises(SSRFError, match="Esquema"):
            await downloader.download(
                "http://object.sp2.eveo.com.br/file.mp3",
                "audio",
            )

    @pytest.mark.asyncio
    async def test_download_valido_retorna_bytes(self):
        """Download valido deve retornar (bytes, content_type)."""
        downloader = self._make_downloader()

        fake_content = b"fake-audio-content"
        mock_response = MagicMock()
        mock_response.content = fake_content
        mock_response.headers = {"content-type": "audio/ogg"}
        mock_response.is_redirect = False
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("socket.getaddrinfo") as mock_gai,
            patch("app.integrations.media.httpx.AsyncClient", return_value=mock_client),
        ):
            mock_gai.return_value = [(None, None, None, None, ("203.0.113.1", 0))]
            result = await downloader.download(
                "https://object.sp2.eveo.com.br/audio/file.ogg",
                "audio",
            )

        assert result is not None
        content, content_type = result
        assert content == fake_content
        assert "audio" in content_type

    @pytest.mark.asyncio
    async def test_redirect_malicioso_barrado(self):
        """Redirect para host fora da allowlist deve levantar SSRFError."""
        downloader = self._make_downloader()

        mock_redirect_response = MagicMock()
        mock_redirect_response.is_redirect = True
        mock_redirect_response.headers = {"location": "https://evil.com/steal-data"}
        mock_redirect_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_redirect_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("socket.getaddrinfo") as mock_gai,
            patch("app.integrations.media.httpx.AsyncClient", return_value=mock_client),
        ):
            mock_gai.return_value = [(None, None, None, None, ("203.0.113.1", 0))]
            with pytest.raises(SSRFError):
                await downloader.download(
                    "https://object.sp2.eveo.com.br/audio/file.ogg",
                    "audio",
                )

    @pytest.mark.asyncio
    async def test_tipos_suportados_validados(self):
        """Todos os tipos suportados devem passar pela validacao de tipo."""
        assert SUPPORTED_MEDIA_TYPES == {"text", "audio", "video", "image", "document"}
        downloader = self._make_downloader()
        # Apenas verificar que a validacao de tipo nao rejeita os suportados
        # (o SSRF check vai falhar depois, mas o tipo e aceito)
        for media_type in SUPPORTED_MEDIA_TYPES:
            with pytest.raises(Exception):  # SSRF ou rede
                await downloader.download(
                    "https://object.sp2.eveo.com.br/test",
                    media_type,
                )
