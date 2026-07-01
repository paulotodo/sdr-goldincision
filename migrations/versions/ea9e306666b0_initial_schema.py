"""initial_schema

Revision ID: ea9e306666b0
Revises:
Create Date: 2026-06-28 02:04:54.669196

Schema inicial do sdr-whatsapp (data-model.md).
Entidades: contato, ticket, sessao_conversa, mensagem, curso,
           curso_apresentacao, curso_objecao, curso_turma, curso_link,
           curso_midia, evento_log.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = 'ea9e306666b0'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- contato ----
    op.create_table(
        'contato',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('numero', sa.Text(), nullable=False, unique=True),
        sa.Column('contact_id_externo', sa.BigInteger(), nullable=True),
        sa.Column('nome', sa.Text(), nullable=True),
        sa.Column('idioma', sa.Text(), sa.CheckConstraint("idioma IN ('pt','en','es')", name='ck_contato_idioma'), nullable=True),
        sa.Column('eh_medico', sa.Boolean(), nullable=True),
        sa.Column('especialidade', sa.Text(), nullable=True),
        sa.Column('experiencia_corporal', sa.Boolean(), nullable=True),
        sa.Column('produto_interesse', sa.Text(), nullable=True),
        sa.Column('etapa_funil', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_contato_numero', 'contato', ['numero'], unique=True)

    # ---- ticket ----
    op.create_table(
        'ticket',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('chamado_id', sa.BigInteger(), nullable=False, unique=True),
        sa.Column('contato_id', sa.BigInteger(), sa.ForeignKey('contato.id'), nullable=False),
        sa.Column('company_id', sa.BigInteger(), nullable=True),
        sa.Column('queue_id', sa.BigInteger(), nullable=True),
        sa.Column('whatsapp_id', sa.BigInteger(), nullable=True),
        sa.Column('caminho_atual', sa.SmallInteger(), sa.CheckConstraint("caminho_atual BETWEEN 1 AND 6", name='ck_ticket_caminho'), nullable=True),
        sa.Column('etapa_mapa_mestre', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), sa.CheckConstraint("status IN ('aberto','em_handoff','encerrado')", name='ck_ticket_status'), server_default='aberto', nullable=False),
        sa.Column('handoff_motivo', sa.Text(), nullable=True),
        sa.Column('handoff_destino', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_ticket_chamado_id', 'ticket', ['chamado_id'], unique=True)
    op.create_index('ix_ticket_contato_id', 'ticket', ['contato_id'])

    # ---- sessao_conversa ----
    op.create_table(
        'sessao_conversa',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('ticket_id', sa.BigInteger(), sa.ForeignKey('ticket.id'), unique=True, nullable=False),
        sa.Column('contato_id', sa.BigInteger(), sa.ForeignKey('contato.id'), nullable=False),
        sa.Column('resumo_rolante', sa.Text(), nullable=True),
        sa.Column('resumo_tokens', sa.Integer(), nullable=True),
        sa.Column('ultima_atualizacao_resumo', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('ticket_id', name='uq_sessao_ticket'),
    )

    # ---- mensagem ----
    op.create_table(
        'mensagem',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('sessao_id', sa.BigInteger(), sa.ForeignKey('sessao_conversa.id'), nullable=False),
        sa.Column('direcao', sa.Text(), sa.CheckConstraint("direcao IN ('inbound','outbound')", name='ck_msg_direcao'), nullable=False),
        sa.Column('tipo', sa.Text(), sa.CheckConstraint("tipo IN ('text','audio','video','image','document')", name='ck_msg_tipo'), nullable=False),
        sa.Column('conteudo', sa.Text(), nullable=True),
        sa.Column('media_url', sa.Text(), nullable=True),
        sa.Column('wid', sa.Text(), nullable=True),
        sa.Column('transcrito', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_mensagem_sessao_id', 'mensagem', ['sessao_id'])

    # ---- curso ----
    op.create_table(
        'curso',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('slug', sa.Text(), unique=True, nullable=False),
        sa.Column('nome', sa.Text(), nullable=False),
        sa.Column('tipo', sa.Text(), sa.CheckConstraint("tipo IN ('online','presencial','licenciamento','franquia')", name='ck_curso_tipo'), nullable=False),
        sa.Column('caminho_mapa_mestre', sa.SmallInteger(), sa.CheckConstraint("caminho_mapa_mestre BETWEEN 1 AND 6", name='ck_curso_caminho'), nullable=True),
        sa.Column('elegibilidade', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('ativo', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_curso_slug', 'curso', ['slug'], unique=True)
    op.create_index('ix_curso_ativo', 'curso', ['ativo'])

    # ---- curso_apresentacao ----
    op.create_table(
        'curso_apresentacao',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('curso_id', sa.BigInteger(), sa.ForeignKey('curso.id'), nullable=False),
        sa.Column('idioma', sa.Text(), sa.CheckConstraint("idioma IN ('pt','en','es')", name='ck_apres_idioma'), nullable=False),
        sa.Column('texto', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('curso_id', 'idioma', name='uq_apresentacao_curso_idioma'),
    )

    # ---- curso_objecao ----
    op.create_table(
        'curso_objecao',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('curso_id', sa.BigInteger(), sa.ForeignKey('curso.id'), nullable=False),
        sa.Column('idioma', sa.Text(), sa.CheckConstraint("idioma IN ('pt','en','es')", name='ck_objecao_idioma'), nullable=False),
        sa.Column('objecao', sa.Text(), nullable=False),
        sa.Column('resposta', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_objecao_curso_idioma', 'curso_objecao', ['curso_id', 'idioma'])

    # ---- curso_turma ----
    op.create_table(
        'curso_turma',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('curso_id', sa.BigInteger(), sa.ForeignKey('curso.id'), nullable=False),
        sa.Column('cidade', sa.Text(), nullable=False),
        sa.Column('pais', sa.Text(), nullable=True),
        sa.Column('data_inicio', sa.Date(), nullable=True),
        sa.Column('capacidade', sa.Integer(), nullable=True),
        sa.Column('vagas_disponiveis', sa.Integer(), nullable=True),
        sa.Column('lote_preco', sa.Text(), nullable=True),
        sa.Column('ativo', sa.Boolean(), server_default='true', nullable=False),
    )

    # ---- curso_link ----
    op.create_table(
        'curso_link',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('curso_id', sa.BigInteger(), sa.ForeignKey('curso.id'), nullable=False),
        sa.Column('idioma', sa.Text(), sa.CheckConstraint("idioma IN ('pt','en','es')", name='ck_link_idioma'), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.UniqueConstraint('curso_id', 'idioma', name='uq_link_curso_idioma'),
    )

    # ---- curso_midia ----
    op.create_table(
        'curso_midia',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('curso_id', sa.BigInteger(), sa.ForeignKey('curso.id'), nullable=False),
        sa.Column('idioma', sa.Text(), sa.CheckConstraint("idioma IS NULL OR idioma IN ('pt','en','es')", name='ck_midia_idioma'), nullable=True),
        sa.Column('tipo', sa.Text(), sa.CheckConstraint("tipo IN ('image','audio','video','document')", name='ck_midia_tipo'), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('legenda', sa.Text(), nullable=True),
    )

    # ---- evento_log ----
    op.create_table(
        'evento_log',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('ticket_id', sa.BigInteger(), nullable=True),
        sa.Column('contact_number', sa.Text(), nullable=True),
        sa.Column('tipo', sa.Text(), nullable=False),
        sa.Column('stage', sa.Text(), nullable=True),
        sa.Column('model_used', sa.Text(), nullable=True),
        sa.Column('tokens_in', sa.Integer(), nullable=True),
        sa.Column('tokens_out', sa.Integer(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('detalhe', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_evento_log_ticket_id', 'evento_log', ['ticket_id'])
    op.create_index('ix_evento_log_created_at', 'evento_log', ['created_at'])


def downgrade() -> None:
    # Remover em ordem inversa de dependencia
    op.drop_table('evento_log')
    op.drop_table('curso_midia')
    op.drop_table('curso_link')
    op.drop_table('curso_turma')
    op.drop_table('curso_objecao')
    op.drop_table('curso_apresentacao')
    op.drop_table('curso')
    op.drop_table('mensagem')
    op.drop_table('sessao_conversa')
    op.drop_table('ticket')
    op.drop_table('contato')
