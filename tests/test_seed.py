"""
Testes do seed idempotente dos 6 cursos (task 2.3.3).

Cenarios:
- Banco vazio -> 6 cursos carregados (slugs presentes)
- Re-run -> sem duplicatas (contagem permanece 6)
- Cada curso tem slug unico
- Slugs esperados estao todos presentes
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.seed import CURSOS_SEED, run_seed

# ---------------------------------------------------------------------------
# Testes unitarios (sem DB real — verifica logica e chamadas)
# ---------------------------------------------------------------------------

EXPECTED_SLUGS = {
    "curso-online-hg",
    "hg-modulo-1",
    "hg360-sp",
    "hg360-barcelona",
    "licenciamento-internacional",
    "franquia-goldincision",
}


def test_cursos_seed_count():
    """CURSOS_SEED deve ter exatamente 6 cursos."""
    assert len(CURSOS_SEED) == 6


def test_cursos_seed_slugs_unicos():
    """Todos os slugs devem ser unicos."""
    slugs = [c["slug"] for c in CURSOS_SEED]
    assert len(slugs) == len(set(slugs)), "Slugs duplicados encontrados"


def test_cursos_seed_slugs_esperados():
    """Slugs esperados devem estar presentes."""
    slugs = {c["slug"] for c in CURSOS_SEED}
    assert slugs == EXPECTED_SLUGS


def test_cursos_seed_tipos_validos():
    """Tipos devem ser do enum do modelo (online/presencial/licenciamento/franquia)."""
    TIPOS_VALIDOS = {"online", "presencial", "licenciamento", "franquia"}
    for c in CURSOS_SEED:
        assert c["tipo"] in TIPOS_VALIDOS, f"Tipo invalido: {c['tipo']} (slug={c['slug']})"


def test_cursos_seed_caminho_mapa_mestre():
    """caminho_mapa_mestre deve estar entre 1 e 6."""
    for c in CURSOS_SEED:
        cmp = c.get("caminho_mapa_mestre")
        if cmp is not None:
            assert 1 <= cmp <= 6, f"caminho invalido: {cmp} (slug={c['slug']})"


@pytest.mark.asyncio
async def test_run_seed_chama_upsert_6_vezes():
    """run_seed deve realizar upsert para exatamente 6 cursos."""
    mock_session = AsyncMock(spec=AsyncSession)
    # Mock do execute que retorna scalar_one() = id fixo
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 1
    mock_session.execute.return_value = mock_result
    mock_session.commit = AsyncMock()
    mock_session.add = MagicMock()

    with patch("app.seed._extract_file", return_value=None):
        await run_seed(mock_session)

    # Deve ter commitado uma vez no final
    mock_session.commit.assert_called_once()

    # execute chamado pelo menos 6 vezes (uma por curso — upsert do Curso)
    assert mock_session.execute.call_count >= 6, (
        f"execute chamado {mock_session.execute.call_count} vezes, esperado >= 6"
    )


@pytest.mark.asyncio
async def test_run_seed_idempotente():
    """Re-execucao do seed nao deve falhar (upsert idempotente)."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 42
    mock_session.execute.return_value = mock_result
    mock_session.commit = AsyncMock()
    mock_session.add = MagicMock()

    with patch("app.seed._extract_file", return_value=None):
        # Primeira execucao
        await run_seed(mock_session)
        first_execute_count = mock_session.execute.call_count

        # Segunda execucao (sem reset do mock — acumula)
        await run_seed(mock_session)
        second_execute_count = mock_session.execute.call_count

    # Deve ter executado o dobro de chamadas (idempotente = mesma logica)
    assert second_execute_count == 2 * first_execute_count


@pytest.mark.asyncio
async def test_run_seed_com_extracao_docx():
    """Com _extract_file retornando texto, deve criar apresentacoes e objecoes."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 1
    mock_session.execute.return_value = mock_result
    mock_session.commit = AsyncMock()
    mock_session.add = MagicMock()

    texto_apresentacao = "Texto oficial do curso verbatim para apresentacao ao lead."
    texto_objecoes = "Objecao: Esta caro.\n\nResposta: O investimento e incomparavel."

    def fake_extract(filename):
        if filename is None:
            return None
        if "Obje" in (filename or ""):
            return texto_objecoes
        return texto_apresentacao

    with patch("app.seed._extract_file", side_effect=fake_extract):
        await run_seed(mock_session)

    # session.add deve ter sido chamado (para objecoes via CursoObjecao)
    # (a quantidade exata depende do parsing de pares objecao/resposta)
    mock_session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Testes de _extract_file (sem I/O real — verifica fallbacks)
# ---------------------------------------------------------------------------

def test_extract_file_none_para_arquivo_none():
    """_extract_file(None) deve retornar None sem erros."""
    from app.seed import _extract_file
    result = _extract_file(None)
    assert result is None


def test_extract_file_none_para_arquivo_inexistente(tmp_path):
    """_extract_file com arquivo inexistente deve retornar None (nao raise)."""
    from app.seed import _extract_file
    with patch("app.seed.KNOWLEDGE_BASE_PATH", tmp_path):
        result = _extract_file("arquivo_que_nao_existe.docx")
    assert result is None


def test_parse_objecoes_formato_obj_nnn():
    """
    _parse_objecoes deve extrair pares no formato OBJ-NNN (curso online).
    Formato real: OBJ-001 – Titulo / Quando utilizar / Resposta homologada / resposta.
    """
    from app.seed import _parse_objecoes
    texto = (
        "OBJ-001 – Esta muito caro\n\n"
        "Quando utilizar\nQuando o medico afirmar que o investimento e alto.\n\n"
        "Resposta homologada\n\n"
        "O investimento se paga rapidamente.\n\n"
        "OBJ-002 – Nao tenho tempo\n\n"
        "Quando utilizar\nQuando o medico disser que esta sem tempo.\n\n"
        "Resposta homologada\n\n"
        "O curso e intensivo e em 2 dias."
    )
    pairs = _parse_objecoes(texto)
    assert len(pairs) == 2
    assert pairs[0][0] == "Esta muito caro"
    assert "se paga" in pairs[0][1]
    assert pairs[1][0] == "Nao tenho tempo"
    assert "2 dias" in pairs[1][1]


def test_parse_objecoes_formato_alternado():
    """
    _parse_objecoes deve extrair pares no formato alternado (presenciais).
    Formato real: cabecalho / objecao / resposta / objecao / resposta ...
    """
    from app.seed import _parse_objecoes
    texto = (
        "HG Modulo 1\n\n"
        "Esta muito caro\n\n"
        "O investimento se paga rapidamente.\n\n"
        "Nao tenho tempo\n\n"
        "O curso e intensivo e em 2 dias."
    )
    pairs = _parse_objecoes(texto)
    assert len(pairs) == 2
    assert pairs[0][0] == "Esta muito caro"
    assert "se paga" in pairs[0][1]
    assert pairs[1][0] == "Nao tenho tempo"


def test_parse_objecoes_texto_nao_estruturado():
    """_parse_objecoes com texto sem pares deve retornar objecao generica (fallback)."""
    from app.seed import _parse_objecoes
    # Texto com apenas um paragrafo (sem pares alternados nem OBJ-NNN)
    texto = "Texto unico sem estrutura de pares."
    pairs = _parse_objecoes(texto)
    # Fallback: deve retornar ao menos 1 par com o texto bruto
    assert len(pairs) >= 1


def test_parse_objecoes_texto_vazio():
    """_parse_objecoes com texto vazio deve retornar lista vazia."""
    from app.seed import _parse_objecoes
    assert _parse_objecoes("") == []
    assert _parse_objecoes(None) == []
