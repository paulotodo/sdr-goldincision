"""chunk: identidade estavel por conteudo (conteudo_hash + uq_chunk_conteudo)

Revision ID: f7a1b2c3d4e5
Revises: e5f6a7b8c9d0
Create Date: 2026-07-01 21:40:00.000000

Fix de raiz do "seed-churn": o `run_seed` faz delete+insert de faq/objecao a
cada boot, gerando `fonte_id`s seriais NOVOS; como o `chunk` era chaveado por
`fonte_id`, cada boot criava chunks novos e tombstonava os antigos (crescimento
ilimitado + re-embedding). Esta migration adiciona `conteudo_hash` (sha256 do
`conteudo`) e a UNIQUE (fonte_tabela, idioma, conteudo_hash), tornando o chunk
identificado por CONTEUDO — conteudo inalterado => mesmo chunk entre boots.

Seguranca do backfill em producao (114 chunks existentes):
  1. Adiciona a coluna NULLABLE.
  2. Backfill do sha256 em Python (sem exigir pgcrypto).
  3. Dedup por (fonte_tabela, idioma, conteudo_hash) mantendo o MENOR id
     (remove chunks de conteudo identico — dedup desejavel; preserva embedding
     do sobrevivente) ANTES de criar a UNIQUE, para nao falhar.
  4. Cria a UNIQUE e torna a coluna NOT NULL.

Tolerante: se a tabela `chunk` ainda nao existe (ex.: CI sem a extensao
`vector`, onde a migration e5f6a7b8c9d0 abortou de forma controlada), esta
migration e no-op — a tabela sera criada ja com o schema novo via
`Base.metadata.create_all` (testes) ou por um upgrade posterior com pgvector.
"""
import hashlib
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "f7a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tem_tabela_chunk(conn) -> bool:
    return sa.inspect(conn).has_table("chunk")


def _tem_coluna(conn, coluna: str) -> bool:
    return any(c["name"] == coluna for c in sa.inspect(conn).get_columns("chunk"))


def _tem_constraint(conn, nome: str) -> bool:
    ucs = sa.inspect(conn).get_unique_constraints("chunk")
    return any(uc.get("name") == nome for uc in ucs)


def upgrade() -> None:
    conn = op.get_bind()
    if not _tem_tabela_chunk(conn):
        # chunk ainda nao criada (pre-swap pgvector / CI sem extensao) — no-op.
        return

    # 1. coluna nullable
    if not _tem_coluna(conn, "conteudo_hash"):
        op.add_column("chunk", sa.Column("conteudo_hash", sa.Text(), nullable=True))

    # 2. backfill sha256 (Python — sem depender de pgcrypto)
    rows = conn.execute(
        sa.text("SELECT id, conteudo FROM chunk WHERE conteudo_hash IS NULL")
    ).fetchall()
    for row in rows:
        h = hashlib.sha256((row.conteudo or "").encode("utf-8")).hexdigest()
        conn.execute(
            sa.text("UPDATE chunk SET conteudo_hash = :h WHERE id = :i"),
            {"h": h, "i": row.id},
        )

    # 3. dedup por (fonte_tabela, idioma, conteudo_hash) mantendo o menor id
    #    (conteudo identico == chunk duplicado; o sobrevivente preserva embedding)
    conn.execute(
        sa.text(
            """
            DELETE FROM chunk a
            USING chunk b
            WHERE a.fonte_tabela = b.fonte_tabela
              AND a.idioma = b.idioma
              AND a.conteudo_hash = b.conteudo_hash
              AND a.id > b.id
            """
        )
    )

    # 4. UNIQUE + NOT NULL
    if not _tem_constraint(conn, "uq_chunk_conteudo"):
        op.create_unique_constraint(
            "uq_chunk_conteudo", "chunk", ["fonte_tabela", "idioma", "conteudo_hash"]
        )
    op.alter_column("chunk", "conteudo_hash", existing_type=sa.Text(), nullable=False)


def downgrade() -> None:
    conn = op.get_bind()
    if not _tem_tabela_chunk(conn):
        return
    if _tem_constraint(conn, "uq_chunk_conteudo"):
        op.drop_constraint("uq_chunk_conteudo", "chunk", type_="unique")
    if _tem_coluna(conn, "conteudo_hash"):
        op.drop_column("chunk", "conteudo_hash")
