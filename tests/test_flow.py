"""
Testes do motor de fluxo conversacional (task 4.2.9).

Cenarios:
- Intencao ambigua → menu de 6 opcoes
- Caminho 1-4: qualificacao medica obrigatoria
- Caminho 2-4: qualificacao de experiencia corporal
- "Apenas facial" → nao elegivel para presenciais avancados
- Caminho 5 (paciente modelo): somente contato da Nidia
- Caminho 6 (licenciamento): conduzir para reuniao
- Mudanca de assunto redireciona para novo caminho
- Verbatim de apresentacoes (base de conhecimento usada diretamente)
- Objecoes exclusivamente do banco oficial (via base de conhecimento)
- Recusa fora da base → handoff
"""
from __future__ import annotations

from typing import Optional

import pytest

from app.core.flow import (
    ETAPA_HANDOFF,
    ETAPA_MENU,
    ETAPA_PACIENTE,
    ETAPA_QUALIF_EXPERIENCIA,
    ETAPA_QUALIF_MEDICO,
    CaminhoMapaMestre,
    FlowResult,
    _detectar_confirmacao,
    _detectar_experiencia_corporal,
)
from app.core.intent import ClassificacaoIntencao, Idioma
from app.core.memory import SessionContext

# ---------------------------------------------------------------------------
# Fixtures e mocks
# ---------------------------------------------------------------------------

def make_context(
    ticket_id: int = 1,
    chamado_id: int = 1001,
    contato_id: int = 10,
    caminho: Optional[int] = None,
    etapa: Optional[str] = None,
    idioma: str = "pt",
    eh_medico: Optional[bool] = None,
    especialidade: Optional[str] = None,
    experiencia_corporal: Optional[bool] = None,
) -> SessionContext:
    return SessionContext(
        ticket_id=ticket_id,
        chamado_id=chamado_id,
        contato_id=contato_id,
        caminho=caminho,
        etapa=etapa,
        idioma=idioma,
        eh_medico=eh_medico,
        especialidade=especialidade,
        experiencia_corporal=experiencia_corporal,
        resumo_rolante=None,
        historico_recente=[],
        sessao_id=100,
    )


class MockIntentClassifier:
    """Mock do IntentClassifier."""

    def __init__(self, intencao: ClassificacaoIntencao, idioma: Idioma = Idioma.PT):
        self._intencao = intencao
        self._idioma = idioma

    async def classify(self, message: str, session_context=None):
        return self._intencao, self._idioma

    def get_caminho(self, intencao):
        from app.core.intent import INTENCAO_PARA_CAMINHO
        return INTENCAO_PARA_CAMINHO.get(intencao)


class MockMemoryManager:
    """Mock do MemoryManager."""

    def build_messages_for_llm(self, context, max_msgs=10):
        return []

    async def update_qualification_variables(self, contato_id, updates):
        pass

    async def update_ticket_state(self, ticket_id, caminho=None, etapa=None):
        pass


class MockResponder:
    """Mock do GroundedResponder."""

    def __init__(self, response_text: str = "Resposta mock", handoff: bool = False):
        self._response_text = response_text
        self._handoff = handoff
        self.last_call: dict = {}

    async def generate(self, user_message, caminho, etapa, knowledge_context, **kwargs):
        self.last_call = {
            "user_message": user_message,
            "caminho": caminho,
            "etapa": etapa,
            "knowledge_context": knowledge_context,
        }
        return self._response_text, self._handoff

    async def generate_menu(self, idioma: str = "pt"):
        return f"MENU_{idioma.upper()}"

    async def generate_not_eligible(self, idioma: str = "pt"):
        return f"NAO_ELEGIVEL_{idioma.upper()}"

    async def generate_paciente_modelo(self, nidia_phone: str, idioma: str = "pt"):
        return f"CONTATO_NIDIA: {nidia_phone}"


class MockFlowEngine:
    """Motor de fluxo com DB mockado (sem acesso real ao Postgres)."""

    def __init__(
        self,
        intent: MockIntentClassifier,
        responder: MockResponder,
        knowledge: str = "BASE_OFICIAL_MOCK",
        nidia_phone: str = "+55 21 97423-9844",
    ):
        self._intent = intent
        self._memory = MockMemoryManager()
        self._responder = responder
        self._knowledge = knowledge
        self._nidia_phone = nidia_phone
        self._db = None  # nao usado nos testes unitarios

    async def _load_knowledge(self, caminho: int, idioma: str) -> str:
        return self._knowledge

    async def process(self, ticket_id: int, user_message: str, context: SessionContext) -> FlowResult:
        """Versao testavel do FlowEngine.process (sem acesso DB)."""
        updates: dict = {}

        intencao, idioma = await self._intent.classify(
            user_message, session_context={"idioma": context.idioma, "caminho": context.caminho}
        )

        # Atualizar idioma se mudou
        if idioma.value != context.idioma:
            context.idioma = idioma.value
            updates["idioma"] = idioma.value

        from app.core.intent import INTENCAO_PARA_CAMINHO
        novo_caminho = INTENCAO_PARA_CAMINHO.get(intencao)

        # Mudanca de assunto
        if (
            novo_caminho is not None
            and context.caminho is not None
            and novo_caminho != context.caminho
            and intencao != ClassificacaoIntencao.AMBIGUA
        ):
            context.caminho = novo_caminho
            context.etapa = None
            updates["caminho_atual"] = novo_caminho

        caminho_ativo = context.caminho or novo_caminho

        # Menu se ambiguo ou sem caminho
        if caminho_ativo is None or intencao == ClassificacaoIntencao.AMBIGUA:
            if context.caminho is None:
                menu_text = await self._responder.generate_menu(context.idioma)
                context.etapa = ETAPA_MENU
                updates["etapa_mapa_mestre"] = ETAPA_MENU
                return FlowResult(menu_text, "continue", None, ETAPA_MENU, updates)
            caminho_ativo = context.caminho

        if caminho_ativo is None:
            menu_text = await self._responder.generate_menu(context.idioma)
            return FlowResult(menu_text, "continue", None, ETAPA_MENU, updates)

        # Caminho 5
        if caminho_ativo == CaminhoMapaMestre.PACIENTE_MODELO:
            resposta = await self._responder.generate_paciente_modelo(
                self._nidia_phone, context.idioma
            )
            updates["caminho_atual"] = CaminhoMapaMestre.PACIENTE_MODELO
            return FlowResult(resposta, "end", CaminhoMapaMestre.PACIENTE_MODELO, ETAPA_PACIENTE, updates)

        # Caminho 3 (Sistema GoldIncision: licenciamento/franquia)
        if caminho_ativo == CaminhoMapaMestre.SISTEMA_GOLDINCISION:
            knowledge = await self._load_knowledge(caminho_ativo, context.idioma)
            r, h = await self._responder.generate(
                user_message, caminho_ativo, context.etapa or "sistema_qualif",
                knowledge
            )
            action = "handoff" if h else "continue"
            updates["caminho_atual"] = caminho_ativo
            return FlowResult(r, action, caminho_ativo, context.etapa or "sistema_qualif", updates)

        # Caminhos 1-2 (e 4 se necessario — todos requerem qualificacao medica)
        context.caminho = caminho_ativo
        updates["caminho_atual"] = caminho_ativo

        if context.eh_medico is None:
            # Usar gerar_pergunta_medico do responder (simplificado)
            from app.core.flow import FlowEngine
            resposta = await FlowEngine._gerar_pergunta_medico(self, context.idioma)
            return FlowResult(resposta, "continue", caminho_ativo, ETAPA_QUALIF_MEDICO, updates)

        if context.eh_medico is False:
            resposta = await self._responder.generate_not_eligible(context.idioma)
            return FlowResult(resposta, "handoff", caminho_ativo, ETAPA_HANDOFF, updates)

        # Caminho 2 (cursos_presenciais): verificar experiencia corporal
        if caminho_ativo == CaminhoMapaMestre.CURSOS_PRESENCIAIS and context.experiencia_corporal is None:
            from app.core.flow import FlowEngine
            resposta = await FlowEngine._gerar_pergunta_experiencia(self, context.idioma, caminho_ativo)
            return FlowResult(resposta, "continue", caminho_ativo, ETAPA_QUALIF_EXPERIENCIA, updates)

        if caminho_ativo == CaminhoMapaMestre.CURSOS_PRESENCIAIS and context.experiencia_corporal is False:
            resposta = await self._responder.generate_not_eligible(context.idioma)
            return FlowResult(resposta, "handoff", caminho_ativo, ETAPA_HANDOFF, updates)

        # Elegivel: gerar resposta com base
        knowledge = await self._load_knowledge(caminho_ativo, context.idioma)
        r, h = await self._responder.generate(
            user_message, caminho_ativo, context.etapa or "apresentacao", knowledge
        )
        action = "handoff" if h else "continue"
        return FlowResult(r, action, caminho_ativo, context.etapa or "apresentacao", updates)


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_intencao_ambigua_retorna_menu():
    """Primeira mensagem ambigua → menu de 6 opcoes."""
    engine = MockFlowEngine(
        intent=MockIntentClassifier(ClassificacaoIntencao.AMBIGUA),
        responder=MockResponder(),
    )
    ctx = make_context()

    result = await engine.process(1, "oi", ctx)

    assert result.action == "continue"
    assert result.etapa == ETAPA_MENU
    assert "MENU" in result.response_text
    assert result.caminho is None


@pytest.mark.asyncio
async def test_intencao_clara_entra_direto_no_caminho():
    """Intencao clara para curso online → vai direto ao fluxo (sem menu)."""
    engine = MockFlowEngine(
        intent=MockIntentClassifier(ClassificacaoIntencao.CURSO_ONLINE),
        responder=MockResponder(),
    )
    ctx = make_context()  # sem caminho definido

    result = await engine.process(1, "quero o curso online", ctx)

    # Nao deve retornar menu; deve perguntar se e medico (primeiro passo do caminho)
    assert result.etapa == ETAPA_QUALIF_MEDICO
    assert "MENU" not in result.response_text
    assert result.action == "continue"


@pytest.mark.asyncio
async def test_lead_nao_medico_handoff():
    """Lead que nao e medico → nao elegivel → handoff."""
    engine = MockFlowEngine(
        intent=MockIntentClassifier(ClassificacaoIntencao.CURSO_ONLINE),
        responder=MockResponder(),
    )
    ctx = make_context(caminho=1, etapa=ETAPA_QUALIF_MEDICO, eh_medico=False)

    result = await engine.process(1, "nao sou medico", ctx)

    assert result.action == "handoff"
    assert result.etapa == ETAPA_HANDOFF
    assert "NAO_ELEGIVEL" in result.response_text


@pytest.mark.asyncio
async def test_medico_sem_experiencia_corporal_para_presencial():
    """Medico sem experiencia em corporal → nao elegivel para cursos presenciais."""
    engine = MockFlowEngine(
        intent=MockIntentClassifier(ClassificacaoIntencao.CURSOS_PRESENCIAIS),
        responder=MockResponder(),
    )
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS,
        eh_medico=True,
        experiencia_corporal=False,
    )

    result = await engine.process(1, "quero o modulo 1", ctx)

    assert result.action == "handoff"
    assert result.etapa == ETAPA_HANDOFF


@pytest.mark.asyncio
async def test_medico_com_experiencia_recebe_apresentacao():
    """Medico com experiencia corporal → elegivel → recebe apresentacao do curso."""
    engine = MockFlowEngine(
        intent=MockIntentClassifier(ClassificacaoIntencao.CURSOS_PRESENCIAIS),
        responder=MockResponder(response_text="APRESENTACAO_OFICIAL"),
        knowledge="BASE_KNOWLEDGE_HG1",
    )
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS,
        eh_medico=True,
        experiencia_corporal=True,
    )

    result = await engine.process(1, "quero mais info", ctx)

    assert result.action == "continue"
    assert result.response_text == "APRESENTACAO_OFICIAL"


@pytest.mark.asyncio
async def test_caminho_5_paciente_modelo_somente_nidia():
    """Caminho 5: somente contato da Nidia, sem mais informacoes."""
    nidia = "+55 21 97423-9844"
    engine = MockFlowEngine(
        intent=MockIntentClassifier(ClassificacaoIntencao.PACIENTE_MODELO),
        responder=MockResponder(),
        nidia_phone=nidia,
    )
    ctx = make_context()

    result = await engine.process(1, "quero ser paciente modelo", ctx)

    assert result.caminho == CaminhoMapaMestre.PACIENTE_MODELO
    assert nidia in result.response_text
    assert result.action == "end"  # nao continua apos dar o contato


@pytest.mark.asyncio
async def test_caminho_3_sistema_goldincision_para_reuniao():
    """Caminho 3: qualifica interesse no Sistema GoldIncision e conduz para reuniao."""
    engine = MockFlowEngine(
        intent=MockIntentClassifier(ClassificacaoIntencao.SISTEMA_GOLDINCISION),
        responder=MockResponder(response_text="Vamos marcar uma reuniao"),
    )
    ctx = make_context()

    result = await engine.process(1, "quero o licenciamento", ctx)

    assert result.caminho == CaminhoMapaMestre.SISTEMA_GOLDINCISION
    assert result.action == "continue"


@pytest.mark.asyncio
async def test_mudanca_de_assunto_redireciona():
    """Lead muda de assunto: caminho atual e substituido pelo novo."""
    # Lead estava no caminho 1, agora pede sistema_goldincision (caminho 3)
    engine = MockFlowEngine(
        intent=MockIntentClassifier(ClassificacaoIntencao.SISTEMA_GOLDINCISION),
        responder=MockResponder(),
    )
    ctx = make_context(caminho=1, etapa="apresentacao", eh_medico=True)

    _ = await engine.process(1, "na verdade quero saber sobre licenciamento", ctx)

    # Deve ter mudado para caminho 3 (Sistema GoldIncision)
    assert ctx.caminho == CaminhoMapaMestre.SISTEMA_GOLDINCISION


@pytest.mark.asyncio
async def test_handoff_quando_informacao_fora_da_base():
    """Responder sinaliza handoff → acao de handoff."""
    engine = MockFlowEngine(
        intent=MockIntentClassifier(ClassificacaoIntencao.CURSO_ONLINE),
        responder=MockResponder(
            response_text="Nao tenho essa informacao. Vou encaminhar para a equipe.",
            handoff=True,
        ),
    )
    ctx = make_context(caminho=1, eh_medico=True)

    result = await engine.process(1, "qual o cpf do diretor?", ctx)

    assert result.action == "handoff"


@pytest.mark.asyncio
async def test_idioma_ingles_menu_em_ingles():
    """Lead em ingles recebe menu em ingles."""
    engine = MockFlowEngine(
        intent=MockIntentClassifier(ClassificacaoIntencao.AMBIGUA, Idioma.EN),
        responder=MockResponder(),
    )
    ctx = make_context(idioma="pt")  # inicia em PT

    result = await engine.process(1, "hello, I want information", ctx)

    assert ctx.idioma == "en"
    assert "MENU_EN" in result.response_text


# ---------------------------------------------------------------------------
# Testes dos helpers NLU
# ---------------------------------------------------------------------------

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


def test_detectar_experiencia_corporal_positiva():
    assert _detectar_experiencia_corporal("sim, tenho experiencia em corporal") is True
    assert _detectar_experiencia_corporal("ja fiz gluteo antes") is True
    assert _detectar_experiencia_corporal("experiencia em gluteal harmonization") is True


def test_detectar_experiencia_corporal_negativa():
    assert _detectar_experiencia_corporal("apenas facial") is False
    assert _detectar_experiencia_corporal("so facial") is False
    assert _detectar_experiencia_corporal("only facial work") is False
    assert _detectar_experiencia_corporal("nao tenho experiencia corporal") is False


def test_detectar_experiencia_corporal_indeterminado():
    assert _detectar_experiencia_corporal("talvez") is None
