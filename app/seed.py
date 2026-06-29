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

from app.repository.models import (
    Curso,
    CursoApresentacao,
    CursoLink,
    CursoObjecao,
    CursoTurma,
    Faq,
)

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
        # Os 3 PDFs oficiais (PORT/ING/ESP) sao baseados em imagem (sem camada de
        # texto) — pypdf extrai 0. O operador consolidou o conteudo num unico docx
        # organizado por idioma (Parte 1 PT / Parte 2 ES / Part 3 EN), do qual
        # extraimos pt/es/en (ver _split_licenciamento_por_idioma + run_seed).
        "arquivo_pt": None,
        "arquivo_en": None,
        "arquivo_es": None,
        "arquivo_multilingue": "GoldIncision_Base_Conhecimento.docx",
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
# Links oficiais de inscricao por idioma (texto exato do documento oficial
# "Harmonização Glútea On-line.docx" — anti-alucinacao). Apenas o curso online
# possui links de inscricao diretos; presenciais/licenciamento/franquia sao
# conduzidos por consultor/reuniao (sem link de inscricao automatico).
# Chave: slug do curso; valor: mapa idioma -> URL.
# ---------------------------------------------------------------------------
LINKS_SEED: dict[str, dict[str, str]] = {
    "curso-online-hg": {
        "pt": "https://hotmart.com/pt-br/marketplace/produtos/masterclass-de-harmonizacao-glutea-360-online-em-ate-10x/E104665031C?sck=HOTMART_PRODUCT_PAGE",
        "es": "https://pay.hotmart.com/N95711232T?off=knlbem12",
        "en": "https://pay.hotmart.com/Q95039051K?off=h9zgo86a",
    },
}


# ---------------------------------------------------------------------------
# Extracao de texto dos documentos
# ---------------------------------------------------------------------------

def _extract_text_docx(path: Path) -> Optional[str]:
    """Extrai texto de arquivo .docx usando python-docx.

    Le paragrafos E tabelas EM ORDEM (iter no body) — tabelas sao comuns em
    apresentacoes (ex.: precos do Licenciamento) e seriam perdidas se lessemos
    apenas doc.paragraphs. A ordem importa para o split por idioma.
    Retorna None se indisponivel.
    """
    try:
        import docx  # python-docx
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        doc = docx.Document(str(path))
        parts: list[str] = []
        for child in doc.element.body.iterchildren():
            if isinstance(child, CT_P):
                t = Paragraph(child, doc).text.strip()
                if t:
                    parts.append(t)
            elif isinstance(child, CT_Tbl):
                for row in Table(child, doc).rows:
                    celulas = [c.text.strip() for c in row.cells if c.text.strip()]
                    # dedup de celulas mescladas (row.cells repete merges)
                    vistos: list[str] = []
                    for c in celulas:
                        if not vistos or vistos[-1] != c:
                            vistos.append(c)
                    if vistos:
                        parts.append(" — ".join(vistos))
        return "\n\n".join(parts) if parts else None
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


async def _upsert_links(
    session: AsyncSession, curso_id: int, links: dict[str, str]
) -> None:
    """
    Upsert de CursoLink por (curso_id, idioma) — ON CONFLICT DO UPDATE.
    Idempotente: re-executar atualiza a URL se o documento oficial mudar.
    """
    for idioma, url in links.items():
        if not url:
            continue
        stmt = (
            pg_insert(CursoLink)
            .values(curso_id=curso_id, idioma=idioma, url=url)
            .on_conflict_do_update(
                constraint="uq_link_curso_idioma",
                set_={"url": url},
            )
        )
        await session.execute(stmt)


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
# Licenciamento: split do docx multilingue por idioma
# ---------------------------------------------------------------------------

# Marcadores de secao de idioma no docx consolidado (case-insensitive, sem acento
# nao e necessario pois comparamos via 'in' no texto original).
_LIC_MARCADORES = [
    ("pt", "Parte 1 — Português"),
    ("es", "Parte 2 — Español"),
    ("en", "Part 3 — English"),
]


def _split_licenciamento_por_idioma(texto: str) -> dict[str, str]:
    """
    Divide o docx consolidado de Licenciamento em {pt, es, en} pelos marcadores
    'Parte 1 — Português' / 'Parte 2 — Español' / 'Part 3 — English'.
    Cada secao vai do seu marcador ate o proximo (en vai ate o fim).
    Retorna apenas idiomas com conteudo nao-vazio.
    """
    out: dict[str, str] = {}
    if not texto:
        return out
    # posicoes dos marcadores no texto
    posicoes = []
    for idioma, marcador in _LIC_MARCADORES:
        idx = texto.find(marcador)
        if idx != -1:
            posicoes.append((idx, idioma, marcador))
    posicoes.sort()
    for i, (idx, idioma, marcador) in enumerate(posicoes):
        inicio = idx + len(marcador)
        fim = posicoes[i + 1][0] if i + 1 < len(posicoes) else len(texto)
        trecho = texto[inicio:fim].strip()
        if trecho:
            out[idioma] = trecho
    return out


# ---------------------------------------------------------------------------
# FAQ Oficial: parsing Q/A + upsert
# ---------------------------------------------------------------------------

_FAQ_ARQUIVO = "FAQ.docx"


def _parse_faq(texto: Optional[str]) -> list[tuple[Optional[str], str, str]]:
    """
    Extrai tuplas (secao, pergunta, resposta) do FAQ.docx.

    Heuristica: linhas terminadas em '?' sao perguntas; linhas seguintes (ate a
    proxima pergunta) compoem a resposta. Cabecalhos de secao sao linhas curtas,
    sem pontuacao final, que aparecem ANTES de uma pergunta — detectados como a
    ultima linha de um bloco de resposta com mais de uma linha, ou como linhas
    soltas antes da primeira pergunta.
    """
    if not texto:
        return []
    linhas = [ln.strip() for ln in texto.splitlines() if ln.strip()]
    pares: list[tuple[Optional[str], str, str]] = []
    secao: Optional[str] = None
    pergunta: Optional[str] = None
    ans: list[str] = []

    def _eh_header(s: str) -> bool:
        return len(s) <= 35 and not s.endswith((".", "!", "?", ":", ";", ")"))

    def _flush() -> None:
        nonlocal pergunta, ans, secao
        if pergunta is None:
            return
        secao_proxima: Optional[str] = None
        # Se ha mais de uma linha e a ultima parece header de secao, separa-a.
        if len(ans) > 1 and _eh_header(ans[-1]):
            secao_proxima = ans.pop()
        resposta = "\n".join(ans).strip()
        if resposta:
            pares.append((secao, pergunta[:2000], resposta[:5000]))
        pergunta = None
        ans = []
        if secao_proxima:
            secao = secao_proxima

    for ln in linhas:
        if ln.endswith("?"):
            if pergunta is None:
                if ans:  # linhas antes da 1a pergunta = secao
                    secao = ans[-1]
                    ans = []
                pergunta = ln
            else:
                _flush()
                pergunta = ln
        else:
            ans.append(ln)
    _flush()
    return pares


async def _upsert_faq(
    session: AsyncSession, pares: list[tuple[Optional[str], str, str]], idioma: str = "pt"
) -> None:
    """Upsert idempotente do FAQ — delete+insert por idioma (re-seed limpo)."""
    if not pares:
        return
    await session.execute(
        text("DELETE FROM faq WHERE idioma = :idi"), {"idi": idioma}
    )
    for secao, pergunta, resposta in pares:
        session.add(
            Faq(idioma=idioma, secao=secao, pergunta=pergunta, resposta=resposta, ativo=True)
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

        # Apresentacao multilingue consolidada (ex.: Licenciamento — 1 docx com
        # Parte 1 PT / Parte 2 ES / Part 3 EN). Split por idioma e upsert.
        arquivo_multi = meta.get("arquivo_multilingue")
        if arquivo_multi:
            texto_multi = _extract_file(arquivo_multi)
            por_idioma = _split_licenciamento_por_idioma(texto_multi or "")
            for idioma, trecho in por_idioma.items():
                await _upsert_apresentacao(db_session, curso_id, idioma, trecho)
                logger.debug(
                    "seed: apresentacao(multilingue) curso=%s idioma=%s chars=%d",
                    slug, idioma, len(trecho),
                )

        # Banco de objecoes (somente PT)
        objecao_filename = meta.get("arquivo_objecoes_pt")
        texto_objecoes = _extract_file(objecao_filename)
        if texto_objecoes:
            pairs = _parse_objecoes(texto_objecoes)
            await _upsert_objecoes(db_session, curso_id, pairs)
            logger.debug("seed: %d objecoes upserted curso=%s", len(pairs), slug)

        # Links oficiais de inscricao por idioma (curso online)
        links = LINKS_SEED.get(slug)
        if links:
            await _upsert_links(db_session, curso_id, links)
            logger.debug("seed: %d links upserted curso=%s", len(links), slug)

    # Seed de turmas presenciais com datas oficiais
    for slug, turmas in TURMAS_SEED.items():
        curso_id = slug_to_id.get(slug)
        if curso_id is None:
            logger.warning("seed: slug '%s' nao encontrado para turmas — pulando", slug)
            continue
        await _upsert_turmas(db_session, curso_id, turmas)
        logger.info("seed: %d turma(s) upserted para slug=%s", len(turmas), slug)

    # Seed do FAQ Oficial (global — consultado na hierarquia apos a Base/Objecoes)
    faq_texto = _extract_file(_FAQ_ARQUIVO)
    faq_pares = _parse_faq(faq_texto)
    if faq_pares:
        await _upsert_faq(db_session, faq_pares, idioma="pt")
        logger.info("seed: %d itens de FAQ upserted (pt)", len(faq_pares))

    await db_session.commit()
    logger.info("seed: concluido — %d cursos upserted.", len(CURSOS_SEED))
