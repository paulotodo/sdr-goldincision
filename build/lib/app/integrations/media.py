"""
Download de midia de mensagens do WhatsApp com defesa SSRF.

Implementa FR-004, FR-005 e controle SEC-WH-2 (OWASP API7 SSRF):
- Allowlist estrita de host: apenas `object.sp2.eveo.com.br` (configuravel)
- Bloqueio de IP privado/loopback/metadata (169.254.x.x, 10.x, 172.16-31.x, 192.168.x)
- Esquema HTTPS obrigatorio
- Redirect fora da allowlist bloqueado (follow_redirects=False + validacao manual)
- Tipos suportados: text/audio/video/image/document
- Tipo desconhecido: descartar com log (FR-004)

Implementacao: FASE 3, task 3.3
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
_BLOCKED_NETWORKS: list[ipaddress.IPv4Network] = [
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("169.254.0.0/16"),    # metadata/link-local (AWS 169.254.169.254)
    ipaddress.IPv4Network("0.0.0.0/8"),
    ipaddress.IPv4Network("100.64.0.0/10"),     # CGNAT
    ipaddress.IPv4Network("198.18.0.0/15"),     # benchmark
    ipaddress.IPv4Network("240.0.0.0/4"),       # reservado
]

_BLOCKED_NETWORKS_V6: list[ipaddress.IPv6Network] = [
    ipaddress.IPv6Network("::1/128"),           # loopback IPv6
    ipaddress.IPv6Network("fc00::/7"),          # ULA
    ipaddress.IPv6Network("fe80::/10"),         # link-local
]

SUPPORTED_MEDIA_TYPES: set[str] = {"text", "audio", "video", "image", "document"}

# Timeout para download de midia (segundos)
_DOWNLOAD_TIMEOUT_SECONDS = 30
# Limite de tamanho do conteudo (20 MB)
_MAX_CONTENT_BYTES = 20 * 1024 * 1024


class SSRFError(Exception):
    """Tentativa de SSRF detectada ou URL invalida."""


def _is_ip_blocked(ip_str: str) -> bool:
    """Verifica se um IP esta em faixas bloqueadas (privado/metadata/reservado)."""
    try:
        addr = ipaddress.ip_address(ip_str)
        if isinstance(addr, ipaddress.IPv4Address):
            return any(addr in net for net in _BLOCKED_NETWORKS)
        if isinstance(addr, ipaddress.IPv6Address):
            return any(addr in net for net in _BLOCKED_NETWORKS_V6)
    except ValueError:
        pass
    return False


def _validate_url(url: str, allowlist: list[str]) -> str:
    """
    Valida URL contra allowlist de host e bloqueios de IP.

    Args:
        url:       URL a validar
        allowlist: lista de hostnames permitidos

    Returns:
        hostname validado

    Raises:
        SSRFError: se a URL violar qualquer politica de seguranca
    """
    if not url:
        raise SSRFError("URL vazia")

    parsed = urlparse(url)

    # 1. Apenas HTTPS
    if parsed.scheme != "https":
        raise SSRFError(f"Esquema nao permitido: {parsed.scheme!r} (apenas https)")

    # 2. Host deve estar na allowlist
    host = (parsed.hostname or "").lower()
    if not host:
        raise SSRFError("URL sem hostname")

    normalized_allowlist = [h.lower() for h in allowlist]
    if host not in normalized_allowlist:
        raise SSRFError(f"Host {host!r} fora da allowlist {normalized_allowlist}")

    # 3. Resolver IP e checar contra faixas bloqueadas
    try:
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip_str = info[4][0]
            if _is_ip_blocked(ip_str):
                raise SSRFError(
                    f"IP {ip_str!r} do host {host!r} esta em faixa bloqueada (SSRF)"
                )
    except SSRFError:
        raise
    except OSError as exc:
        raise SSRFError(f"Falha ao resolver host {host!r}: {exc}") from exc

    return host


class MediaDownloader:
    """
    Download seguro de midia com protecao SSRF completa.

    Seguranca:
    - Allowlist estrita de host (configura via settings.media_download_allowlist)
    - Bloqueio de IP privado/metadata na resolucao DNS
    - Sem seguimento automatico de redirects (cada redirect e validado)
    - Limite de tamanho de conteudo (20 MB)
    - Timeout de 30s
    """

    def __init__(self, allowlist: list[str]) -> None:
        self._allowlist = allowlist

    async def download(
        self, url: str, media_type: str
    ) -> Optional[tuple[bytes, str]]:
        """
        Baixa midia validando URL e tipo.

        Args:
            url:        URL da midia (ex: mediaUrl do payload ChatMaster)
            media_type: tipo declarado (audio/video/image/document/text)

        Returns:
            (bytes, content_type) ou None se tipo desconhecido

        Raises:
            SSRFError: se a URL violar as politicas de seguranca
            httpx.HTTPError: erros de rede
            ValueError: conteudo excede limite de tamanho
        """
        # Tipo desconhecido: descartar silenciosamente (FR-004)
        if media_type not in SUPPORTED_MEDIA_TYPES:
            logger.warning(
                "media: tipo desconhecido '%s' — descartando (FR-004)", media_type
            )
            return None

        # Validar URL ANTES de qualquer requisicao de rede
        _validate_url(url, self._allowlist)

        logger.debug("media: download iniciado type=%s url=%s...", media_type, url[:60])

        async with httpx.AsyncClient(
            follow_redirects=False,     # Redirects tratados manualmente abaixo
            timeout=httpx.Timeout(_DOWNLOAD_TIMEOUT_SECONDS),
        ) as client:
            response = await client.get(url)

            # Tratar redirects manualmente (validar destino contra allowlist)
            hops = 0
            max_hops = 5
            while response.is_redirect and hops < max_hops:
                redirect_url = response.headers.get("location", "")
                if not redirect_url:
                    break
                # Validar URL de redirect (pode tentar mover para IP privado)
                _validate_url(redirect_url, self._allowlist)
                response = await client.get(redirect_url)
                hops += 1

            if response.is_redirect:
                raise SSRFError(f"Muitos redirects ({max_hops}) ao baixar midia")

            response.raise_for_status()

            # Limite de tamanho
            content = response.content
            if len(content) > _MAX_CONTENT_BYTES:
                raise ValueError(
                    f"Conteudo da midia excede limite: "
                    f"{len(content)} > {_MAX_CONTENT_BYTES} bytes"
                )

            content_type = response.headers.get("content-type", "application/octet-stream")
            logger.info(
                "media: download concluido type=%s bytes=%d ct=%s",
                media_type, len(content), content_type,
            )
            return content, content_type
