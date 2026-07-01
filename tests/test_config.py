"""
Testes de app/config.py — envs de controle de turnos (task 1.1.6).

Cobre: defaults corretos dos novos envs (FASE 1, task 1.1) e override via
variavel de ambiente (pydantic-settings, case-insensitive).
"""
from __future__ import annotations

from app.config import Settings


def test_turnos_config_defaults():
    """Defaults dos envs de controle de turnos (Spec FR-007, FR-INFRA-01, FR-013)."""
    s = Settings()
    assert s.max_turnos_no_no == 6
    assert s.max_turnos_sessao == 25
    assert s.max_turnos_duvidas == 12
    assert s.reengajamento_horas == 24
    assert s.expira_sessao_horas == 72
    assert s.lock_ttl_ms == 90_000


def test_turnos_config_override_via_env(monkeypatch):
    """Todos os novos envs sao overridaveis (pydantic-settings env-driven)."""
    monkeypatch.setenv("MAX_TURNOS_NO_NO", "8")
    monkeypatch.setenv("MAX_TURNOS_SESSAO", "30")
    monkeypatch.setenv("MAX_TURNOS_DUVIDAS", "15")
    monkeypatch.setenv("REENGAJAMENTO_HORAS", "12")
    monkeypatch.setenv("EXPIRA_SESSAO_HORAS", "48")
    monkeypatch.setenv("LOCK_TTL_MS", "120000")

    s = Settings()

    assert s.max_turnos_no_no == 8
    assert s.max_turnos_sessao == 30
    assert s.max_turnos_duvidas == 15
    assert s.reengajamento_horas == 12
    assert s.expira_sessao_horas == 48
    assert s.lock_ttl_ms == 120_000


def test_turnos_config_types_are_int():
    """Os 6 novos campos devem ser inteiros (horas/contagens/ms), nunca str."""
    s = Settings()
    for field in (
        "max_turnos_no_no",
        "max_turnos_sessao",
        "max_turnos_duvidas",
        "reengajamento_horas",
        "expira_sessao_horas",
        "lock_ttl_ms",
    ):
        assert isinstance(getattr(s, field), int), f"{field} deveria ser int"


def test_verify_timeout_seconds_default():
    """Portao de Fidelidade (FASE 2, task 2.2.1): default hard = 3s."""
    s = Settings()
    assert s.verify_timeout_seconds == 3
    assert isinstance(s.verify_timeout_seconds, int)


def test_verify_timeout_seconds_override_via_env(monkeypatch):
    monkeypatch.setenv("VERIFY_TIMEOUT_SECONDS", "5")
    s = Settings()
    assert s.verify_timeout_seconds == 5
