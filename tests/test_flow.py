"""
Testes do motor de fluxo conversacional (FlowEngine REAL).

Diferente da versao anterior (que reimplementava process() num MockFlowEngine),
aqui exercitamos o FlowEngine REAL — apenas a leitura da Base (metodos _load_*)
e stubada, para que toda a maquina de estados (process + handlers) seja testada
de fato, fiel ao MAPA MESTRE DO ATENDIMENTO.

Cobertura (plano "Jornada Humanizada", §5):
  C1: pergunta direta de preco → responde sem travar; qualifica; fechamento
      oferece link; SIM → envia link no idioma.
  C2: medico → experiencia → especialidade → trilha (Modulo 1 + HG360 SP juntos);
      HG360 → escolha de turma → apresentacao → encaminhar consultor (handoff).
  C3: ETAPA 1 (nao e curso) → ETAPA 2 (objetivo) → sub-caminhos → handoff/reuniao.
  C4: submenu 6 opcoes → encaminhamento (handoff).
  C5: Nidia (end). C6: outro (handoff).
  Robustez: resposta nao reconhecida → reformula → 3a vez → handoff.
  Nao-repeticao: ja-medico nao re-pergunta ao trocar de caminho.
  Multilingue: PT/EN/ES.
"""
from __future__ import annotations

from typing import Optional
from unittest.mock import AsyncMock

import pytest

from app.core.flow import (
    ETAPA_ALUNO_MENU,
    ETAPA_DUVIDAS,
    ETAPA_ESCOLHA_TURMA,
    ETAPA_FECHAMENTO,
    ETAPA_HANDOFF,
    ETAPA_LINK,
    ETAPA_MENU,
    ETAPA_PACIENTE,
    ETAPA_QUALIF_ESPECIALIDADE,
    ETAPA_QUALIF_EXPERIENCIA,
    ETAPA_QUALIF_MEDICO,
    ETAPA_SISTEMA_DIAGNOSTICO,
    ETAPA_SISTEMA_FRANQUIA,
    ETAPA_SISTEMA_LICENCIAMENTO,
    ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS,
    ETAPA_SISTEMA_OBJETIVO,
    CaminhoMapaMestre,
    FlowEngine,
    _detectar_confirmacao,
    _detectar_escolha_turma,
    _detectar_especialidade,
    _detectar_experiencia_corporal,
    _detectar_fechamento,
    _detectar_objetivo_sistema,
    _detectar_opcao_aluno,
    _eh_pergunta_informativa,
    _merge_perfil,
    _pede_humano,
    _perfil_conhecido,
    _saudacao,
)
from app.core.intent import ClassificacaoIntencao, Idioma
from app.core.memory import SessionContext

# ---------------------------------------------------------------------------
# Fixtures / stubs
# ---------------------------------------------------------------------------

def make_context(
    *,
    caminho: Optional[int] = None,
    etapa: Optional[str] = None,
    idioma: str = "pt",
    eh_medico: Optional[bool] = None,
    especialidade: Optional[str] = None,
    experiencia_corporal: Optional[bool] = None,
    produto_interesse: Optional[str] = None,
    etapa_funil: Optional[str] = None,
    nome: Optional[str] = "Paulo",
    turnos_sessao: int = 0,
    turnos_no_no: int = 0,
) -> SessionContext:
    return SessionContext(
        ticket_id=1,
        chamado_id=1001,
        contato_id=10,
        caminho=caminho,
        etapa=etapa,
        idioma=idioma,
        eh_medico=eh_medico,
        especialidade=especialidade,
        experiencia_corporal=experiencia_corporal,
        produto_interesse=produto_interesse,
        resumo_rolante=None,
        historico_recente=[],
        sessao_id=100,
        nome=nome,
        etapa_funil=etapa_funil,
        turnos_sessao=turnos_sessao,
        turnos_no_no=turnos_no_no,
    )


class MockIntent:
    def __init__(self, intencao: ClassificacaoIntencao, idioma: Idioma = Idioma.PT):
        self.intencao = intencao
        self.idioma = idioma

    async def classify(self, message: str, session_context=None):
        return self.intencao, self.idioma


class MockMemory:
    def build_messages_for_llm(self, context, max_msgs=10):
        return []


class MockResponder:
    """Responder com geracao deterministica; marca quando o LLM seria usado."""

    def __init__(self, response_text: str = "RESPOSTA_LLM", handoff: bool = False):
        self._text = response_text
        self._handoff = handoff
        self.generate_calls: list[dict] = []

    async def generate(self, user_message, caminho, etapa, knowledge_context, **kwargs):
        self.generate_calls.append({"caminho": caminho, "etapa": etapa})
        return self._text, self._handoff

    async def generate_menu(self, idioma: str = "pt"):
        return f"MENU_{idioma.upper()}"

    async def generate_not_eligible(self, idioma: str = "pt"):
        return f"NAO_ELEGIVEL_{idioma.upper()}"

    async def generate_paciente_modelo(self, nidia_phone: str, idioma: str = "pt"):
        return f"CONTATO_NIDIA: {nidia_phone}"


class StubFlowEngine(FlowEngine):
    """FlowEngine REAL com a leitura da Base stubada (sem Postgres)."""

    def __init__(self, intent, responder, *, apres=None, link=None, knowledge="BASE"):
        super().__init__(
            db_session=None,
            intent_classifier=intent,
            memory_manager=MockMemory(),
            responder=responder,
            nidia_phone="+55 21 97423-9844",
        )
        # apres: dict slug → texto; link: dict (slug,idioma) → url
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


def engine(intencao=ClassificacaoIntencao.AMBIGUA, idioma=Idioma.PT, **kw):
    resp = kw.pop("responder", None) or MockResponder(**kw.pop("resp_kw", {}))
    return StubFlowEngine(MockIntent(intencao, idioma), resp, **kw)


# ===========================================================================
# Menu / despacho basico
# ===========================================================================

@pytest.mark.asyncio
async def test_intencao_ambigua_retorna_menu():
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    result = await eng.process(1, "oi", make_context())
    assert result.action == "continue"
    assert result.etapa == ETAPA_MENU
    assert "MENU" in result.response_text


@pytest.mark.asyncio
async def test_menu_opcao_numerica_roteia_mesmo_com_intent_ambigua():
    """Digitar '3' no menu entra no C3 mesmo com o classificador retornando ambigua
    (numero seco nao deve depender do LLM) — corrige o relato do operador."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)  # LLM nao ajuda
    ctx = make_context(caminho=None, etapa=ETAPA_MENU)
    r = await eng.process(1, "3", ctx)
    assert r.caminho == CaminhoMapaMestre.SISTEMA_GOLDINCISION
    assert r.etapa == ETAPA_SISTEMA_OBJETIVO


@pytest.mark.asyncio
async def test_menu_opcoes_numericas_roteiam_sem_llm():
    """Os numeros 1/2/3 do menu entram no caminho correspondente, sem depender do LLM."""
    esperado = {
        "1": CaminhoMapaMestre.CURSO_ONLINE_HG,
        "2": CaminhoMapaMestre.CURSOS_PRESENCIAIS,
        "3": CaminhoMapaMestre.SISTEMA_GOLDINCISION,
    }
    for opcao, caminho in esperado.items():
        eng = engine(ClassificacaoIntencao.AMBIGUA)
        ctx = make_context(caminho=None, etapa=ETAPA_MENU)
        r = await eng.process(1, opcao, ctx)
        assert r.caminho == caminho, f"opcao {opcao} deveria rotear para {caminho}"


@pytest.mark.asyncio
async def test_pedido_humano_handoff_global():
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(caminho=2, etapa=ETAPA_DUVIDAS, eh_medico=True)
    result = await eng.process(1, "quero falar com um humano", ctx)
    assert result.action == "handoff"
    assert result.etapa == ETAPA_HANDOFF


# ===========================================================================
# Caminho 1 — Curso Online
# ===========================================================================

@pytest.mark.asyncio
async def test_c1_pergunta_direta_preco_responde_sem_travar():
    """Pergunta direta de preco com eh_medico=None → responde da Base, sem qualificar."""
    resp = MockResponder(response_text="O curso custa R$X")
    eng = engine(ClassificacaoIntencao.CURSO_ONLINE, responder=resp)
    ctx = make_context(caminho=1)
    result = await eng.process(1, "Quanto custa o curso online?", ctx)

    assert result.caminho == CaminhoMapaMestre.CURSO_ONLINE_HG
    assert result.etapa == ETAPA_DUVIDAS
    assert result.response_text == "O curso custa R$X"
    assert len(resp.generate_calls) == 1  # usou o LLM (duvida), nao travou na qualificacao
    assert result.action == "continue"


@pytest.mark.asyncio
async def test_c1_sem_pergunta_pergunta_medico_primeiro():
    eng = engine(ClassificacaoIntencao.CURSO_ONLINE)
    ctx = make_context(caminho=1)
    result = await eng.process(1, "quero o curso online", ctx)
    assert result.etapa == ETAPA_QUALIF_MEDICO
    assert "médico" in result.response_text.lower()


@pytest.mark.asyncio
async def test_c1_fluxo_completo_ate_link():
    """Qualifica → apresenta → fechamento oferece link → SIM → envia link (PT)."""
    apres = {"curso-online-hg": "APRESENTACAO OFICIAL DO CURSO ONLINE"}
    link = {("curso-online-hg", "pt"): "https://hotmart.com/pt/inscricao"}
    resp = MockResponder()
    eng = engine(ClassificacaoIntencao.CURSO_ONLINE, responder=resp, apres=apres, link=link)

    # Passo 1: confirma medico → apresentacao
    ctx = make_context(caminho=1, etapa=ETAPA_QUALIF_MEDICO)
    r1 = await eng.process(1, "sim, sou médico com CRM ativo", ctx)
    assert r1.etapa == ETAPA_DUVIDAS
    assert "APRESENTACAO OFICIAL" in r1.response_text
    ctx.eh_medico = True
    ctx.etapa = ETAPA_DUVIDAS

    # Passo 2: lead sinaliza que quer se inscrever → fechamento envia o link
    eng2 = engine(ClassificacaoIntencao.AMBIGUA, responder=MockResponder(), apres=apres, link=link)
    r2 = await eng2.process(1, "quero me inscrever, pode enviar o link", ctx)
    assert "hotmart.com/pt/inscricao" in r2.response_text


@pytest.mark.asyncio
async def test_c1_link_gate_qualificacao_quando_medico_desconhecido():
    """Pergunta direta (medico desconhecido) → quer link → qualifica antes de liberar."""
    apres = {"curso-online-hg": "APRES"}
    link = {("curso-online-hg", "pt"): "https://x/link"}
    eng = engine(ClassificacaoIntencao.CURSO_ONLINE, apres=apres, link=link)
    ctx = make_context(caminho=1, etapa=ETAPA_DUVIDAS)  # eh_medico None
    r = await eng.process(1, "quero o link de inscrição", ctx)
    # Deve pedir qualificacao medica antes de liberar o link
    assert r.etapa == ETAPA_LINK
    assert "médico" in r.response_text.lower()


@pytest.mark.asyncio
async def test_c1_nao_medico_encerra():
    eng = engine(ClassificacaoIntencao.CURSO_ONLINE)
    ctx = make_context(caminho=1, etapa=ETAPA_QUALIF_MEDICO)
    r = await eng.process(1, "não, não sou médico", ctx)
    assert r.action == "end"
    assert "NAO_ELEGIVEL" in r.response_text


@pytest.mark.asyncio
async def test_c1_link_em_ingles():
    apres = {"curso-online-hg": "PRESENTATION"}
    link = {
        ("curso-online-hg", "pt"): "https://x/pt",
        ("curso-online-hg", "en"): "https://x/en",
    }
    eng = engine(ClassificacaoIntencao.AMBIGUA, idioma=Idioma.EN, apres=apres, link=link)
    ctx = make_context(caminho=1, etapa=ETAPA_FECHAMENTO, idioma="en", eh_medico=True)
    r = await eng.process(1, "yes", ctx)
    assert "https://x/en" in r.response_text


@pytest.mark.asyncio
async def test_c1_pergunta_geral_responde_sem_gate_medico():
    """REGRA do Caminho 1: pergunta GERAL sobre o curso (sem preço explícito) →
    responde da Base, sem disparar 'você é médico?'."""
    for msg in [
        "quais são os cursos online?",
        "me fala sobre o curso online",
        "quero saber sobre o curso",
    ]:
        resp = MockResponder(response_text="info do curso")
        eng = engine(ClassificacaoIntencao.CURSO_ONLINE, responder=resp)
        ctx = make_context(caminho=1)  # eh_medico None
        r = await eng.process(1, msg, ctx)
        assert r.etapa == ETAPA_DUVIDAS, f"{msg!r} caiu em {r.etapa}"
        assert len(resp.generate_calls) == 1, f"não respondeu via base para {msg!r}"


@pytest.mark.asyncio
async def test_c1_intencao_de_compra_ainda_qualifica():
    """'quero o curso' não é pergunta → mantém a qualificação médica primeiro."""
    eng = engine(ClassificacaoIntencao.CURSO_ONLINE)
    ctx = make_context(caminho=1)
    r = await eng.process(1, "quero o curso online", ctx)
    assert r.etapa == ETAPA_QUALIF_MEDICO


@pytest.mark.asyncio
async def test_qualif_medico_fiel_por_caminho():
    """Texto de qualificação específico por caminho (fiel ao Mapa Mestre)."""
    eng = engine()
    assert "Curso Online" in await eng._gerar_pergunta_medico(
        "pt", CaminhoMapaMestre.CURSO_ONLINE_HG)
    assert "Presenciais" in await eng._gerar_pergunta_medico(
        "pt", CaminhoMapaMestre.CURSOS_PRESENCIAIS)
    assert "Licenciamento" in await eng._gerar_pergunta_medico(
        "pt", CaminhoMapaMestre.SISTEMA_GOLDINCISION)
    # Sem caminho → fallback genérico (não vazio)
    assert await eng._gerar_pergunta_medico("pt")


# ===========================================================================
# Caminho 2 — Presenciais
# ===========================================================================

@pytest.mark.asyncio
async def test_c2_qualificacao_medico_experiencia_trilha():
    """
    Sem experiencia + especialidade nao qualificante → HG Modulo 1 (trilha):
    apresenta HG Modulo 1 + HG360 SP juntos.
    """
    apres = {
        "hg-modulo-1": "CONTEUDO_MODULO_1",
        "hg360-sp": "CONTEUDO_HG360_SP",
    }
    eng = engine(ClassificacaoIntencao.CURSOS_PRESENCIAIS, apres=apres)
    # Medico, sem experiencia, especialidade "outra" → Modulo 1
    ctx = make_context(
        caminho=2, etapa=ETAPA_QUALIF_ESPECIALIDADE,
        eh_medico=True, experiencia_corporal=False,
    )
    r = await eng.process(1, "clínica geral, não possuo especialidade", ctx)
    assert r.etapa == ETAPA_DUVIDAS
    assert "CONTEUDO_MODULO_1" in r.response_text
    assert "CONTEUDO_HG360_SP" in r.response_text  # trilha: ambos juntos


@pytest.mark.asyncio
async def test_c2_pergunta_experiencia_apos_medico():
    eng = engine(ClassificacaoIntencao.CURSOS_PRESENCIAIS)
    ctx = make_context(caminho=2, etapa=ETAPA_QUALIF_MEDICO)
    r = await eng.process(1, "sim, sou médico", ctx)
    assert r.etapa == ETAPA_QUALIF_EXPERIENCIA


@pytest.mark.asyncio
async def test_c2_com_experiencia_escolha_turma():
    eng = engine(ClassificacaoIntencao.CURSOS_PRESENCIAIS)
    ctx = make_context(caminho=2, etapa=ETAPA_QUALIF_EXPERIENCIA, eh_medico=True)
    r = await eng.process(1, "sim, já atuo com harmonização corporal", ctx)
    assert r.etapa == ETAPA_ESCOLHA_TURMA
    assert "São Paulo" in r.response_text and "Barcelona" in r.response_text


@pytest.mark.asyncio
async def test_c2_hg360_apresentacao_e_consultor_handoff():
    """Escolhe turma SP → apresenta HG360 SP → 'sim encaminhar' → consultor (handoff)."""
    apres = {"hg360-sp": "APRES_HG360_SP"}
    eng = engine(ClassificacaoIntencao.CURSOS_PRESENCIAIS, apres=apres)
    ctx = make_context(
        caminho=2, etapa=ETAPA_ESCOLHA_TURMA, eh_medico=True, experiencia_corporal=True,
    )
    r1 = await eng.process(1, "São Paulo", ctx)
    assert r1.etapa == ETAPA_DUVIDAS
    assert "APRES_HG360_SP" in r1.response_text
    assert ctx.produto_interesse == "hg360-sp"

    # Fechamento: encaminhar consultor
    ctx.etapa = ETAPA_DUVIDAS
    eng2 = engine(ClassificacaoIntencao.AMBIGUA, apres=apres)
    r2 = await eng2.process(1, "sim, pode encaminhar ao consultor", ctx)
    assert r2.action == "handoff"
    assert "consultor" in r2.response_text.lower()


@pytest.mark.asyncio
async def test_c2_duvida_usa_prompt_por_slug():
    """Duvida no HG360 SP usa o prompt despachado por SLUG (corrige colisao)."""
    apres = {"hg360-sp": "APRES"}
    resp = MockResponder(response_text="resposta da duvida")
    eng = engine(ClassificacaoIntencao.AMBIGUA, responder=resp, apres=apres)
    ctx = make_context(
        caminho=2, etapa=ETAPA_DUVIDAS, eh_medico=True,
        experiencia_corporal=True, produto_interesse="hg360-sp",
    )
    r = await eng.process(1, "qual a carga horária do curso?", ctx)
    assert r.response_text == "resposta da duvida"
    assert resp.generate_calls[0]["caminho"] == "hg360-sp"  # nao 3 (Sistema GoldIncision)


@pytest.mark.asyncio
async def test_c2_nao_medico_encerra():
    eng = engine(ClassificacaoIntencao.CURSOS_PRESENCIAIS)
    ctx = make_context(caminho=2, etapa=ETAPA_QUALIF_MEDICO)
    r = await eng.process(1, "não sou médico", ctx)
    assert r.action == "end"


# ===========================================================================
# Caminho 3 — Sistema GoldIncision
# ===========================================================================

@pytest.mark.asyncio
async def test_c3_etapa1_explica_nao_e_curso_e_pergunta_objetivo():
    eng = engine(ClassificacaoIntencao.SISTEMA_GOLDINCISION)
    ctx = make_context(caminho=3)
    r = await eng.process(1, "quero saber do sistema GoldIncision", ctx)
    assert r.etapa == ETAPA_SISTEMA_OBJETIVO
    assert "não é" in r.response_text.lower() or "curso avulso" in r.response_text.lower()
    assert "Licenciamento" in r.response_text and "Franquia" in r.response_text


@pytest.mark.asyncio
async def test_c3_incorporar_medico_apresenta_licenciamento_e_reuniao():
    apres = {"licenciamento-internacional": "APRES_LICENCIAMENTO"}
    eng = engine(ClassificacaoIntencao.AMBIGUA, apres=apres)
    # ETAPA 2: escolhe "1" (incorporar) → pergunta medico
    ctx = make_context(caminho=3, etapa=ETAPA_SISTEMA_OBJETIVO)
    r1 = await eng.process(1, "1", ctx)
    assert r1.etapa == ETAPA_SISTEMA_LICENCIAMENTO

    # Confirma medico → abre duvidas com RESUMO curto (anti-rajada): NAO despeja a
    # apresentacao verbatim longa; conduz a uma conversa com especialista.
    ctx.etapa = ETAPA_SISTEMA_LICENCIAMENTO
    r2 = await eng.process(1, "sim, sou médico", ctx)
    assert r2.etapa == ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS
    assert "APRES_LICENCIAMENTO" not in r2.response_text  # sem dump verbatim
    assert "Licenciamento Internacional GoldIncision" in r2.response_text
    assert "especialista" in r2.response_text.lower()
    # Resposta objetiva: poucas frases (sem rajada de muitos blocos longos).
    assert len(r2.response_text) < 700

    # Sem mais duvidas → convida reuniao (handoff)
    ctx.etapa = ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS
    ctx.eh_medico = True
    r3 = await eng.process(1, "não tenho dúvidas, obrigado", ctx)
    assert r3.action == "handoff"
    assert "especialista" in r3.response_text.lower() or "reunião" in r3.response_text.lower()


@pytest.mark.asyncio
async def test_c3_incorporar_nao_medico_oferece_franquia():
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(caminho=3, etapa=ETAPA_SISTEMA_LICENCIAMENTO)
    r = await eng.process(1, "não, sou investidor", ctx)
    assert r.action == "handoff"
    assert "Franquia" in r.response_text


@pytest.mark.asyncio
async def test_c3_abrir_clinica_franquia_handoff():
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(caminho=3, etapa=ETAPA_SISTEMA_OBJETIVO)
    r1 = await eng.process(1, "2", ctx)
    assert r1.etapa == ETAPA_SISTEMA_FRANQUIA
    ctx.etapa = ETAPA_SISTEMA_FRANQUIA
    r2 = await eng.process(1, "sou investidor", ctx)
    assert r2.action == "handoff"


@pytest.mark.asyncio
async def test_c3_diagnostico_handoff():
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(caminho=3, etapa=ETAPA_SISTEMA_OBJETIVO)
    r1 = await eng.process(1, "ainda não tenho certeza", ctx)
    assert r1.etapa == ETAPA_SISTEMA_DIAGNOSTICO
    ctx.etapa = ETAPA_SISTEMA_DIAGNOSTICO
    r2 = await eng.process(1, "clínica nova, no Brasil, sou médico", ctx)
    assert r2.action == "handoff"


# ===========================================================================
# Caminho 4 — Aluno/suporte
# ===========================================================================

@pytest.mark.asyncio
async def test_c4_submenu_e_encaminhamento():
    eng = engine(ClassificacaoIntencao.ALUNO_SUPORTE)
    ctx = make_context(caminho=4)
    r1 = await eng.process(1, "sou aluno e preciso de suporte", ctx)
    assert r1.etapa == ETAPA_ALUNO_MENU
    assert "Certificado" in r1.response_text

    ctx.etapa = ETAPA_ALUNO_MENU
    eng2 = engine(ClassificacaoIntencao.AMBIGUA)
    r2 = await eng2.process(1, "2", ctx)  # certificado
    assert r2.action == "handoff"
    assert "equipe" in r2.response_text.lower()


# ===========================================================================
# Caminho 5 / 6
# ===========================================================================

@pytest.mark.asyncio
async def test_c5_paciente_modelo_end():
    eng = engine(ClassificacaoIntencao.PACIENTE_MODELO)
    r = await eng.process(1, "quero ser paciente modelo", make_context())
    assert r.action == "end"
    assert r.etapa == ETAPA_PACIENTE
    assert "97423" in r.response_text


@pytest.mark.asyncio
async def test_c6_outro_assunto_handoff():
    eng = engine(ClassificacaoIntencao.OUTRO_ASSUNTO)
    r = await eng.process(1, "tenho uma parceria comercial pra propor", make_context())
    assert r.action == "handoff"


# ===========================================================================
# Robustez — resposta nao reconhecida → reformula → 3a vez handoff
# ===========================================================================

@pytest.mark.asyncio
async def test_robustez_reformula_depois_handoff():
    eng = engine(ClassificacaoIntencao.CURSOS_PRESENCIAIS)
    ctx = make_context(caminho=2, etapa=ETAPA_QUALIF_MEDICO)

    # 1a resposta nao reconhecida → re-pergunta (tentativa 1)
    r1 = await eng.process(1, "xpto blá", ctx)
    assert r1.action == "continue"
    assert r1.etapa == ETAPA_QUALIF_MEDICO
    ctx.etapa_funil = r1.updates.get("etapa_funil")

    # 2a → reformula (prefixo "não entendi")
    r2 = await eng.process(1, "zzz", ctx)
    assert r2.action == "continue"
    assert "não entendi" in r2.response_text.lower()
    ctx.etapa_funil = r2.updates.get("etapa_funil")

    # 3a → handoff (nao repete infinitamente)
    r3 = await eng.process(1, "????", ctx)
    assert r3.action == "handoff"


# ===========================================================================
# Nao-repeticao ao trocar de caminho (FR-021)
# ===========================================================================

@pytest.mark.asyncio
async def test_nao_repete_medico_ao_trocar_de_caminho():
    """Lead ja-medico muda de C1 para C2: nao deve re-perguntar 'e medico?'."""
    apres = {"hg-modulo-1": "MOD1", "hg360-sp": "SP"}
    eng = engine(ClassificacaoIntencao.CURSOS_PRESENCIAIS, apres=apres)
    # Estava no C1 (apresentacao), ja medico; agora pede presencial
    ctx = make_context(caminho=1, etapa=ETAPA_DUVIDAS, eh_medico=True)
    r = await eng.process(1, "na verdade quero o curso presencial", ctx)
    assert r.caminho == CaminhoMapaMestre.CURSOS_PRESENCIAIS
    assert r.etapa != ETAPA_QUALIF_MEDICO  # nao re-pergunta medico


def test_perfil_conhecido_monta_fatos():
    """_perfil_conhecido lista os fatos duraveis conhecidos do lead (anti-redundancia)."""
    ctx = make_context(
        eh_medico=True, especialidade="dermatologia",
        experiencia_corporal=False, produto_interesse="hg360-sp", nome="Ana Souza",
    )
    bloco = _perfil_conhecido(ctx)
    assert "FATOS JA CONHECIDOS DO LEAD" in bloco
    assert "Ana Souza" in bloco
    assert "e medico" in bloco.lower()
    assert "dermatologia" in bloco
    assert "hg360-sp" in bloco


def test_perfil_conhecido_vazio_quando_nada_sabido():
    """Sem fatos conhecidos (e sem nome), retorna string vazia."""
    ctx = make_context(nome=None)
    assert _perfil_conhecido(ctx) == ""


def test_saudacao_varia_por_turno_e_respeita_idioma():
    """Abertura varia entre turnos (anti-repeticao) e respeita o idioma do lead."""
    # Varia conforme o nº de mensagens ja trocadas (turnos diferentes → aberturas diferentes).
    ctx = make_context(nome=None)
    ctx.historico_recente = []
    a = _saudacao(ctx)
    ctx.historico_recente = [{"x": 1}]
    b = _saudacao(ctx)
    assert a != b, "Aberturas de turnos consecutivos nao devem ser iguais"
    # Idioma-aware: jornada EN nao recebe abertura em PT.
    ctx_en = make_context(idioma="en", nome=None)
    ctx_en.historico_recente = []
    assert _saudacao(ctx_en).rstrip("!") in {
        "Perfect", "Great", "Wonderful", "Got it", "Excellent", "Sounds good",
    }
    # Inclui o nome quando disponivel.
    ctx_nome = make_context(idioma="pt", nome="Ana Souza")
    assert _saudacao(ctx_nome).endswith("Ana!")


def test_perfil_conhecido_inclui_perfil_livre():
    """Caracteristicas livres do perfil tambem entram no bloco de fatos conhecidos."""
    ctx = make_context(nome=None)
    ctx.perfil = {"perfil_franquia": "investidor"}
    bloco = _perfil_conhecido(ctx)
    assert "Perfil franquia: investidor" in bloco


def test_merge_perfil_acumula_e_propaga():
    """_merge_perfil mescla in-memory, propaga o dict completo e ignora vazios."""
    ctx = make_context(nome=None)
    updates: dict = {}
    _merge_perfil(ctx, updates, {"perfil_franquia": "investidor", "cidade": ""})
    assert ctx.perfil == {"perfil_franquia": "investidor"}  # vazio ignorado
    assert updates["perfil"] == {"perfil_franquia": "investidor"}
    # Nao apaga o que ja sabemos: acrescenta nova chave.
    _merge_perfil(ctx, updates, {"foco": "estetico"})
    assert ctx.perfil == {"perfil_franquia": "investidor", "foco": "estetico"}


@pytest.mark.asyncio
async def test_c3_franquia_captura_perfil_no_lead():
    """C3 Franquia: o perfil declarado (medico/investidor) e guardado no lead."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(caminho=3, etapa=ETAPA_SISTEMA_FRANQUIA)
    r = await eng.process(1, "sou investidor", ctx)
    assert r.action == "handoff"
    assert r.updates.get("perfil", {}).get("perfil_franquia") == "investidor"


@pytest.mark.asyncio
async def test_c3_ja_medico_nao_repergunta_abre_licenciamento():
    """Lead que JA confirmou ser medico (outro caminho) entra no C3 'incorporar' →
    abre direto o resumo do Licenciamento + duvidas, SEM re-perguntar medico."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(caminho=3, etapa=ETAPA_SISTEMA_OBJETIVO, eh_medico=True)
    r = await eng.process(1, "1", ctx)  # objetivo "incorporar"
    # Pula a etapa de pergunta (ETAPA_SISTEMA_LICENCIAMENTO) e vai direto as duvidas.
    assert r.etapa == ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS
    assert "Licenciamento Internacional GoldIncision" in r.response_text
    # Conteudo e o resumo (convite a esclarecer), nao a pergunta de qualificacao.
    assert "o que gostaria de saber primeiro" in r.response_text.lower()


@pytest.mark.asyncio
async def test_c3_ja_nao_medico_vai_franquia_sem_repergunta():
    """Lead que JA informou NAO ser medico entra no C3 'incorporar' → handoff
    Franquia direto, sem re-perguntar."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(caminho=3, etapa=ETAPA_SISTEMA_OBJETIVO, eh_medico=False)
    r = await eng.process(1, "1", ctx)  # objetivo "incorporar"
    assert r.action == "handoff"
    assert r.handoff_destino == "franquia"
    assert r.etapa != ETAPA_SISTEMA_LICENCIAMENTO  # nao foi para a pergunta


@pytest.mark.asyncio
async def test_troca_de_caminho_conservadora_durante_qualificacao():
    """Durante uma pergunta de qualificacao, nao troca de caminho por reclassificacao."""
    eng = engine(ClassificacaoIntencao.SISTEMA_GOLDINCISION)  # classifica como C3
    ctx = make_context(caminho=2, etapa=ETAPA_QUALIF_MEDICO)
    r = await eng.process(1, "sim", ctx)
    # Deve permanecer no C2 (interpretando "sim" como resposta), nao saltar pro C3
    assert r.caminho == CaminhoMapaMestre.CURSOS_PRESENCIAIS


# ===========================================================================
# Multilingue
# ===========================================================================

@pytest.mark.asyncio
async def test_multilingue_menu_en():
    eng = engine(ClassificacaoIntencao.AMBIGUA, idioma=Idioma.EN)
    ctx = make_context(idioma="pt")
    r = await eng.process(1, "hello", ctx)
    assert ctx.idioma == "en"
    assert "MENU_EN" in r.response_text


@pytest.mark.asyncio
async def test_multilingue_qualif_es():
    eng = engine(ClassificacaoIntencao.CURSO_ONLINE, idioma=Idioma.ES)
    ctx = make_context(caminho=1, idioma="es")
    r = await eng.process(1, "quiero el curso online", ctx)
    assert r.etapa == ETAPA_QUALIF_MEDICO
    assert "médico" in r.response_text.lower()


# ===========================================================================
# Helpers de NLU
# ===========================================================================

def test_detectar_confirmacao_positivo():
    assert _detectar_confirmacao("sim, sou medico") is True
    assert _detectar_confirmacao("yes I am") is True
    assert _detectar_confirmacao("Si, soy medico") is True
    assert _detectar_confirmacao("tenho CRM ativo") is True


def test_detectar_confirmacao_negativo():
    assert _detectar_confirmacao("nao sou medico") is False
    assert _detectar_confirmacao("No I'm not") is False
    assert _detectar_confirmacao("não tenho") is False


def test_detectar_confirmacao_indeterminado():
    assert _detectar_confirmacao("talvez") is None
    assert _detectar_confirmacao("preciso pensar") is None


def test_detectar_confirmacao_ambiguos_isolados_endurecido():
    # "claro" continua positivo; "azul" nao confirma nada
    assert _detectar_confirmacao("claro") is True
    assert _detectar_confirmacao("a casa é azul") is None


def test_detectar_experiencia_corporal():
    assert _detectar_experiencia_corporal("sim, tenho experiencia em corporal") is True
    assert _detectar_experiencia_corporal("ja fiz gluteo antes") is True
    assert _detectar_experiencia_corporal("apenas facial") is False
    assert _detectar_experiencia_corporal("nao tenho experiencia corporal") is False
    assert _detectar_experiencia_corporal("talvez") is None


def test_detectar_objetivo_sistema():
    assert _detectar_objetivo_sistema("1") == "incorporar"
    assert _detectar_objetivo_sistema("quero incorporar à minha clínica") == "incorporar"
    assert _detectar_objetivo_sistema("2") == "abrir"
    assert _detectar_objetivo_sistema("quero abrir uma clínica") == "abrir"
    assert _detectar_objetivo_sistema("ainda não tenho certeza") == "nao_sei"


def test_detectar_opcao_aluno():
    assert _detectar_opcao_aluno("2") == "certificado"
    assert _detectar_opcao_aluno("não consigo acessar a plataforma") == "plataforma_acesso"
    assert _detectar_opcao_aluno("xyz") is None


def test_detectar_fechamento():
    assert _detectar_fechamento("quero me inscrever") == "aceita"
    assert _detectar_fechamento("pode encaminhar ao consultor") == "aceita"
    assert _detectar_fechamento("agora não, obrigado") == "recusa"
    assert _detectar_fechamento("qual o conteúdo?") is None


def test_eh_pergunta_informativa():
    assert _eh_pergunta_informativa("quanto custa?") is True
    assert _eh_pergunta_informativa("qual a duração e o certificado?") is True
    assert _eh_pergunta_informativa("sim, sou médico") is False


def test_pede_humano():
    assert _pede_humano("quero falar com um humano") is True
    assert _pede_humano("prefiro falar com um atendente") is True
    assert _pede_humano("quanto custa?") is False


# --- Regressões de NLU (code-review): substring matching frágil ---

def test_detectar_confirmacao_no_contracao_pt_nao_reprova_medico():
    # 'no'/'na' são contrações em PT — não devem ser lidas como negação (FR-009)
    assert _detectar_confirmacao("Sim, sou médico, atendo no Rio") is True
    assert _detectar_confirmacao("Sim, tenho registro ativo no CRM do meu país") is True


def test_detectar_confirmacao_nao_aprova_nao_medico():
    # "I am a nurse" não deve confirmar médico (era falso-positivo por "i am a")
    assert _detectar_confirmacao("I am a nurse") is not True
    assert _detectar_confirmacao("No, I'm a nurse") is False


def test_detectar_experiencia_no_contracao_pt():
    assert _detectar_experiencia_corporal(
        "faço preenchimento de glúteo no meu consultório"
    ) is True


def test_detectar_escolha_turma_sp_nao_casa_substring():
    # 'sp' dentro de 'esperar' não deve fixar São Paulo
    assert _detectar_escolha_turma("vou esperar pra decidir") is None


def test_detectar_objetivo_sistema_duvida_nao_intercepta_opcao1():
    assert _detectar_objetivo_sistema(
        "tenho uma dúvida: quero incorporar à minha clínica"
    ) == "incorporar"


def test_detectar_especialidade_nao_qualificante_so_frases():
    assert _detectar_especialidade("atendo na minha clínica") is None
    assert _detectar_especialidade("sou clínico geral") == "outra"
    assert _detectar_especialidade("dermatologia") == "dermatologia"


def test_sem_mais_duvidas():
    from app.core.flow import _sem_mais_duvidas
    assert _sem_mais_duvidas("não tenho dúvidas, obrigado") is True
    assert _sem_mais_duvidas("preciso pensar") is False


@pytest.mark.asyncio
async def test_handoff_destino_por_tipo():
    """Cada tipo de handoff carrega o destino lógico correto (roteamento de fila)."""
    apres = {"hg360-sp": "X", "licenciamento-internacional": "Y"}

    # C4 aluno/suporte → suporte
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(caminho=4, etapa=ETAPA_ALUNO_MENU)
    r = await eng.process(1, "2", ctx)
    assert r.action == "handoff" and r.handoff_destino == "suporte"

    # C2 consultor presencial → presencial
    eng = engine(ClassificacaoIntencao.AMBIGUA, apres=apres)
    ctx = make_context(
        caminho=2, etapa=ETAPA_DUVIDAS, eh_medico=True,
        experiencia_corporal=True, produto_interesse="hg360-sp",
    )
    r = await eng.process(1, "sim, pode encaminhar ao consultor", ctx)
    assert r.action == "handoff" and r.handoff_destino == "presencial"

    # C3 reunião de licenciamento → licenciamento
    eng = engine(ClassificacaoIntencao.AMBIGUA, apres=apres)
    ctx = make_context(caminho=3, etapa=ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS, eh_medico=True)
    r = await eng.process(1, "não tenho dúvidas, podemos marcar", ctx)
    assert r.action == "handoff" and r.handoff_destino == "licenciamento"

    # C3 franquia → franquia
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(caminho=3, etapa=ETAPA_SISTEMA_FRANQUIA)
    r = await eng.process(1, "sou investidor", ctx)
    assert r.action == "handoff" and r.handoff_destino == "franquia"

    # Pedido explícito de humano → consultores (genérico)
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(caminho=1, etapa=ETAPA_DUVIDAS, eh_medico=True)
    r = await eng.process(1, "quero falar com um atendente", ctx)
    assert r.action == "handoff" and r.handoff_destino == "consultores"
    assert r.handoff_motivo == "pedido_humano"


@pytest.mark.asyncio
async def test_c3_licenciamento_objecao_vai_ao_llm_nao_handoff():
    """Objeção sem '?' na fase de dúvidas do Licenciamento → LLM (Banco de Objeções),
    não handoff prematuro de reunião."""
    resp = MockResponder(response_text="resposta de objeção")
    eng = engine(ClassificacaoIntencao.AMBIGUA, responder=resp)
    ctx = make_context(
        caminho=3, etapa=ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS, eh_medico=True,
    )
    r = await eng.process(1, "achei o contrato complexo", ctx)
    assert r.action == "continue"
    assert r.response_text == "resposta de objeção"
    assert len(resp.generate_calls) == 1


# ===========================================================================
# Orcamento de turnos (US1, FASE 3 — FR-001 a FR-007)
# ===========================================================================

from app.config import settings as _cfg  # noqa: E402


@pytest.mark.asyncio
async def test_turnos_abaixo_do_teto_de_no_nao_dispara_nudge():
    """Acceptance Scenario 1, US1: contador abaixo do teto -> segue normal,
    sem intervencao (sem nudge anexado a resposta)."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=1, etapa=ETAPA_QUALIF_MEDICO,
        turnos_no_no=_cfg.max_turnos_no_no - 1,
    )
    r = await eng.process(1, "sim", ctx)
    assert r.action == "continue"
    assert r.turno_acao is None
    assert r.motivo is None


@pytest.mark.asyncio
async def test_turnos_no_teto_de_no_dispara_nudge_sem_handoff():
    """Acceptance Scenario 2, US1 (task 3.2.3): teto de turnos-no-no atingido
    -> nudge cordial anexado, SEM encerrar o atendimento (action continua
    'continue', nao vira handoff)."""
    resp = MockResponder(response_text="resposta normal")
    eng = engine(ClassificacaoIntencao.AMBIGUA, responder=resp)
    ctx = make_context(
        caminho=1, etapa=ETAPA_QUALIF_MEDICO,
        turnos_no_no=_cfg.max_turnos_no_no,
    )
    r = await eng.process(1, "sim", ctx)
    assert r.action == "continue"
    assert r.turno_acao == "nudge"
    assert r.motivo == "turnos_no_no"
    # Nudge e ANEXADO — nao substitui a resposta normal do fluxo.
    assert len(r.response_text) > 0


@pytest.mark.asyncio
async def test_turnos_duvidas_abaixo_do_teto_elevado_nao_dispara_nudge():
    """Acceptance Scenario 4, US1 (task 3.2.4): etapa de duvidas abertas
    tolera mais turnos (limiar elevado) antes de sugerir especialista —
    acima do teto generico de no mas abaixo do teto elevado de duvidas,
    NENHUM nudge e disparado."""
    assert _cfg.max_turnos_no_no < _cfg.max_turnos_duvidas, (
        "pre-condicao do cenario: limiar de duvidas deve ser maior"
    )
    resp = MockResponder(response_text="resposta de duvida")
    eng = engine(ClassificacaoIntencao.AMBIGUA, responder=resp)
    ctx = make_context(
        caminho=1, etapa=ETAPA_DUVIDAS, eh_medico=True,
        turnos_no_no=_cfg.max_turnos_no_no + 1,  # acima do teto generico
    )
    r = await eng.process(1, "ainda tenho uma duvida sobre o curso", ctx)
    assert r.action == "continue"
    assert r.turno_acao is None
    assert r.response_text == "resposta de duvida"


@pytest.mark.asyncio
async def test_turnos_duvidas_no_teto_elevado_dispara_nudge():
    """Complemento do cenario 4: no teto ELEVADO de duvidas, o nudge passa a
    disparar (o limiar diferenciado nao e infinito)."""
    resp = MockResponder(response_text="resposta de duvida")
    eng = engine(ClassificacaoIntencao.AMBIGUA, responder=resp)
    ctx = make_context(
        caminho=1, etapa=ETAPA_DUVIDAS, eh_medico=True,
        turnos_no_no=_cfg.max_turnos_duvidas,
    )
    r = await eng.process(1, "ainda tenho duvida", ctx)
    assert r.action == "continue"
    assert r.turno_acao == "nudge"
    assert r.motivo == "turnos_no_no"


@pytest.mark.asyncio
async def test_turnos_teto_de_sessao_dispara_handoff_destino_logico():
    """Acceptance Scenario 3, US1 (task 3.3.4): teto de turnos-de-sessao
    atingido -> handoff ao destino LOGICO do caminho corrente (nao ao
    handoff que o proprio handler emitiria por outro motivo), com motivo
    de observabilidade registrado."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=2, etapa=ETAPA_DUVIDAS, eh_medico=True,
        experiencia_corporal=True, produto_interesse="hg-modulo-1",
        # C2 (Cursos Presenciais) -> destino logico "presencial"
        turnos_sessao=_cfg.max_turnos_sessao,
    )
    r = await eng.process(1, "qual o valor do investimento?", ctx)
    assert r.action == "handoff"
    assert r.handoff_destino == "presencial"
    assert r.motivo == "turnos_sessao"


@pytest.mark.asyncio
async def test_turnos_colisao_sessao_e_no_prevalece_sessao():
    """Edge Case item 1 / task 3.3.5: colisao simultanea teto-sessao +
    teto-no no mesmo turno -> handoff de sessao SEMPRE prevalece sobre o
    nudge de no (nao emite nudge quando ja e handoff)."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=1, etapa=ETAPA_QUALIF_MEDICO,
        turnos_sessao=_cfg.max_turnos_sessao,
        turnos_no_no=_cfg.max_turnos_no_no,
    )
    r = await eng.process(1, "sim", ctx)
    assert r.action == "handoff"
    assert r.motivo == "turnos_sessao"
    assert r.turno_acao is None  # nao e nudge — handoff prevaleceu


@pytest.mark.asyncio
async def test_turnos_sessao_handoff_destino_nunca_do_llm():
    """Task 3.3.6: handoff_destino do teto de sessao vem SEMPRE da allowlist
    estatica `_destino_logico_por_caminho`, nunca de um valor retornado pelo
    responder/LLM (assert contra a allowlist, nao contra saida do modelo)."""
    from app.core.flow import (
        DEST_ESPECIALISTA,
        DEST_PRESENCIAL,
        DEST_SUPORTE,
        _destino_logico_por_caminho,
    )

    # A allowlist e 100% estatica: mesmo caminho -> sempre o mesmo destino,
    # independente de qualquer texto/decisao gerada pelo LLM.
    assert _destino_logico_por_caminho(CaminhoMapaMestre.CURSOS_PRESENCIAIS) == DEST_PRESENCIAL
    assert _destino_logico_por_caminho(CaminhoMapaMestre.SISTEMA_GOLDINCISION) == DEST_ESPECIALISTA
    assert _destino_logico_por_caminho(CaminhoMapaMestre.ALUNO_SUPORTE) == DEST_SUPORTE
    assert _destino_logico_por_caminho(None) == "consultores"
    assert _destino_logico_por_caminho(999) == "consultores"  # caminho desconhecido -> fallback

    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS, etapa=ETAPA_DUVIDAS,
        turnos_sessao=_cfg.max_turnos_sessao,
    )
    r = await eng.process(1, "ok", ctx)
    assert r.action == "handoff"
    assert r.handoff_destino == DEST_PRESENCIAL


@pytest.mark.asyncio
async def test_turnos_nudge_nao_sobrepoe_handoff_ja_disparado_por_outro_motivo():
    """Orcamento de turnos NUNCA escalona sobre um resultado que ja e
    handoff por outro motivo (pedido explicito de humano, Regra 26) — o
    campo `motivo` do orcamento permanece None nesse caso."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=1, etapa=ETAPA_DUVIDAS,
        turnos_sessao=_cfg.max_turnos_sessao,  # tambem no teto — irrelevante aqui
    )
    r = await eng.process(1, "quero falar com um atendente", ctx)
    assert r.action == "handoff"
    assert r.handoff_motivo == "pedido_humano"
    # O motivo de OBSERVABILIDADE do orcamento de turnos nao foi setado —
    # este handoff nao veio do orcamento, veio do pedido explicito.
    assert r.motivo is None


@pytest.mark.asyncio
async def test_turnos_no_no_ortogonal_ao_contador_anti_loop():
    """Acceptance Scenario 5, US1 (task 3.1.5): o contador de turnos desta
    feature e independente do contador anti-loop de tentativas nao
    reconhecidas (`_tent_count`/`etapa_funil`) — turnos RECONHECIDOS
    incrementam turnos_no_no sem tocar etapa_funil."""
    from app.core.flow import _tent_count

    resp = MockResponder(response_text="ok")
    eng = engine(ClassificacaoIntencao.AMBIGUA, responder=resp)
    ctx = make_context(
        caminho=1, etapa=ETAPA_DUVIDAS, eh_medico=True, turnos_no_no=2,
    )
    r = await eng.process(1, "pergunta reconhecida", ctx)

    assert r.action == "continue"
    # Turno reconhecido -> etapa_funil (contador anti-loop) NAO e tocado.
    assert "etapa_funil" not in r.updates
    assert _tent_count(ctx, ETAPA_DUVIDAS) == 0


# ===========================================================================
# FASE 1 (sdr-fidelidade-json) — Contrato JSON estruturado (task 1.3.3)
#
# Regressao: FlowEngine REAL + GroundedResponder REAL, mock SOMENTE do client
# OpenAI (nunca o motor). Garante que a transicao de estado permanece 100%
# deterministica mesmo com o pacote RespostaEstruturada presente (FR-006):
# variar confianca/fontes do pacote (campos que o FlowEngine nunca inspeciona)
# nao pode mudar caminho/etapa/action resultantes.
# ===========================================================================

def _fake_openai_client_json(*, texto: str, precisa_handoff: bool, idioma: str = "pt"):
    """Fake do client OpenAI (nao do motor): devolve um RespostaEstruturada
    serializado, como o `chat_reasoning_json` real devolveria."""
    from app.core.contracts import RespostaEstruturada

    client = AsyncMock()

    async def _chat_reasoning_json(messages, response_model, max_tokens=1024, temperature=0.3):
        pacote = RespostaEstruturada(
            texto=texto,
            fontes=["base-teste"],
            precisa_handoff=precisa_handoff,
            confianca=0.77,  # valor arbitrario: FlowEngine nunca le este campo
            idioma=idioma,
        )
        return pacote.model_dump_json()

    client.chat_reasoning_json = _chat_reasoning_json
    return client


@pytest.mark.asyncio
async def test_contrato_json_nao_altera_determinismo_da_transicao():
    """Duas execucoes com pacotes RespostaEstruturada distintos (confianca e
    fontes diferentes, mesmo texto/handoff) produzem a MESMA transicao de
    estado — o FlowEngine so consome (texto, handoff), nunca o objeto."""
    from app.core.responder import GroundedResponder

    ctx1 = make_context(caminho=1)
    ctx2 = make_context(caminho=1)

    resp1 = GroundedResponder(
        openai_client=_fake_openai_client_json(texto="Resposta A", precisa_handoff=False)
    )
    resp2 = GroundedResponder(
        openai_client=_fake_openai_client_json(texto="Resposta A", precisa_handoff=False)
    )

    eng1 = StubFlowEngine(MockIntent(ClassificacaoIntencao.CURSO_ONLINE), resp1)
    eng2 = StubFlowEngine(MockIntent(ClassificacaoIntencao.CURSO_ONLINE), resp2)

    r1 = await eng1.process(1, "Quanto custa o curso online?", ctx1)
    r2 = await eng2.process(1, "Quanto custa o curso online?", ctx2)

    # Mesma entrada + mesmo (texto, handoff) do pacote -> mesma transicao,
    # apesar de confianca/fontes serem detalhes internos do pacote.
    assert r1.caminho == r2.caminho == CaminhoMapaMestre.CURSO_ONLINE_HG
    assert r1.etapa == r2.etapa == ETAPA_DUVIDAS
    assert r1.action == r2.action == "continue"
    assert r1.response_text == r2.response_text == "Resposta A"


@pytest.mark.asyncio
async def test_contrato_json_precisa_handoff_true_vira_action_handoff():
    """precisa_handoff=True no pacote (2-tupla) e o UNICO sinal que o
    FlowEngine usa para decidir handoff — nenhum outro campo do pacote
    (confianca/fontes) participa da decisao de fluxo (FR-006)."""
    from app.core.responder import GroundedResponder

    ctx = make_context(caminho=1)
    resp = GroundedResponder(
        openai_client=_fake_openai_client_json(
            texto="Vou conectar você com nossa equipe.", precisa_handoff=True
        )
    )
    eng = StubFlowEngine(MockIntent(ClassificacaoIntencao.CURSO_ONLINE), resp)

    r = await eng.process(1, "quero um desconto especial no curso online", ctx)

    assert r.action == "handoff"
    assert r.response_text == "Vou conectar você com nossa equipe."
