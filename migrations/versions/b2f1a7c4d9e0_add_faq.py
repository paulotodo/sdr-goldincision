"""add faq table

Revision ID: b2f1a7c4d9e0
Revises: ea9e306666b0
Create Date: 2026-06-28 23:30:00.000000

Tabela FAQ Oficial (global, nao por curso) — consultada na hierarquia
Mapa Mestre -> Base -> Objecoes -> FAQ.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = 'b2f1a7c4d9e0'
down_revision: Union[str, Sequence[str], None] = 'ea9e306666b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'faq',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column(
            'idioma', sa.Text(),
            sa.CheckConstraint("idioma IN ('pt','en','es')", name='ck_faq_idioma'),
            nullable=False, server_default='pt',
        ),
        sa.Column('secao', sa.Text(), nullable=True),
        sa.Column('pergunta', sa.Text(), nullable=False),
        sa.Column('resposta', sa.Text(), nullable=False),
        sa.Column('ativo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('idioma', 'pergunta', name='uq_faq_idioma_pergunta'),
    )
    op.create_index('ix_faq_idioma', 'faq', ['idioma'])


def downgrade() -> None:
    op.drop_index('ix_faq_idioma', table_name='faq')
    op.drop_table('faq')
