"""
Seed idempotente dos 6 cursos da GoldIncision (FR-027, US4-AS7).

Popula o catalogo inicial a partir dos documentos em knowledge_base/documentos_agente/.
Re-execucao nao duplica entradas (upsert por slug via ON CONFLICT DO UPDATE).

Cursos seedados:
1. Curso Online Harmonizacao Glutea          (slug: curso-online-hg)
2. HG Modulo 1                               (slug: hg-modulo-1)
3. HG360 Sao Paulo                           (slug: hg360-sp)
4. HG360 Barcelona                           (slug: hg360-barcelona)
5. Licenciamento Internacional GoldIncision  (slug: licenciamento-internacional)
6. Franquia GoldIncision                     (slug: franquia-goldincision)

Conteudo extraido dos documentos:
- Apresentacoes (.docx/.pdf) → CursoApresentacao (idioma pt/en/es)
- Banco de Objecoes (.docx) → CursoObjecao (idioma pt)
- Nomes de arquivo com acentos conforme listados em knowledge_base/

Invariante: nao inventa dados — usa SOMENTE o que esta nos documentos oficiais.
Campos de preco/datas: NAO seedados aqui — preenchidos via admin API pelo operador.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.repository.models import Curso, CursoApresentacao, CursoObjecao

logger = logging.getLogger(__name__)

# Caminho absoluto da base de conhecimento (relativo a raiz do projeto)
KNOWLEDGE_BASE_PATH = Path(__file__).parent.parent / "knowledge_base" / "documentos_agente"

# ---------------------------------------------------------------------------
# Metadados estruturais dos 6 cursos (sem dados de negocio — preco/datas
# sao preenchidos via admin API pelo operador apos o seed)
# ---------------------------------------------------------------------------
CURSOS_SEED: list[dict] = [
    {
        "slug": "curso-online-hg",
        "nome": "Curso Online Harmonizacao Glutea",
        "tipo": "online",
        "caminho_mapa_mestre": 1,
        "elegibilidade": {"medico": True},
        # Nomes de arquivo com acentos conforme filesystem (ls knowledge_base/)
        "arquivo_pt": "Harmonização Glútea On-line.docx",
        "arquivo_objecoes_pt": "Banco de Objeções Curso Harm Glútea on-line.docx",
        "arquivo_en": None,
        "arquivo_es": None,
    },
    {
        "slug": "hg-modulo-1",
        "nome": "HG Modulo 1",
        "tipo": "presencial",
        "caminho_mapa_mestre": 2,
        "elegibilidade": {"medico": True, "corporal": True},
        "arquivo_pt": "Harmonização Glútea 1.docx",
        "arquivo_objecoes_pt": "Banco de Objeções Curso Harm Glútea 1.docx",
        "arquivo_en": None,
        "arquivo_es": None,
    },
    {
        "slug": "hg360-sp",
        "nome": "HG360 Sao Paulo",
        "tipo": "presencial",
        "caminho_mapa_mestre": 3,
        "elegibilidade": {"medico": True, "corporal": True},
        "arquivo_pt": "Harmonização Glútea 2 São Paulo.docx",
        "arquivo_objecoes_pt": "Banco de Objeções Curso Harm Glútea 2.docx",
        "arquivo_en": None,
        "arquivo_es": None,
    },
    {
        "slug": "hg360-barcelona",
        "nome": "HG360 Barcelona",
        "tipo": "presencial",
        "caminho_mapa_mestre": 4,
        "elegibilidade": {"medico": True, "corporal": True},
        "arquivo_pt": "Harmonização Glútea 360º Barcelona.docx",
        "arquivo_objecoes_pt": "Banco de Objeções Curso Harm Glútea 360º Bacelona.docx",
        "arquivo_en": None,
        "arquivo_es": None,
    },
    {
        "slug": "licenciamento-internacional",
        "nome": "Licenciamento Internacional GoldIncision",
        "tipo": "licenciamento",
        "caminho_mapa_mestre": 6,
        "elegibilidade": {},
        # Apresentacoes disponiveis em 3 idiomas (PDF)
        "arquivo_pt": "Apres Lic Internac Gold PORT.pdf",
        "arquivo_en": "Apres Lic Internac Gold ING.pdf",
        "arquivo_es": "Apres Lic Internac Gold ESP.pdf",
        "arquivo_objecoes_pt": None,
    },
    {
        "slug": "franquia-goldincision",
        "nome": "Franquia GoldIncision",
        "tipo": "franquia",
        "caminho_mapa_mestre": 6,
        "elegibilidade": {},
        "arquivo_pt": None,
        "arquivo_en": None,
        "arquivo_es": None,
        "arquivo_objecoes_pt": None,
    },
]


# ---------------------------------------------------------------------------
# Extracao de texto dos documentos
# ---------------------------------------------------------------------------

def _extract_text_docx(path: Path) -> Optional[str]:
    """Extrai texto de arquivo .docx usando python-docx. Retorna None se indisponivel."""
    try:
        import docx  # python-docx
        doc = docx.Document(str(path))
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs) if paragraphs else None
    except ImportError:
        logger.warning("seed: python-docx nao disponivel — apresentacoes .docx nao extraidas")
        return None
    except Exception as exc:
        logger.warning("seed: erro ao ler %s: %s", path.name, exc)
        return None


def _extract_text_pdf(path: Path) -> Optional[str]:
    """
    Extrai texto de arquivo .pdf.
    Tenta pypdf (pip install pypdf); se indisponivel retorna None graciosamente.
    """
    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(p.strip() for p in pages if p.strip())
        return text or None
    except ImportError:
        logger.warning("seed: pypdf nao disponivel — apresentacoes .pdf nao extraidas")
        return None
    except Exception as exc:
        logger.warning("seed: erro ao ler PDF %s: %s", path.name, exc)
        return None


def _extract_file(filename: Optional[str]) -> Optional[str]:
    """Extrai texto do arquivo de apresentacao/objecao. Retorna None se impossivel."""
    if not filename:
        return None
    path = KNOWLEDGE_BASE_PATH / filename
    if not path.exists():
        logger.warning("seed: arquivo nao encontrado: %s", path)
        return None
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _extract_text_docx(path)
    if suffix == ".pdf":
        return _extract_text_pdf(path)
    logger.warning("seed: tipo de arquivo nao suportado para extracao: %s", suffix)
    return None


def _parse_objecoes(texto: Optional[str]) -> list[tuple[str, str]]:
    """
    Extrai pares (objecao, resposta) de texto de banco de objecoes.

    Heuristica: procura blocos "Objecao: ..." / "Resposta: ..." (case-insensitive).
    Retorna lista de pares. Se nao encontrar pares, retorna o texto como uma unica
    objecao generica para preservar o conteudo.
    """
    if not texto:
        return []

    pairs: list[tuple[str, str]] = []
    # Tenta extrair pares Objecao/Resposta estruturados
    objecao_pattern = re.compile(
        r"(?:Obje[cç][aã]o|OBJE[CÇ][AÃ]O)\s*[:\-]?\s*(.+?)(?=(?:Resposta|RESPOSTA)\s*[:\-]?)",
        re.DOTALL,
    )
    resposta_pattern = re.compile(
        r"(?:Resposta|RESPOSTA)\s*[:\-]?\s*(.+?)(?=(?:Obje[cç][aã]o|OBJE[CÇ][AÃ]O)\s*[:\-]?|\Z)",
        re.DOTALL,
    )

    objecoes = [m.group(1).strip() for m in objecao_pattern.finditer(texto)]
    respostas = [m.group(1).strip() for m in resposta_pattern.finditer(texto)]

    if objecoes and respostas and len(objecoes) == len(respostas):
        for obj, resp in zip(objecoes, respostas):
            if obj and resp:
                pairs.append((obj, resp))
    elif texto.strip():
        # Fallback: armazenar texto bruto como objecao generica
        pairs.append(("conteudo_banco_objecoes", texto.strip()))

    return pairs


# ---------------------------------------------------------------------------
# Funcoes de upsert (idempotentes via ON CONFLICT DO UPDATE)
# ---------------------------------------------------------------------------

async def _upsert_curso(session: AsyncSession, meta: dict) -> int:
    """
    Upsert do registro Curso. Retorna o id do curso (novo ou existente).
    Usa INSERT ... ON CONFLICT (slug) DO UPDATE para idempotencia.
    """
    stmt = (
        pg_insert(Curso)
        .values(
            slug=meta["slug"],
            nome=meta["nome"],
            tipo=meta["tipo"],
            caminho_mapa_mestre=meta.get("caminho_mapa_mestre"),
            elegibilidade=meta.get("elegibilidade", {}),
            ativo=True,
        )
        .on_conflict_do_update(
            index_elements=["slug"],
            set_={
                "nome": meta["nome"],
                "tipo": meta["tipo"],
                "caminho_mapa_mestre": meta.get("caminho_mapa_mestre"),
                "elegibilidade": meta.get("elegibilidade", {}),
                "ativo": True,
            },
        )
        .returning(Curso.id)
    )
    result = await session.execute(stmt)
    curso_id: int = result.scalar_one()
    return curso_id


async def _upsert_apresentacao(
    session: AsyncSession, curso_id: int, idioma: str, texto: str
) -> None:
    """
    Upsert de CursoApresentacao por (curso_id, idioma).
    ON CONFLICT DO UPDATE garante idempotencia.
    """
    stmt = (
        pg_insert(CursoApresentacao)
        .values(curso_id=curso_id, idioma=idioma, texto=texto)
        .on_conflict_do_update(
            index_elements=None,
            constraint="uq_apresentacao_curso_idioma",
            set_={"texto": texto},
        )
    )
    await session.execute(stmt)


async def _upsert_objecoes(
    session: AsyncSession, curso_id: int, pairs: list[tuple[str, str]]
) -> None:
    """
    Upsert de CursoObjecao — trata como DELETE+INSERT (truncate-replace)
    por curso_id/idioma para garantir idempotencia quando o arquivo muda.
    Usa DELETE condicional + INSERT em batch.
    """
    if not pairs:
        return

    # Apagar objecoes existentes para este curso/idioma (upsert simples para objecoes)
    await session.execute(
        text(
            "DELETE FROM curso_objecao WHERE curso_id = :cid AND idioma = 'pt'"
        ),
        {"cid": curso_id},
    )
    for objecao, resposta in pairs:
        obj = CursoObjecao(
            curso_id=curso_id,
            idioma="pt",
            objecao=objecao[:2000],   # limite seguro
            resposta=resposta[:5000],
        )
        session.add(obj)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run_seed(db_session: AsyncSession) -> None:
    """
    Executa seed idempotente dos 6 cursos.

    Para cada curso:
      1. Upsert do registro Curso (slug unico — ON CONFLICT DO UPDATE)
      2. Extracao de texto das apresentacoes (.docx/.pdf) quando disponiveis
      3. Upsert de CursoApresentacao por idioma (ON CONFLICT constraint)
      4. Extracao e upsert de CursoObjecao do banco de objecoes

    Re-execucao sem alteracao nos arquivos = nop semantico (dados sobrescritos
    com os mesmos valores). Com arquivos alterados = atualizacao do conteudo.
    """
    logger.info("seed: iniciando seed idempotente de %d cursos...", len(CURSOS_SEED))

    for meta in CURSOS_SEED:
        slug = meta["slug"]
        logger.info("seed: upsert curso slug=%s", slug)

        # 1. Upsert do curso base
        curso_id = await _upsert_curso(db_session, meta)
        logger.debug("seed: curso_id=%s slug=%s", curso_id, slug)

        # 2. Apresentacoes por idioma
        for idioma, arquivo_key in [("pt", "arquivo_pt"), ("en", "arquivo_en"), ("es", "arquivo_es")]:
            filename = meta.get(arquivo_key)
            texto = _extract_file(filename)
            if texto:
                await _upsert_apresentacao(db_session, curso_id, idioma, texto)
                logger.debug(
                    "seed: apresentacao upserted curso=%s idioma=%s chars=%d",
                    slug, idioma, len(texto),
                )

        # 3. Banco de objecoes (somente PT por enquanto)
        objecao_filename = meta.get("arquivo_objecoes_pt")
        texto_objecoes = _extract_file(objecao_filename)
        if texto_objecoes:
            pairs = _parse_objecoes(texto_objecoes)
            await _upsert_objecoes(db_session, curso_id, pairs)
            logger.debug(
                "seed: %d objecoes upserted curso=%s", len(pairs), slug
            )

    await db_session.commit()
    logger.info("seed: concluido — %d cursos upserted.", len(CURSOS_SEED))
