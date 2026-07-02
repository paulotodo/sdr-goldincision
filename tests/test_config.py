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


def test_slot_confidence_threshold_default():
    """Interpretacao Agentica (FASE 3, task 3.2.1): default = 0.6 (dec-009)."""
    s = Settings()
    assert s.slot_confidence_threshold == 0.6
    assert isinstance(s.slot_confidence_threshold, float)


def test_slot_confidence_threshold_override_via_env(monkeypatch):
    monkeypatch.setenv("SLOT_CONFIDENCE_THRESHOLD", "0.75")
    s = Settings()
    assert s.slot_confidence_threshold == 0.75


def test_intent_switch_confidence_threshold_default():
    """Fluidez de intencao (FASE 1, task 1.2.4): default = 0.6, mesmo
    padrao de slot_confidence_threshold (contracts/slot-troca-caminho.md S-2)."""
    s = Settings()
    assert s.intent_switch_confidence_threshold == 0.6
    assert isinstance(s.intent_switch_confidence_threshold, float)


def test_intent_switch_confidence_threshold_override_via_env(monkeypatch):
    monkeypatch.setenv("INTENT_SWITCH_CONFIDENCE_THRESHOLD", "0.8")
    s = Settings()
    assert s.intent_switch_confidence_threshold == 0.8


def test_rag_config_defaults():
    """RAG Hibrido (Onda 3, FASE 7, task 7.1.1/7.1.3) — defaults documentados
    em data-model.md §4 (mesmos usados por HybridRetriever, FASE 4)."""
    s = Settings()
    assert s.rag_embedding_model == "text-embedding-3-small"
    assert s.rag_limiar_abstencao == 0.45
    assert s.rag_k_vetorial == 20
    assert s.rag_k_textual == 20
    assert s.rag_top_k == 5
    assert s.rag_retrieval_timeout_seconds == 3.0
    assert s.rag_cache_enabled is False


def test_rag_config_override_via_env(monkeypatch):
    """Os 7 envs novos sao overridaveis (pydantic-settings env-driven, sem
    hardcode — FR-020)."""
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setenv("RAG_LIMIAR_ABSTENCAO", "0.6")
    monkeypatch.setenv("RAG_K_VETORIAL", "30")
    monkeypatch.setenv("RAG_K_TEXTUAL", "15")
    monkeypatch.setenv("RAG_TOP_K", "8")
    monkeypatch.setenv("RAG_RETRIEVAL_TIMEOUT_SECONDS", "5.5")
    monkeypatch.setenv("RAG_CACHE_ENABLED", "true")

    s = Settings()

    assert s.rag_embedding_model == "text-embedding-3-large"
    assert s.rag_limiar_abstencao == 0.6
    assert s.rag_k_vetorial == 30
    assert s.rag_k_textual == 15
    assert s.rag_top_k == 8
    assert s.rag_retrieval_timeout_seconds == 5.5
    assert s.rag_cache_enabled is True


def test_rag_config_types():
    """Tipos dos 7 campos novos (data-model.md §4): nunca str crua."""
    s = Settings()
    assert isinstance(s.rag_embedding_model, str)
    assert isinstance(s.rag_limiar_abstencao, float)
    assert isinstance(s.rag_k_vetorial, int)
    assert isinstance(s.rag_k_textual, int)
    assert isinstance(s.rag_top_k, int)
    assert isinstance(s.rag_retrieval_timeout_seconds, float)
    assert isinstance(s.rag_cache_enabled, bool)
