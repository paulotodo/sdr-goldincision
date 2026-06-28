"""
Seed idempotente dos cursos da GoldIncision (FR-027, US4-AS7).

Popula o catalogo inicial a partir dos documentos em knowledge_base/documentos_agente/.
Re-execucao nao duplica entradas (upsert por slug via ON CONFLICT DO UPDATE).

Cursos seedados:
1. Curso Online Harmonizacao Glutea          (slug: curso-online-hg)       caminho 1
2. HG Modulo 1                               (slug: hg-modulo-1)            caminho 2
3. HG360 Sao Paulo                           (slug: hg360-sp)               caminho 2
4. HG360 Barcelona                           (slug: hg360-barcelona)        caminho 2
5. Licenciamento Internacional GoldIncision  (slug: licenciamento-intl)     caminho 3
6. Franquia GoldIncision                     (slug: franquia-goldincision)  caminho 3

Turmas seedadas (datas oficiais dos documentos):
- HG360 SP: 28-30/08/2026 (cidade=Sao Paulo, pais=Brasil)
- HG360 Barcelona: 24-25/07/2026 (cidade=Barcelona, pais=Espanha)

Formatos de banco de objecoes:
- Formato OBJ-NNN (curso online): cada objecao comeca com "OBJ-NNN – Titulo",
  seguida de "Quando utilizar" e "Resposta homologada".
- Formato alternado (presenciais): pares alternados (objecao, resposta) apos
  paragrafo de cabecalho do curso.

Invariante: nao inventa dados — usa SOMENTE o que esta nos documentos oficiais.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.repository.models import Curso, CursoApresentacao, CursoObjecao, CursoTurma

logger = logging.getLogger(__name__)

# Caminho absoluto da base de conhecimento (relativo a raiz do projeto)
KNOWLEDGE_BASE_PATH = Path(__file__).parent.parent / "knowledge_base" / "documentos_agente"

# ---------------------------------------------------------------------------
# Metadados estruturais dos cursos
# caminho_mapa_mestre fiel ao MAPA MESTRE DO ATENDIMENTO.docx:
#   1 = Curso Online HG
#   2 = Cursos Presenciais HG (modulo 1, HG360 SP e Barcelona sao sub-fluxos)
#   3 = Sistema GoldIncision (Licenciamento / Franquia)
# ---------------------------------------------------------------------------
CURSOS_SEED: list[dict] = [
    {
        "slug": "curso-online-hg",
        "nome": "Curso Online Harmonizacao Glutea",
        "tipo": "online",
        "caminho_mapa_mestre": 1,
        "elegibilidade": {"medico": True},
        "arquivo_pt": "Harmonização Glútea On-line.docx",
        "arquivo_objecoes_pt": "Banco de Objeções Curso Harm Glútea on-line.docx",
        "arquivo_en": None,
        "arquivo_es": None,
    },
    {
        "slug": "hg-modulo-1",
        "nome": "HG Modulo 1",
        "tipo": "presencial",
        "caminho_mapa_mestre": 2,       # sub-fluxo de Caminho 2
        "elegibilidade": {"medico": True},
        "arquivo_pt": "Harmonização Glútea 1.docx",
        "arquivo_objecoes_pt": "Banco de Objeções Curso Harm Glútea 1.docx",
        "arquivo_en": None,
        "arquivo_es": None,
    },
    {
        "slug": "hg360-sp",
        "nome": "HG360 Sao Paulo",
        "tipo": "presencial",
        "caminho_mapa_mestre": 2,       # sub-fluxo de Caminho 2
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
        "caminho_mapa_mestre": 2,       # sub-fluxo de Caminho 2
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
        "caminho_mapa_mestre": 3,       # Caminho 3 = Sistema GoldIncision
        "elegibilidade": {},
        "arquivo_pt": "Apres Lic Internac Gold PORT.pdf",
        "arquivo_en": "Apres Lic Internac Gold ING.pdf",
        "arquivo_es": "Apres Lic Internac Gold ESP.pdf",
        "arquivo_objecoes_pt": None,
    },
    {
        "slug": "franquia-goldincision",
        "nome": "Franquia GoldIncision",
        "tipo": "franquia",
        "caminho_mapa_mestre": 3,       # Caminho 3 = Sistema GoldIncision
        "elegibilidade": {},
        "arquivo_pt": None,
        "arquivo_en": None,
        "arquivo_es": None,
        "arquivo_objecoes_pt": None,
    },
]

# ---------------------------------------------------------------------------
# Turmas presenciais com datas oficiais do MAPA MESTRE DO ATENDIMENTO.docx
# Chave: slug do curso; valor: lista de turmas a serem seedadas
# ---------------------------------------------------------------------------
TURMAS_SEED: dict[str, list[dict]] = {
    "hg360-sp": [
        {
            "cidade": "São Paulo",
            "pais": "Brasil",
            "data_inicio": date(2026, 8, 28),   # 28-30/08/2026
        },
    ],
    "hg360-barcelona": [
        {
            "cidade": "Barcelona",
            "pais": "Espanha",
            "data_inicio": date(2026, 7, 24),   # 24-25/07/2026
        },
    ],
}


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
    Extrai texto de arquivo .pdf via pypdf.
    Retorna None graciosamente se pypdf nao disponivel.
    """
    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        full_text = "\n\n".join(p.strip() for p in pages if p.strip())
        return full_text or None
    except ImportError:
        logger.warning("seed: pypdf nao disponivel — apresentacoes .pdf nao extraidas")
        return None
    except Exception as exc:
        logger.warning("seed: erro ao ler PDF %s: %s", path.name, exc)
        return None


def _extract_file(filename: Optional[str]) -> Optional[str]:
    """Extrai texto do arquivo. Retorna None se impossivel."""
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
    logger.warning("seed: tipo de arquivo nao suportado: %s", suffix)
    return None


# ---------------------------------------------------------------------------
# Parsing de bancos de objecoes
# ---------------------------------------------------------------------------

_OBJ_HEADER_RE = re.compile(r"^OBJ-\d+", re.MULTILINE)
_RESPOSTA_HOMOLOGADA_RE = re.compile(r"^Resposta homologada$", re.MULTILINE | re.IGNORECASE)


def _parse_objecoes_formato_obj(paragrafos: list[str]) -> list[tuple[str, str]]:
    """
    Extrai pares (objecao, resposta) do formato OBJ-NNN.

    Estrutura esperada por bloco:
      OBJ-NNN – Titulo da objecao
      Quando utilizar
      <contexto>
      Resposta homologada
      <paragrafo(s) de resposta>
      <...>
      OBJ-NNN+1 ...

    A objecao e o titulo apos "OBJ-NNN – ".
    A resposta sao todos os paragrafos apos "Resposta homologada" ate o proximo OBJ-.
    """
    pairs: list[tuple[str, str]] = []

    # Encontrar indices dos paragrafos que iniciam blocos OBJ-NNN
    obj_indices: list[int] = [
        i for i, p in enumerate(paragrafos) if _OBJ_HEADER_RE.match(p)
    ]

    for pos, start in enumerate(obj_indices):
        end = obj_indices[pos + 1] if pos + 1 < len(obj_indices) else len(paragrafos)
        bloco = paragrafos[start:end]

        # Titulo: tudo apos "OBJ-NNN – " na primeira linha
        header = bloco[0]
        dash_pos = header.find(" – ")
        if dash_pos != -1:
            titulo = header[dash_pos + 3:].strip()
        else:
            # Sem tracado tipografico, pegar o que vier apos o numero
            titulo = re.sub(r"^OBJ-\d+\s*[-–]?\s*", "", header).strip()

        if not titulo:
            titulo = header  # fallback: usar o header completo

        # Resposta: paragrafos apos "Resposta homologada" dentro do bloco
        resposta_parts: list[str] = []
        in_resposta = False
        for p in bloco[1:]:
            if _RESPOSTA_HOMOLOGADA_RE.match(p.strip()):
                in_resposta = True
                continue
            if in_resposta:
                resposta_parts.append(p)

        resposta = "\n\n".join(resposta_parts).strip()
        if titulo and resposta:
            pairs.append((titulo, resposta))
        elif titulo and not resposta:
            # Bloco sem secao "Resposta homologada" — registrar o bloco bruto
            bloco_texto = "\n\n".join(bloco[1:]).strip()
            if bloco_texto:
                pairs.append((titulo, bloco_texto))

    return pairs


def _parse_objecoes_formato_alternado(paragrafos: list[str]) -> list[tuple[str, str]]:
    """
    Extrai pares (objecao, resposta) do formato alternado (cursos presenciais).

    Estrutura:
      <Titulo do curso> — paragrafo 0, ignorado
      <Objecao 1>       — paragrafo 1
      <Resposta 1>      — paragrafo 2
      <Objecao 2>       — paragrafo 3
      <Resposta 2>      — paragrafo 4
      ...

    Se o numero de paragrafos util for impar, o ultimo e descartado.
    """
    pairs: list[tuple[str, str]] = []

    # Ignorar o primeiro paragrafo (cabecalho do curso)
    util = paragrafos[1:]
    i = 0
    while i + 1 < len(util):
        objecao = util[i].strip()
        resposta = util[i + 1].strip()
        if objecao and resposta:
            pairs.append((objecao, resposta))
        i += 2

    return pairs


def _parse_objecoes(texto: Optional[str]) -> list[tuple[str, str]]:
    """
    Extrai pares (objecao, resposta) do texto do banco de objecoes.

    Detecta automaticamente o formato:
    - Formato OBJ-NNN: primeiro paragrafo nao-vazio comeca com "OBJ-"
    - Formato alternado: pares even/odd apos cabecalho do curso (cursos presenciais)

    Retorna lista de (objecao, resposta). Em caso de falha, retorna [].
    """
    if not texto:
        return []

    # Dividir em paragrafos (separados por linha dupla, como joinados pelo extrator)
    paragrafos = [p.strip() for p in texto.split("\n\n") if p.strip()]
    if not paragrafos:
        return []

    # Detectar formato: presenca de OBJ-NNN no primeiro paragrafo nao-vazio
    primeiro = paragrafos[0]
    if _OBJ_HEADER_RE.match(primeiro):
        pairs = _parse_objecoes_formato_obj(paragrafos)
        logger.debug("seed: parse OBJ-NNN: %d pares extraidos", len(pairs))
    else:
        pairs = _parse_objecoes_formato_alternado(paragrafos)
        logger.debug("seed: parse alternado: %d pares extraidos", len(pairs))

    if not pairs and texto.strip():
        # Ultimo recurso: guardar texto bruto para nao perder conteudo
        pairs = [("banco_objecoes_completo", texto.strip())]
        logger.warning("seed: nenhum par detectado — armazenando texto bruto como fallback")

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
    Upsert de CursoObjecao — delete+insert por curso_id/idioma para
    idempotencia quando o arquivo muda.
    """
    if not pairs:
        return

    await session.execute(
        text("DELETE FROM curso_objecao WHERE curso_id = :cid AND idioma = 'pt'"),
        {"cid": curso_id},
    )
    for objecao, resposta in pairs:
        obj = CursoObjecao(
            curso_id=curso_id,
            idioma="pt",
            objecao=objecao[:2000],
            resposta=resposta[:5000],
        )
        session.add(obj)


async def _upsert_turmas(
    session: AsyncSession, curso_id: int, turmas: list[dict]
) -> None:
    """
    Upsert de CursoTurma por (curso_id, cidade, data_inicio).
    Idempotente: atualiza campos se ja existir, insere se nao existir.
    """
    for turma in turmas:
        # Verificar se ja existe turma com mesmo curso+cidade+data_inicio
        stmt_sel = select(CursoTurma).where(
            CursoTurma.curso_id == curso_id,
            CursoTurma.cidade == turma["cidade"],
            CursoTurma.data_inicio == turma["data_inicio"],
        )
        result = await session.execute(stmt_sel)
        existing = result.scalar_one_or_none()

        if existing is None:
            new_turma = CursoTurma(
                curso_id=curso_id,
                cidade=turma["cidade"],
                pais=turma.get("pais"),
                data_inicio=turma["data_inicio"],
                ativo=True,
            )
            session.add(new_turma)
            logger.debug(
                "seed: turma inserida curso_id=%s cidade=%s data=%s",
                curso_id,
                turma["cidade"],
                turma["data_inicio"],
            )
        else:
            # Atualizar pais se necessario
            if existing.pais != turma.get("pais"):
                existing.pais = turma.get("pais")
            logger.debug(
                "seed: turma ja existe curso_id=%s cidade=%s data=%s",
                curso_id,
                turma["cidade"],
                turma["data_inicio"],
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run_seed(db_session: AsyncSession) -> None:
    """
    Executa seed idempotente dos cursos e turmas presenciais.

    Para cada curso:
      1. Upsert do registro Curso (slug unico — ON CONFLICT DO UPDATE)
      2. Extracao de texto das apresentacoes (.docx/.pdf) quando disponiveis
      3. Upsert de CursoApresentacao por idioma (ON CONFLICT constraint)
      4. Extracao e upsert de CursoObjecao do banco de objecoes

    Para cada turma presencial definida em TURMAS_SEED:
      5. Upsert de CursoTurma por (curso_id, cidade, data_inicio)

    Re-execucao sem alteracao nos arquivos = nop semantico.
    """
    logger.info("seed: iniciando seed de %d cursos...", len(CURSOS_SEED))

    # Mapa slug → curso_id para o seed de turmas
    slug_to_id: dict[str, int] = {}

    for meta in CURSOS_SEED:
        slug = meta["slug"]
        logger.info("seed: upsert curso slug=%s", slug)

        curso_id = await _upsert_curso(db_session, meta)
        slug_to_id[slug] = curso_id
        logger.debug("seed: curso_id=%s slug=%s", curso_id, slug)

        # Apresentacoes por idioma
        for idioma, arquivo_key in [
            ("pt", "arquivo_pt"),
            ("en", "arquivo_en"),
            ("es", "arquivo_es"),
        ]:
            filename = meta.get(arquivo_key)
            texto = _extract_file(filename)
            if texto:
                await _upsert_apresentacao(db_session, curso_id, idioma, texto)
                logger.debug(
                    "seed: apresentacao curso=%s idioma=%s chars=%d",
                    slug, idioma, len(texto),
                )

        # Banco de objecoes (somente PT)
        objecao_filename = meta.get("arquivo_objecoes_pt")
        texto_objecoes = _extract_file(objecao_filename)
        if texto_objecoes:
            pairs = _parse_objecoes(texto_objecoes)
            await _upsert_objecoes(db_session, curso_id, pairs)
            logger.debug("seed: %d objecoes upserted curso=%s", len(pairs), slug)

    # Seed de turmas presenciais com datas oficiais
    for slug, turmas in TURMAS_SEED.items():
        curso_id = slug_to_id.get(slug)
        if curso_id is None:
            logger.warning("seed: slug '%s' nao encontrado para turmas — pulando", slug)
            continue
        await _upsert_turmas(db_session, curso_id, turmas)
        logger.info("seed: %d turma(s) upserted para slug=%s", len(turmas), slug)

    await db_session.commit()
    logger.info("seed: concluido — %d cursos upserted.", len(CURSOS_SEED))
