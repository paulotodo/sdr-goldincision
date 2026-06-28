"""
Testes dos helpers de chave Redis (task 2.2.3 — TTL/expiracao).

Testa a corretude dos prefixos e parametros de TTL
(sem Redis real — testa apenas a logica de formacao de chaves).
"""
from __future__ import annotations

import pytest

from app.core.redis_keys import (
    IDEMP_TTL_SECONDS,
    LOCK_TTL_MS,
    HOT_WINDOW_TTL_SECONDS,
    idemp_key,
    debounce_key,
    lock_key,
    hot_window_key,
    estado_key,
)
from app.core.idempotency import compute_payload_hash


def test_idemp_key_format():
    """idemp_key gera chave com prefixo e hash corretos."""
    key = idemp_key(138901, "abc123")
    assert key == "idemp:138901:abc123"
    assert key.startswith("idemp:")


def test_debounce_key_format():
    """debounce_key gera chave com prefixo correto."""
    key = debounce_key(138901)
    assert key == "debounce:138901"


def test_lock_key_format():
    """lock_key gera chave com prefixo correto."""
    key = lock_key(138901)
    assert key == "lock:ticket:138901"


def test_hot_window_key_format():
    """hot_window_key gera chave com prefixo correto."""
    key = hot_window_key(138901)
    assert key == "sessao:138901:hot"


def test_estado_key_format():
    """estado_key gera chave com prefixo correto."""
    key = estado_key(138901)
    assert key == "estado:138901"


def test_idemp_ttl_is_24h():
    """Idempotencia deve durar 24h (86400s — FR-037)."""
    assert IDEMP_TTL_SECONDS == 86_400


def test_lock_ttl_is_30s_in_ms():
    """Lock de ticket deve ser 30s em millisegundos (FR-035)."""
    assert LOCK_TTL_MS == 30_000


def test_keys_are_unique_by_chamado_id():
    """Chaves de tickets diferentes nao colidem."""
    assert idemp_key(1, "abc") != idemp_key(2, "abc")
    assert lock_key(1) != lock_key(2)
    assert debounce_key(1) != debounce_key(2)
    assert hot_window_key(1) != hot_window_key(2)


def test_compute_payload_hash_deterministic():
    """Hash do payload e deterministico e canonico (ordem de keys nao importa)."""
    payload1 = {"a": 1, "b": 2}
    payload2 = {"b": 2, "a": 1}  # mesma informacao, ordem diferente
    assert compute_payload_hash(payload1) == compute_payload_hash(payload2)


def test_compute_payload_hash_different_payloads():
    """Payloads diferentes geram hashes diferentes."""
    h1 = compute_payload_hash({"chamadoId": 1, "texto": "ola"})
    h2 = compute_payload_hash({"chamadoId": 1, "texto": "tchau"})
    assert h1 != h2


def test_idemp_key_with_real_hash():
    """idemp_key formata corretamente com hash real de payload."""
    payload = {"chamadoId": 138901, "sender": "5511999999999"}
    h = compute_payload_hash(payload)
    key = idemp_key(138901, h)
    assert key.startswith("idemp:138901:")
    assert len(key) > 20  # hash SHA-256 tem 64 chars hex
