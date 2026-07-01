"""
Golden set de regressao de jornada (US6, FASE 7, task 7.2).

Roda o **FlowEngine REAL** contra casos de referencia derivados do Mapa
Mestre / dos testes existentes (`tests/test_flow.py`,
`tests/test_turnos_contadores.py`, `tests/test_reengajamento.py`).
Apenas a leitura da Base (`_load_apresentacao`/`_load_curso_link`/
`_load_knowledge*`/`_load_faq`) e stubada — o mesmo padrao de
`StubFlowEngine` em `tests/test_flow.py`. NUNCA mockar `process()`,
`_process_core` ou os handlers.

Marcado com `@pytest.mark.golden` e EXCLUIDO do gate padrao via
`pyproject.toml` (`addopts = '-m "not golden"'`). Por decisao de produto
(research.md Decision 9 / CHK011), o relatorio por dimensao e
**informativo**: nao ha um patamar minimo de taxa de acerto que bloqueie
merge/CI nesta Onda 1 — mas os testes parametrizados individuais PODEM
falhar (visibilidade desejada por caso).

Comando:
    python3 -m pytest tests/golden -m golden -s
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from app.core.flow import FlowEngine
from app.core.intent import ClassificacaoIntencao, Idioma
from app.core.memory import SessionContext

_CASOS_DIR = Path(__file__).parent / "casos"
# Anti-alucinacao de preco (FR-017/7.1.4): nenhum preco deve aparecer na
# resposta a menos que venha literalmente do fixture de conhecimento/
# resposta do caso (nunca sintetizado pelo motor).
_PRECO_REGEX = re.compile(r"R\$\s*[\d.,]+")


# ---------------------------------------------------------------------------
# Stubs — mesmo padrao de tests/test_flow.py::StubFlowEngine (so I/O de Base)
# ---------------------------------------------------------------------------

class _GoldenIntent:
    """Classificador stub: retorna a intencao/idioma definidos pelo caso."""

    def __init__(self, intencao: ClassificacaoIntencao, idioma: Idioma) -> None:
        self._intencao = intencao
        self._idioma = idioma

    async def classify(self, message: str, session_context: dict | None = None):
        return self._intencao, self._idioma


class _GoldenMemory:
    """MemoryManager stub: sem historico de LLM (nao afeta a maquina de estados)."""

    def build_messages_for_llm(self, context: SessionContext, max_msgs: int = 10) -> list:
        return []


class _GoldenResponder:
    """
    Responder deterministico controlado pelo campo `responder` do caso.

    Representa o que o GroundedResponder (LLM com grounding estrito)
    teria decidido para aquele turno — o golden set testa a MAQUINA DE
    ESTADOS (roteamento, nao-repeticao de slot, orcamento de turnos,
    reengajamento, allowlist de handoff), nao a geracao do LLM em si
    (coberta por tests/test_responder.py).
    """

    def __init__(self, texto: str = "RESPOSTA_LLM", handoff: bool = False) -> None:
        self._texto = texto
        self._handoff = handoff
        self.generate_calls: list[dict] = []

    async def generate(self, user_message, caminho, etapa, knowledge_context, **kwargs):
        self.generate_calls.append({"caminho": caminho, "etapa": etapa})
        return self._texto, self._handoff

    async def generate_menu(self, idioma: str = "pt") -> str:
        return f"MENU_{idioma.upper()}"

    async def generate_not_eligible(self, idioma: str = "pt") -> str:
        return f"NAO_ELEGIVEL_{idioma.upper()}"

    async def generate_paciente_modelo(self, nidia_phone: str, idioma: str = "pt") -> str:
        return f"CONTATO_NIDIA: {nidia_phone}"


class _GoldenStubEngine(FlowEngine):
    """FlowEngine REAL com leitura da Base stubada (sem Postgres)."""

    def __init__(self, intent, responder, *, apres=None, link=None, knowledge="BASE") -> None:
        super().__init__(
            db_session=None,
            intent_classifier=intent,
            memory_manager=_GoldenMemory(),
            responder=responder,
            nidia_phone="+55 21 97423-9844",
        )
        # apres: dict slug -> texto OU slug -> {idioma: texto}
        # link: dict (slug, idioma) -> url (ja "achatado" por _flatten_links)
        self._apres = apres or {}
        self._link = link or {}
        self._knowledge = knowledge

    async def _load_apresentacao(self, slug: str, idioma: str) -> str:
        val = self._apres.get(slug)
        if isinstance(val, dict):
            return val.get(idioma) or val.get("pt", "")
        return val or ""

    async def _load_curso_link(self, slug: str, idioma: str):
        return self._link.get((slug, idioma)) or self._link.get((slug, "pt"))

    async def _load_knowledge_by_slug(self, slug: str, idioma: str) -> str:
        return self._knowledge

    async def _load_knowledge(self, caminho: int, idioma: str) -> str:
        return self._knowledge

    async def _load_faq(self, idioma: str) -> str:
        return ""


# ---------------------------------------------------------------------------
# Carregamento dos casos (tests/golden/casos/*.json)
# ---------------------------------------------------------------------------

def _carregar_casos() -> list[dict]:
    casos: list[dict] = []
    for path in sorted(_CASOS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for caso in data.get("casos", []):
            caso = dict(caso)
            caso["_arquivo"] = path.name
            casos.append(caso)
    return casos


_CASOS = _carregar_casos()
_IDS = [c["id"] for c in _CASOS]


def _flatten_links(links_json: dict) -> dict:
    """Converte {slug: {idioma: url}} (JSON nao suporta chave tupla) no
    dict (slug, idioma) -> url esperado por _GoldenStubEngine._load_curso_link."""
    flat: dict[tuple[str, str], str] = {}
    for slug, por_idioma in (links_json or {}).items():
        if isinstance(por_idioma, dict):
            for idioma, url in por_idioma.items():
                flat[(slug, idioma)] = url
        else:
            flat[(slug, "pt")] = por_idioma
    return flat


def _build_context(estado: dict) -> SessionContext:
    base: dict[str, Any] = {"ticket_id": 1, "chamado_id": 1, "contato_id": 1}
    base.update(estado or {})
    base.setdefault("perfil", {})
    base.setdefault("historico_recente", [])
    return SessionContext(**base)


def _get_path(obj: Any, dotted: str) -> Any:
    """Acesso por caminho pontilhado (ex.: 'perfil.cidade') em dict ou objeto."""
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
    return cur


async def _rodar_caso(caso: dict) -> tuple[Any, SessionContext, list[str]]:
    """Executa um caso golden contra o FlowEngine REAL.

    Retorna (FlowResult, context_apos_processamento, lista_de_falhas) — a
    lista de falhas fica vazia quando todos os campos de `esperado`
    presentes no caso batem com o resultado.
    """
    intent_cfg = caso.get("intent") or {}
    estado_inicial = caso.get("estado_inicial") or {}
    intencao = ClassificacaoIntencao(intent_cfg.get("intencao", "ambigua"))
    idioma_classificado = Idioma(
        intent_cfg.get("idioma", estado_inicial.get("idioma", "pt"))
    )
    responder_cfg = caso.get("responder") or {}
    responder = _GoldenResponder(
        texto=responder_cfg.get("texto", "RESPOSTA_LLM"),
        handoff=bool(responder_cfg.get("handoff", False)),
    )
    engine = _GoldenStubEngine(
        _GoldenIntent(intencao, idioma_classificado),
        responder,
        apres=caso.get("apresentacoes") or {},
        link=_flatten_links(caso.get("links") or {}),
        knowledge=caso.get("knowledge", "BASE"),
    )
    context = _build_context(estado_inicial)
    result = await engine.process(1, caso["mensagem"], context)

    falhas: list[str] = []
    esperado = caso.get("esperado") or {}
    texto_resp = result.response_text or ""

    if "proxima_acao" in esperado and result.action != esperado["proxima_acao"]:
        falhas.append(
            f"proxima_acao: esperado={esperado['proxima_acao']!r} obtido={result.action!r}"
        )

    if "etapa" in esperado and result.etapa != esperado["etapa"]:
        falhas.append(f"etapa: esperado={esperado['etapa']!r} obtido={result.etapa!r}")

    if "caminho" in esperado and result.caminho != esperado["caminho"]:
        falhas.append(f"caminho: esperado={esperado['caminho']!r} obtido={result.caminho!r}")

    if "handoff_destino" in esperado and result.handoff_destino != esperado["handoff_destino"]:
        falhas.append(
            f"handoff_destino: esperado={esperado['handoff_destino']!r} "
            f"obtido={result.handoff_destino!r}"
        )

    if "turno_acao" in esperado and result.turno_acao != esperado["turno_acao"]:
        falhas.append(
            f"turno_acao: esperado={esperado['turno_acao']!r} obtido={result.turno_acao!r}"
        )

    if "motivo" in esperado and result.motivo != esperado["motivo"]:
        falhas.append(f"motivo: esperado={esperado['motivo']!r} obtido={result.motivo!r}")

    if esperado.get("abster") is True and result.action != "handoff":
        falhas.append("abster=true esperado (fora da Base Oficial), mas action != 'handoff'")

    if esperado.get("sem_preco_inventado") is True:
        achados = _PRECO_REGEX.findall(texto_resp)
        if achados:
            falhas.append(f"sem_preco_inventado violado: precos encontrados={achados}")

    for termo in esperado.get("contains", []):
        if termo not in texto_resp:
            falhas.append(f"contains ausente: {termo!r}")

    for termo in esperado.get("not_contains", []):
        if termo in texto_resp:
            falhas.append(f"not_contains violado: {termo!r}")

    for campo, valor in (esperado.get("nao_repetir_slot") or {}).items():
        obtido = _get_path(context, campo)
        if obtido != valor:
            falhas.append(f"nao_repetir_slot[{campo}]: esperado={valor!r} obtido={obtido!r}")

    return result, context, falhas


# ---------------------------------------------------------------------------
# Testes parametrizados — 1 por caso golden (falhas individuais visiveis)
# ---------------------------------------------------------------------------

@pytest.mark.golden
@pytest.mark.asyncio
@pytest.mark.parametrize("caso", _CASOS, ids=_IDS)
async def test_golden_caso(caso: dict) -> None:
    _, _, falhas = await _rodar_caso(caso)
    assert not falhas, f"[{caso.get('dimensao')}/{caso['id']}] " + "; ".join(falhas)


# ---------------------------------------------------------------------------
# Relatorio agregado por dimensao (informativo — research.md Decision 9 /
# CHK011): NUNCA falha o build, mesmo com taxa de acerto < 100%.
# ---------------------------------------------------------------------------

@pytest.mark.golden
@pytest.mark.asyncio
async def test_golden_relatorio() -> None:
    resultados: dict[str, list[bool]] = defaultdict(list)
    detalhes: dict[str, list[str]] = defaultdict(list)

    for caso in _CASOS:
        dimensao = caso.get("dimensao", "?")
        _, _, falhas = await _rodar_caso(caso)
        ok = not falhas
        resultados[dimensao].append(ok)
        if not ok:
            detalhes[dimensao].append(f"  - {caso['id']}: " + "; ".join(falhas))

    total_casos = sum(len(v) for v in resultados.values())
    total_ok = sum(sum(v) for v in resultados.values())

    linhas = []
    linhas.append("")
    linhas.append("=" * 72)
    linhas.append("GOLDEN SET — taxa de acerto por dimensao (informativo, sem gate)")
    linhas.append("=" * 72)
    linhas.append(f"{'Dimensao':<24}{'Acerto':>8}{'Total':>8}{'Taxa':>10}")
    linhas.append("-" * 72)
    for dimensao in sorted(resultados):
        acertos = sum(resultados[dimensao])
        total = len(resultados[dimensao])
        taxa = (acertos / total * 100) if total else 0.0
        linhas.append(f"{dimensao:<24}{acertos:>8}{total:>8}{taxa:>9.1f}%")
        linhas.extend(detalhes[dimensao])
    linhas.append("-" * 72)
    taxa_geral = (total_ok / total_casos * 100) if total_casos else 0.0
    linhas.append(f"{'TOTAL':<24}{total_ok:>8}{total_casos:>8}{taxa_geral:>9.1f}%")
    linhas.append("=" * 72)
    linhas.append(
        "Nota (research.md Decision 9 / CHK011): relatorio informativo — "
        "sem threshold bloqueante nesta Onda 1."
    )
    print("\n".join(linhas))

    # Informativo por decisao de produto: este teste NUNCA falha o build,
    # independentemente da taxa de acerto (os testes parametrizados
    # individuais acima e' que ficam vermelhos por caso, para visibilidade).
    assert True
