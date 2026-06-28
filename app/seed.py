"""
Seed idempotente dos 6 cursos da GoldIncision (FR-027, US4-AS7).

Popula o catalogo inicial a partir dos documentos em knowledge_base/documentos_agente/.
Re-execucao nao duplica entradas (upsert por slug).

Cursos seedados:
1. Curso Online Harmonizacao Glutea (slug: curso-online-hg)
2. HG Modulo 1 (slug: hg-modulo-1)
3. HG360 Sao Paulo (slug: hg360-sp)
4. HG360 Barcelona (slug: hg360-barcelona)
5. Licenciamento Internacional (slug: licenciamento-internacional)
6. Franquia GoldIncision (slug: franquia-goldincision)

Implementacao completa: FASE 2, task 2.3.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

KNOWLEDGE_BASE_PATH = Path(__file__).parent.parent / "knowledge_base" / "documentos_agente"

# Mapeamento de slugs para arquivos de origem
CURSOS_SEED = [
    {
        "slug": "curso-online-hg",
        "nome": "Curso Online Harmonizacao Glutea",
        "tipo": "online",
        "caminho_mapa_mestre": 1,
        "elegibilidade": {"medico": True},
        "arquivo_doc": "Harmonizacao Glutea On-line.docx",
        "arquivo_objecoes": "Banco de Objecoes Curso Harm Glutea on-line.docx",
    },
    {
        "slug": "hg-modulo-1",
        "nome": "HG Modulo 1",
        "tipo": "presencial",
        "caminho_mapa_mestre": 2,
        "elegibilidade": {"medico": True, "corporal": True},
        "arquivo_doc": "Harmonizacao Glutea 1.docx",
        "arquivo_objecoes": "Banco de Objecoes Curso Harm Glutea 1.docx",
    },
    {
        "slug": "hg360-sp",
        "nome": "HG360 Sao Paulo",
        "tipo": "presencial",
        "caminho_mapa_mestre": 3,
        "elegibilidade": {"medico": True, "corporal": True},
        "arquivo_doc": "Harmonizacao Glutea 2 Sao Paulo.docx",
        "arquivo_objecoes": "Banco de Objecoes Curso Harm Glutea 2.docx",
    },
    {
        "slug": "hg360-barcelona",
        "nome": "HG360 Barcelona",
        "tipo": "presencial",
        "caminho_mapa_mestre": 4,
        "elegibilidade": {"medico": True, "corporal": True},
        "arquivo_doc": "Harmonizacao Glutea 360 Barcelona.docx",
        "arquivo_objecoes": "Banco de Objecoes Curso Harm Glutea 360 Bacelona.docx",
    },
    {
        "slug": "licenciamento-internacional",
        "nome": "Licenciamento Internacional GoldIncision",
        "tipo": "licenciamento",
        "caminho_mapa_mestre": 6,
        "elegibilidade": {},
        "arquivo_doc": "Apres Lic Internac Gold PORT.pdf",
        "arquivo_objecoes": None,
    },
    {
        "slug": "franquia-goldincision",
        "nome": "Franquia GoldIncision",
        "tipo": "franquia",
        "caminho_mapa_mestre": 6,
        "elegibilidade": {},
        "arquivo_doc": None,
        "arquivo_objecoes": None,
    },
]


async def run_seed(db_session) -> None:
    """
    Executa seed idempotente dos 6 cursos.
    Upsert por slug: INSERT ... ON CONFLICT (slug) DO UPDATE SET ...

    STUB: implementacao completa em FASE 2, task 2.3.
    """
    # TODO (FASE 2): para cada curso em CURSOS_SEED:
    #   - extrair texto dos .docx usando python-docx (se disponivel)
    #   - upsert em curso (ON CONFLICT slug DO UPDATE)
    #   - upsert em curso_apresentacao por idioma
    #   - upsert em curso_objecao a partir do arquivo de objecoes
    logger.info("seed: iniciando seed dos %d cursos...", len(CURSOS_SEED))
    for curso in CURSOS_SEED:
        logger.info("seed: processando slug=%s", curso["slug"])
    logger.info("seed: STUB — implementacao completa em FASE 2")
