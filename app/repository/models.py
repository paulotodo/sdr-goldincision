"""
Modelos SQLAlchemy (ORM) para PostgreSQL — schema do sdr-whatsapp.

Entidades (data-model.md):
- Contato: identidade persistente do lead por numero WhatsApp
- Ticket: unidade de atendimento (chamadoId)
- SessaoConversa: historico + resumo rolante por ticket (1:1)
- Mensagem: historico append-only
- Curso: catalogo gerido por admin API
- CursoApresentacao: texto verbatim por idioma
- CursoObjecao: banco de objecoes oficial por curso/idioma
- CursoTurma: instancias presenciais
- CursoLink: links de inscricao por idioma
- CursoMidia: midias por curso
- EventoLog: observabilidade estruturada

Convencao: colunas em snake_case; DTOs Pydantic em camelCase (via mapper.py).
Implementacao completa: FASE 2, task 2.1.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Contato(Base):
    """Identidade persistente do lead por numero WhatsApp."""
    __tablename__ = "contato"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    numero: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    contact_id_externo: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    nome: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    idioma: Mapped[Optional[str]] = mapped_column(
        Text,
        CheckConstraint("idioma IN ('pt','en','es')", name="ck_contato_idioma"),
        nullable=True,
    )
    eh_medico: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    especialidade: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    experiencia_corporal: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    produto_interesse: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    etapa_funil: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    tickets: Mapped[list["Ticket"]] = relationship("Ticket", back_populates="contato")
    sessoes: Mapped[list["SessaoConversa"]] = relationship(
        "SessaoConversa", back_populates="contato"
    )


class Ticket(Base):
    """Unidade de atendimento (chamadoId). Estado de fluxo por ticket."""
    __tablename__ = "ticket"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chamado_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    contato_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("contato.id"), nullable=False
    )
    company_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    queue_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    whatsapp_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    caminho_atual: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        CheckConstraint("caminho_atual BETWEEN 1 AND 6", name="ck_ticket_caminho"),
        nullable=True,
    )
    etapa_mapa_mestre: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text,
        CheckConstraint(
            "status IN ('aberto','em_handoff','encerrado')", name="ck_ticket_status"
        ),
        server_default="aberto",
        nullable=False,
    )
    handoff_motivo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    handoff_destino: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    contato: Mapped["Contato"] = relationship("Contato", back_populates="tickets")
    sessao: Mapped[Optional["SessaoConversa"]] = relationship(
        "SessaoConversa", back_populates="ticket", uselist=False
    )


class SessaoConversa(Base):
    """Container do historico + resumo rolante de um ticket (1:1 com ticket)."""
    __tablename__ = "sessao_conversa"
    __table_args__ = (UniqueConstraint("ticket_id", name="uq_sessao_ticket"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ticket.id"), unique=True, nullable=False
    )
    contato_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("contato.id"), nullable=False
    )
    resumo_rolante: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resumo_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ultima_atualizacao_resumo: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ticket: Mapped["Ticket"] = relationship("Ticket", back_populates="sessao")
    contato: Mapped["Contato"] = relationship("Contato")
    mensagens: Mapped[list["Mensagem"]] = relationship(
        "Mensagem", back_populates="sessao"
    )


class Mensagem(Base):
    """Historico append-only de mensagens por sessao."""
    __tablename__ = "mensagem"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sessao_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sessao_conversa.id"), nullable=False
    )
    direcao: Mapped[str] = mapped_column(
        Text,
        CheckConstraint("direcao IN ('inbound','outbound')", name="ck_msg_direcao"),
        nullable=False,
    )
    tipo: Mapped[str] = mapped_column(
        Text,
        CheckConstraint(
            "tipo IN ('text','audio','video','image','document')", name="ck_msg_tipo"
        ),
        nullable=False,
    )
    conteudo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    wid: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    transcrito: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sessao: Mapped["SessaoConversa"] = relationship(
        "SessaoConversa", back_populates="mensagens"
    )


class Curso(Base):
    """Item do catalogo gerido pela API de admin. Cursos sao DADOS (Principio VII)."""
    __tablename__ = "curso"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[str] = mapped_column(
        Text,
        CheckConstraint(
            "tipo IN ('online','presencial','licenciamento','franquia')",
            name="ck_curso_tipo",
        ),
        nullable=False,
    )
    caminho_mapa_mestre: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        CheckConstraint(
            "caminho_mapa_mestre BETWEEN 1 AND 6", name="ck_curso_caminho"
        ),
        nullable=True,
    )
    elegibilidade: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    apresentacoes: Mapped[list["CursoApresentacao"]] = relationship(
        "CursoApresentacao", back_populates="curso"
    )
    objecoes: Mapped[list["CursoObjecao"]] = relationship(
        "CursoObjecao", back_populates="curso"
    )
    turmas: Mapped[list["CursoTurma"]] = relationship(
        "CursoTurma", back_populates="curso"
    )
    links: Mapped[list["CursoLink"]] = relationship(
        "CursoLink", back_populates="curso"
    )
    midias: Mapped[list["CursoMidia"]] = relationship(
        "CursoMidia", back_populates="curso"
    )


class CursoApresentacao(Base):
    """Texto oficial verbatim por idioma — enviado na integra, nunca reescrito (FR-010)."""
    __tablename__ = "curso_apresentacao"
    __table_args__ = (
        UniqueConstraint("curso_id", "idioma", name="uq_apresentacao_curso_idioma"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    curso_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("curso.id"), nullable=False
    )
    idioma: Mapped[str] = mapped_column(
        Text,
        CheckConstraint("idioma IN ('pt','en','es')", name="ck_apres_idioma"),
        nullable=False,
    )
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    curso: Mapped["Curso"] = relationship("Curso", back_populates="apresentacoes")


class CursoObjecao(Base):
    """Banco de objecoes oficial — UNICA fonte para respostas a objecoes (FR-011)."""
    __tablename__ = "curso_objecao"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    curso_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("curso.id"), nullable=False
    )
    idioma: Mapped[str] = mapped_column(
        Text,
        CheckConstraint("idioma IN ('pt','en','es')", name="ck_objecao_idioma"),
        nullable=False,
    )
    objecao: Mapped[str] = mapped_column(Text, nullable=False)
    resposta: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    curso: Mapped["Curso"] = relationship("Curso", back_populates="objecoes")


class CursoTurma(Base):
    """Instancia de curso presencial."""
    __tablename__ = "curso_turma"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    curso_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("curso.id"), nullable=False
    )
    cidade: Mapped[str] = mapped_column(Text, nullable=False)
    pais: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    data_inicio: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    capacidade: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    vagas_disponiveis: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    lote_preco: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)

    curso: Mapped["Curso"] = relationship("Curso", back_populates="turmas")


class CursoLink(Base):
    """Links de inscricao por idioma (US1-AS3, US5-AS4)."""
    __tablename__ = "curso_link"
    __table_args__ = (
        UniqueConstraint("curso_id", "idioma", name="uq_link_curso_idioma"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    curso_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("curso.id"), nullable=False
    )
    idioma: Mapped[str] = mapped_column(
        Text,
        CheckConstraint("idioma IN ('pt','en','es')", name="ck_link_idioma"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)

    curso: Mapped["Curso"] = relationship("Curso", back_populates="links")


class CursoMidia(Base):
    """Midias associadas ao curso (imagem/audio/video/documento)."""
    __tablename__ = "curso_midia"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    curso_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("curso.id"), nullable=False
    )
    idioma: Mapped[Optional[str]] = mapped_column(
        Text,
        CheckConstraint(
            "idioma IS NULL OR idioma IN ('pt','en','es')", name="ck_midia_idioma"
        ),
        nullable=True,
    )
    tipo: Mapped[str] = mapped_column(
        Text,
        CheckConstraint(
            "tipo IN ('image','audio','video','document')", name="ck_midia_tipo"
        ),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    legenda: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    curso: Mapped["Curso"] = relationship("Curso", back_populates="midias")


class EventoLog(Base):
    """Persistencia de eventos estruturados para metricas (FR-033/034)."""
    __tablename__ = "evento_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    contact_number: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)  # webhook_in/llm_call/etc
    stage: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model_used: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tokens_in: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    detalhe: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Faq(Base):
    """FAQ Oficial — consultada na hierarquia (Mapa Mestre -> Base -> Objecoes -> FAQ).

    Global (nao por curso). Seedada do FAQ.docx; idioma 'pt' por padrao.
    """
    __tablename__ = "faq"
    __table_args__ = (
        UniqueConstraint("idioma", "pergunta", name="uq_faq_idioma_pergunta"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    idioma: Mapped[str] = mapped_column(
        Text,
        CheckConstraint("idioma IN ('pt','en','es')", name="ck_faq_idioma"),
        nullable=False,
        default="pt",
    )
    secao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pergunta: Mapped[str] = mapped_column(Text, nullable=False)
    resposta: Mapped[str] = mapped_column(Text, nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
