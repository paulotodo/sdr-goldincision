"""
Testes do modelo `Chunk` (task 1.1.3, Onda 3 — RAG hibrido).

Verifica as constraints do data-model.md §1 sem exigir Postgres real:
- UniqueConstraint(fonte_tabela, fonte_id, idioma) rejeita duplicata
- CheckConstraint tipo IN ('objecao','faq','base') rejeita valor invalido
- CheckConstraint idioma IN ('pt','en','es') rejeita valor invalido
- Insercao valida funciona

Usa SQLite em memoria (self-contained, sem servico externo — mesmo padrao
"suite self-contained" do restante do projeto). `search_vector` usa
`.with_variant(Text(), "sqlite")` (ver models.py) para permitir a criacao da
tabela sob o dialeto SQLite nos testes, preservando TSVECTOR real em Postgres.
UniqueConstraint/CheckConstraint sao nativamente suportadas pelo SQLite, entao
a validacao aqui e equivalente ao comportamento real do Postgres.
"""
from __future__ import annotations

import itertools

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.repository.models import Base, Chunk


@pytest.fixture()
def session():
    """Sessao SQLAlchemy sincrona contra SQLite em memoria, so com a tabela chunk."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Chunk.__table__])
    with Session(engine) as s:
        yield s
    engine.dispose()


_id_counter = itertools.count(1)


def _novo_chunk(**overrides):
    # BigInteger primary_key nao ganha rowid-alias automatico do SQLite (so
    # `Integer` puro tem esse comportamento) — id explicito e necessario aqui;
    # em Postgres real a sequence continua responsavel por isso normalmente.
    defaults = dict(
        id=next(_id_counter),
        curso_id=None,
        tipo="faq",
        idioma="pt",
        conteudo="Pergunta e resposta oficial de exemplo.",
        fonte_tabela="faq",
        fonte_id=1,
        ativo=True,
    )
    defaults.update(overrides)
    return Chunk(**defaults)


def test_chunk_insercao_valida_funciona(session):
    """Insercao com todos os campos validos persiste sem erro."""
    session.add(_novo_chunk())
    session.commit()

    persisted = session.query(Chunk).one()
    assert persisted.tipo == "faq"
    assert persisted.idioma == "pt"
    assert persisted.ativo is True


def test_chunk_unique_fonte_rejeita_duplicata(session):
    """UNIQUE(fonte_tabela, fonte_id, idioma) rejeita 2a linha com mesma tripla."""
    session.add(_novo_chunk(fonte_tabela="faq", fonte_id=42, idioma="pt"))
    session.commit()

    session.add(_novo_chunk(fonte_tabela="faq", fonte_id=42, idioma="pt", conteudo="outro texto"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_chunk_unique_fonte_permite_mesmo_fonte_id_idioma_diferente(session):
    """A mesma (fonte_tabela, fonte_id) em idiomas diferentes NAO viola o UNIQUE
    (uma linha por idioma e o desenho esperado, ex: objecao pt + objecao en)."""
    session.add(_novo_chunk(fonte_tabela="curso_objecao", fonte_id=7, idioma="pt"))
    session.add(_novo_chunk(fonte_tabela="curso_objecao", fonte_id=7, idioma="en"))
    session.commit()

    assert session.query(Chunk).count() == 2


def test_chunk_conteudo_hash_preenchido_automaticamente(session):
    """`conteudo_hash` e populado no before_insert (models.py) a partir do texto."""
    from app.repository.models import chunk_conteudo_hash

    c = _novo_chunk(fonte_id=500, conteudo="Texto oficial X")
    session.add(c)
    session.commit()
    assert c.conteudo_hash == chunk_conteudo_hash("Texto oficial X")


def test_chunk_unique_conteudo_rejeita_mesmo_conteudo_mesma_fonte_idioma(session):
    """UNIQUE(fonte_tabela, idioma, conteudo_hash): mesmo conteudo na mesma
    fonte/idioma e rejeitado mesmo com `fonte_id` diferente (identidade por
    conteudo — evita o churn: re-seed com id novo NAO duplica o chunk)."""
    session.add(_novo_chunk(fonte_tabela="faq", fonte_id=1, idioma="pt", conteudo="mesma coisa"))
    with pytest.raises(IntegrityError):
        session.add(_novo_chunk(fonte_tabela="faq", fonte_id=2, idioma="pt", conteudo="mesma coisa"))
        session.commit()
    session.rollback()


def test_chunk_tipo_invalido_rejeitado(session):
    """CHECK tipo IN ('objecao','faq','base') rejeita valor fora do enum."""
    session.add(_novo_chunk(tipo="invalido"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


@pytest.mark.parametrize("tipo_valido", ["objecao", "faq", "base"])
def test_chunk_tipo_valido_aceito(session, tipo_valido):
    session.add(_novo_chunk(tipo=tipo_valido, fonte_tabela=tipo_valido, fonte_id=1))
    session.commit()
    assert session.query(Chunk).filter_by(tipo=tipo_valido).count() == 1


def test_chunk_idioma_invalido_rejeitado(session):
    """CHECK idioma IN ('pt','en','es') rejeita valor fora do enum."""
    session.add(_novo_chunk(idioma="fr"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


@pytest.mark.parametrize("idioma_valido", ["pt", "en", "es"])
def test_chunk_idioma_valido_aceito(session, idioma_valido):
    session.add(_novo_chunk(idioma=idioma_valido, fonte_id=99))
    session.commit()
    assert session.query(Chunk).filter_by(idioma=idioma_valido).count() == 1


def test_chunk_embedding_aceita_none(session):
    """embedding e nullable ateh o rag_seed calcular (FR-009)."""
    session.add(_novo_chunk(fonte_id=200))
    session.commit()

    persisted = session.query(Chunk).one()
    assert persisted.embedding is None
