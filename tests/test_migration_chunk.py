"""
Testes da migration `chunk` (task 1.2.5, Onda 3 — RAG hibrido).

Sem Postgres real disponivel no ambiente de teste (mesmo padrao do restante
da suite — self-contained, sem servico externo). Cobre o que pode ser
validado sem uma conexao ao vivo:

- Cadeia de revisao (`revision`/`down_revision`) linkada corretamente ao head
  anterior (`d4b3c2a1f0e9`).
- `_extension_vector_disponivel()` isolada: sucesso/falha de `CREATE
  EXTENSION IF NOT EXISTS vector`.
- `upgrade()` simula o cenario pre-swap pgvector (research.md Decision 0):
  quando a extensao esta indisponivel, levanta `RuntimeError` CONTROLADO —
  nunca uma excecao inesperada no meio da criacao da tabela. Esse
  `RuntimeError` e exatamente o que `app/main.py:_run_alembic_upgrade`
  (chamado dentro de um bloco `try/except Exception: logger.exception(...)
  # continuando`, linha ~102-118) engole sem derrubar o boot — o MESMO
  padrao ja coberto por `tests/test_bootstrap.py` para o app como um todo.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "versions"
    / "e5f6a7b8c9d0_add_chunk_pgvector.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "chunk_migration_e5f6a7b8c9d0", _MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def migration():
    return _load_migration_module()


def test_revision_chain_correta(migration):
    """down_revision aponta para o head anterior (d4b3c2a1f0e9, add_contato_perfil)."""
    assert migration.revision == "e5f6a7b8c9d0"
    assert migration.down_revision == "d4b3c2a1f0e9"


def test_extension_disponivel_quando_execute_sucede(migration):
    conn = MagicMock()
    assert migration._extension_vector_disponivel(conn) is True
    conn.execute.assert_called_once()


def test_extension_indisponivel_quando_execute_falha(migration):
    """Simula postgres:16-alpine (sem pgvector) — CREATE EXTENSION falha."""
    conn = MagicMock()
    conn.execute.side_effect = Exception('extension "vector" is not available')
    assert migration._extension_vector_disponivel(conn) is False


def test_upgrade_nao_fatal_quando_extensao_indisponivel(migration, monkeypatch):
    """Cenario pre-swap pgvector (research.md Decision 0): upgrade() levanta
    RuntimeError CONTROLADO em vez de deixar a tabela pela metade ou propagar
    uma excecao de baixo nivel do driver — o chamador trata isso como
    'migration adiada', nao como corrupcao de schema."""
    conn = MagicMock()
    conn.execute.side_effect = Exception('extension "vector" is not available')
    monkeypatch.setattr(migration.op, "get_bind", lambda: conn)

    with pytest.raises(RuntimeError, match="indisponivel"):
        migration.upgrade()


def test_upgrade_prossegue_quando_extensao_disponivel(migration, monkeypatch):
    """Quando a extensao esta disponivel (pos-swap), upgrade() chama
    op.create_table/op.add_column/op.create_index em vez de abortar cedo."""
    conn = MagicMock()
    monkeypatch.setattr(migration.op, "get_bind", lambda: conn)

    create_table_calls = []
    add_column_calls = []
    create_index_calls = []
    monkeypatch.setattr(
        migration.op, "create_table",
        lambda *a, **kw: create_table_calls.append((a, kw)),
    )
    monkeypatch.setattr(
        migration.op, "add_column",
        lambda *a, **kw: add_column_calls.append((a, kw)),
    )
    monkeypatch.setattr(
        migration.op, "create_index",
        lambda *a, **kw: create_index_calls.append((a, kw)),
    )

    migration.upgrade()

    assert len(create_table_calls) == 1
    assert create_table_calls[0][0][0] == "chunk"
    assert len(add_column_calls) == 1  # search_vector gerada
    assert len(create_index_calls) == 3  # hnsw + gin + composto


def test_downgrade_remove_indices_e_tabela(migration, monkeypatch):
    drop_index_calls = []
    drop_table_calls = []
    monkeypatch.setattr(
        migration.op, "drop_index",
        lambda *a, **kw: drop_index_calls.append((a, kw)),
    )
    monkeypatch.setattr(
        migration.op, "drop_table",
        lambda *a, **kw: drop_table_calls.append((a, kw)),
    )

    migration.downgrade()

    assert len(drop_index_calls) == 3
    assert len(drop_table_calls) == 1
    assert drop_table_calls[0][0] == ("chunk",)
