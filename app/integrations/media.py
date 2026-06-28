"""
Download de midia de mensagens do WhatsApp com defesa SSRF.

Implementa FR-004, FR-005 e controle SEC-WH-2 (OWASP API7 SSRF):
- Allowlist estrita de host: apenas `object.sp2.eveo.com.br`
- Bloqueio de IP privado/loopback/metadata (169.254.169.254)
- Esquema HTTPS obrigatorio
- Redirect fora da allowlist bloqueado
- Tipos suportados: text/audio/video/image/document
- Tipo desconhecido: descartar com log (FR-004)

Implementacao completa: FASE 3, task 3.3.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Faixas de IP privado/reservado bloqueadas (SSRF — SEC-WH-2)
_BLOCKED_NETWORKS = [
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("169.254.0.0/16"),  # metadata/link-local
    ipaddress.IPv4Network("0.0.0.0/8"),
    ipaddress.IPv4Network("100.64.0.0/10"),   # CGNAT
]

SUPPORTED_MEDIA_TYPES = {"text", "audio", "video", "image", "document"}


class SSRFError(Exception):
    """Tentativa de SSRF detectada."""


def _validate_url(url: str, allowlist: list[str]) -> None:
    """
    Valida URL contra allowlist de host e bloqueios de IP.
    Levanta SSRFError se a URL nao e permitida.
    """
    parsed = urlparse(url)

    # Apenas HTTPS
    if parsed.scheme != "https":
        raise SSRFError(f"Esquema nao permitido: {parsed.scheme!r}")

    # Host deve estar na allowlist
    host = parsed.hostname or ""
    if host not in allowlist:
        raise SSRFError(f"Host fora da allowlist: {host!r}")

    # Resolver IP e checar contra faixas bloqueadas
    try:
        addr = socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
        ip = ipaddress.IPv4Address(addr)
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                raise SSRFError(f"IP do host em faixa bloqueada: {ip} in {net}")
    except SSRFError:
        raise
    except Exception as exc:
        raise SSRFError(f"Falha ao resolver host {host!r}: {exc}") from exc


class MediaDownloader:
    """
    Download seguro de midia com protecao SSRF.
    STUB: implementacao completa em FASE 3, task 3.3.
    """

    def __init__(self, allowlist: list[str]):
        self._allowlist = allowlist

    async def download(
        self, url: str, media_type: str
    ) -> Optional[tuple[bytes, str]]:
        """
        Baixa midia validando URL e tipo.

        Args:
            url: URL da midia (ex: mediaUrl do payload)
            media_type: tipo declarado (audio/video/image/document/text)

        Returns:
            (bytes, content_type) ou None se tipo desconhecido

        Raises:
            SSRFError: se a URL violar as politicas de seguranca
        """
        if media_type not in SUPPORTED_MEDIA_TYPES:
            logger.warning(
                "media: tipo desconhecido '%s' — descartando (FR-004)", media_type
            )
            return None

        # Validar URL ANTES de fazer qualquer requisicao
        _validate_url(url, self._allowlist)

        # TODO (FASE 3): httpx.AsyncClient com follow_redirects=False
        # TODO (FASE 3): verificar redirect (se houver) contra allowlist
        # TODO (FASE 3): retornar (content, content_type)
        raise NotImplementedError("MediaDownloader.download implementado em FASE 3")
