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
    _pede_humano,
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

    # Confirma medico → apresenta licenciamento
    ctx.etapa = ETAPA_SISTEMA_LICENCIAMENTO
    r2 = await eng.process(1, "sim, sou médico", ctx)
    assert r2.etapa == ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS
    assert "APRES_LICENCIAMENTO" in r2.response_text

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
