"""add chunk table (pgvector + full-text hibrido)

Revision ID: e5f6a7b8c9d0
Revises: d4b3c2a1f0e9
Create Date: 2026-07-01 19:10:00.000000

RAG hibrido (Onda 3): tabela `chunk` (objecao/faq/base) + indices HNSW
(vetorial) e GIN (full-text). PRE-CONDICAO DE INFRAESTRUTURA
(research.md Decision 0, FR-024-INFRA-PRECONDITION, dec-013): o Postgres
de producao ainda roda `postgres:16-alpine` (sem a extensao `vector`) no
momento em que esta migration foi escrita. `CREATE EXTENSION IF NOT
EXISTS vector` e portanto TOLERANTE A FALHA aqui — se a extensao nao
puder ser criada (imagem sem pgvector), o restante do upgrade e abortado
de forma controlada e NAO propaga excecao alem do que o chamador ja trata:
o MESMO padrao try/except nao-fatal ja usado em `app/main.py:102-118`
(`_run_alembic_upgrade` roda dentro de um bloco que loga e continua o
boot em caso de falha). Apos o operador trocar a imagem do servico
`sdr-whatsapp_postgres` para `pgvector/pgvector:pg16` (fora do escopo de
codigo desta feature — acao do operador, task 10.3.3), rodar `alembic
upgrade head` novamente completa a criacao da tabela/indices.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

# revision identifiers
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4b3c2a1f0e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _extension_vector_disponivel(conn) -> bool:
    """Tenta criar a extensao `vector`; retorna False (nao-fatal) se indisponivel
    na imagem atual do Postgres (pre-swap, research.md Decision 0)."""
    try:
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        return True
    except Exception:
        # Tolerante a falha: extensao nao disponivel na imagem atual
        # (postgres:16-alpine, sem pgvector). Nao interrompe o restante
        # do `alembic upgrade head` — apenas esta migration fica incompleta
        # ate o operador trocar a imagem (task 10.3.3) e rodar upgrade de novo.
        return False


def upgrade() -> None:
    conn = op.get_bind()

    if not _extension_vector_disponivel(conn):
        # Sem a extensao `vector`, a coluna `embedding` (Vector(1536)) nao
        # pode ser criada. Aborta esta migration de forma NAO-FATAL: quem
        # chama (app/main.py:_run_alembic_upgrade, dentro de um try/except
        # que loga e continua) trata a excecao sem derrubar o boot. Rodar
        # novamente apos o swap de imagem completa a criacao da tabela.
        raise RuntimeError(
            "chunk migration: extensao 'vector' indisponivel nesta imagem "
            "do Postgres (pre-swap pgvector/pgvector:pg16, ver research.md "
            "Decision 0) — upgrade desta revisao adiado, sem quebrar o boot."
        )

    op.create_table(
        'chunk',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column(
            'curso_id', sa.BigInteger(),
            sa.ForeignKey('curso.id', ondelete='CASCADE'), nullable=True,
        ),
        sa.Column('tipo', sa.Text(), nullable=False),
        sa.Column('idioma', sa.Text(), nullable=False),
        sa.Column('conteudo', sa.Text(), nullable=False),
        sa.Column('fonte_tabela', sa.Text(), nullable=False),
        sa.Column('fonte_id', sa.BigInteger(), nullable=False),
        sa.Column('embedding', Vector(1536), nullable=True),
        sa.Column('ativo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            'criado_em', sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column('atualizado_em', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('fonte_tabela', 'fonte_id', 'idioma', name='uq_chunk_fonte'),
        sa.CheckConstraint("tipo IN ('objecao','faq','base')", name='ck_chunk_tipo'),
        sa.CheckConstraint("idioma IN ('pt','en','es')", name='ck_chunk_idioma'),
    )

    # Coluna GERADA (STORED) — tsvector por idioma via CASE (data-model.md §1).
    # `add_column` com `sa.Computed(...)` emite a clausula `GENERATED ALWAYS AS
    # (...) STORED` no DDL do Postgres.
    op.add_column(
        'chunk',
        sa.Column(
            'search_vector',
            TSVECTOR(),
            sa.Computed(
                """
                to_tsvector(
                    CASE idioma
                        WHEN 'en' THEN 'english'::regconfig
                        WHEN 'es' THEN 'spanish'::regconfig
                        ELSE 'portuguese'::regconfig
                    END,
                    conteudo
                )
                """,
                persisted=True,
            ),
            nullable=True,
        ),
    )

    op.create_index(
        'ix_chunk_embedding_hnsw', 'chunk', ['embedding'],
        unique=False, postgresql_using='hnsw',
        postgresql_with={'m': 16, 'ef_construction': 64},
        postgresql_ops={'embedding': 'vector_cosine_ops'},
    )
    op.create_index(
        'ix_chunk_search_vector', 'chunk', ['search_vector'],
        unique=False, postgresql_using='gin',
    )
    op.create_index(
        'ix_chunk_curso_idioma_ativo', 'chunk',
        ['curso_id', 'idioma', 'ativo'], unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_chunk_curso_idioma_ativo', table_name='chunk')
    op.drop_index('ix_chunk_search_vector', table_name='chunk')
    op.drop_index('ix_chunk_embedding_hnsw', table_name='chunk')
    op.drop_table('chunk')
