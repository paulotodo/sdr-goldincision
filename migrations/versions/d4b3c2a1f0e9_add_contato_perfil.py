"""add contato.perfil (JSONB)

Revision ID: d4b3c2a1f0e9
Revises: c3a2b1d4e5f6
Create Date: 2026-06-29 23:00:00.000000

Perfil livre/incremental do lead: caracteristicas e preferencias arbitrarias
alem da qualificacao fixa, acumuladas entre turnos/tickets (anti-redundancia).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = 'd4b3c2a1f0e9'
down_revision: Union[str, Sequence[str], None] = 'c3a2b1d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'contato',
        sa.Column(
            'perfil',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column('contato', 'perfil')
