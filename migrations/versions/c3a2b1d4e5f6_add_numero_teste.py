"""add numero_teste table

Revision ID: c3a2b1d4e5f6
Revises: b2f1a7c4d9e0
Create Date: 2026-06-29 02:00:00.000000

Tabela de numeros autorizados ao comando de reset de jornada (#reset).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = 'c3a2b1d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'b2f1a7c4d9e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'numero_teste',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('numero', sa.Text(), nullable=False, unique=True),
        sa.Column('descricao', sa.Text(), nullable=True),
        sa.Column('ativo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_numero_teste_numero', 'numero_teste', ['numero'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_numero_teste_numero', table_name='numero_teste')
    op.drop_table('numero_teste')
