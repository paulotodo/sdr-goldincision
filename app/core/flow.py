"""
Motor do Mapa Mestre — orquestra os 6 caminhos do fluxo conversacional.

Principios:
- Anti-alucinacao rigida: so Base Oficial como fonte (Principio II)
- Hierarquia: Mapa Mestre → Base → Objecoes → FAQ
- Lacuna fora da base → recusa + handoff imediato
- Elegibilidade medica inflexivel (FR-009)
- Apresentacoes verbatim (FR-010), objecoes EXCLUSIVAMENTE do Banco Oficial (FR-011)
- Identidade "Consultor Virtual Oficial" (FR-013)
- Blocos curtos, 1 pergunta/msg (FR-015)
- Mudanca de assunto redireciona imediatamente (Mapa Mestre)
"""
from __future__ import annotations

import logging
from enum import IntEnum
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.intent import ClassificacaoIntencao, IntentClassifier, INTENCAO_PARA_CAMINHO
from app.core.memory import MemoryManager, SessionContext
from app.core.responder import GroundedResponder
from app.repository.models import (
    Curso,
    CursoApresentacao,
    CursoObjecao,
    CursoTurma,
    CursoLink,
    Ticket,
)

logger = logging.getLogger(__name__)


class CaminhoMapaMestre(IntEnum):
    """
    Os 6 caminhos do Mapa Mestre de Atendimento GoldIncision.
    """
    CURSO_ONLINE_HG = 1       # Curso Online Harmonizacao Glutea
    HG_MODULO_1 = 2           # HG Modulo 1 (presencial SP)
    HG360_SP = 3              # HG360 Sao Paulo (28-30/08/2026)
    HG360_BARCELONA = 4       # HG360 Barcelona (24-25/07/2026)
    PACIENTE_MODELO = 5       # Lead quer ser paciente modelo (Nidia)
    LICENCIAMENTO_FRANQUIA = 6  # Licenciamento / Franquia


# Slugs dos cursos mapeados por caminho
_CAMINHO_PARA_SLUG: dict[int, str] = {
    1: "curso-online-hg",
    2: "hg-modulo-1",
    3: "hg360-sp",
    4: "hg360-barcelona",
    5: None,  # paciente modelo nao tem curso
    6: "licenciamento-internacional",
}

# Etapas finas por caminho
ETAPA_QUALIF_MEDICO = "qualif_medico"
ETAPA_QUALIF_EXPERIENCIA = "qualif_experiencia"
ETAPA_APRESENTACAO = "apresentacao"
ETAPA_OBJECAO = "objecao"
ETAPA_LINK = "link"
ETAPA_PACIENTE = "paciente_modelo"
ETAPA_LICENCIAMENTO = "licenciamento_qualif"
ETAPA_MENU = "menu"
ETAPA_HANDOFF = "handoff"

# Numero da Nidia (fallback; ideal: carregar de settings)
_NIDIA_DEFAULT = "+55 21 97423-9844"


class FlowResult:
    """Resultado do processamento de uma mensagem."""

    def __init__(
        self,
        response_text: str,
        action: str,  # "continue" | "handoff" | "end"
        caminho: Optional[int],
        etapa: Optional[str],
        updates: Optional[dict] = None,
    ):
        self.response_text = response_text
        self.action = action
        self.caminho = caminho
        self.etapa = etapa
        self.updates = updates or {}


class FlowEngine:
    """
    Motor de fluxo conversacional baseado no Mapa Mestre.

    Responsabilidades:
    - Identificar/confirmar intencao do lead
    - Verificar elegibilidade (FR-009)
    - Orquestrar chamadas ao responder com grounding na base
    - Determinar handoff quando necessario
    - Garantir 1 pergunta por mensagem (FR-015)
    - Nao repetir perguntas ja respondidas (FR-021)
    """

    def __init__(
        self,
        db_session: AsyncSession,
        intent_classifier: IntentClassifier,
        memory_manager: MemoryManager,
        responder: GroundedResponder,
        nidia_phone: str = _NIDIA_DEFAULT,
    ) -> None:
        self._db = db_session
        self._intent = intent_classifier
        self._memory = memory_manager
        self._responder = responder
        self._nidia_phone = nidia_phone

    async def process(
        self, ticket_id: int, user_message: str, context: SessionContext
    ) -> FlowResult:
        """
        Processa mensagem do lead e retorna FlowResult com resposta + acao.

        Sequencia:
        1. Detectar idioma e atualizar se mudou
        2. Se caminho nao definido → classificar intencao
        3. Se intencao clara → entrar no caminho diretamente
        4. Se ambigua → apresentar menu
        5. Dentro do caminho: verificar etapa e avançar
        6. Detectar objecoes e responder com banco oficial

        Returns:
            FlowResult com resposta, acao e atualizacoes de estado
        """
        updates: dict = {}

        # 1. Classificar intencao / detectar idioma
        intencao, idioma = await self._intent.classify(
            user_message,
            session_context={
                "idioma": context.idioma,
                "caminho": context.caminho,
                "etapa": context.etapa,
            },
        )

        # Atualizar idioma se mudou (US5-AS5)
        if idioma.value != context.idioma:
            logger.info(
                "flow: idioma alterado %s → %s ticket_id=%s",
                context.idioma,
                idioma.value,
                ticket_id,
            )
            context.idioma = idioma.value
            updates["idioma"] = idioma.value

        # 2. Verificar se mensagem redireciona para novo caminho
        novo_caminho = INTENCAO_PARA_CAMINHO.get(intencao)
        if (
            novo_caminho is not None
            and context.caminho is not None
            and novo_caminho != context.caminho
            and intencao != ClassificacaoIntencao.AMBIGUA
        ):
            # Mudanca de assunto: redirecionar imediatamente (Mapa Mestre)
            logger.info(
                "flow: mudanca de caminho %s → %s ticket_id=%s",
                context.caminho,
                novo_caminho,
                ticket_id,
            )
            context.caminho = novo_caminho
            context.etapa = None
            updates["caminho_atual"] = novo_caminho
            updates["etapa_mapa_mestre"] = None

        # 3. Determinar o caminho ativo
        caminho_ativo = context.caminho or novo_caminho

        # 4. Se sem caminho ou intencao ambigua → menu
        if caminho_ativo is None or intencao == ClassificacaoIntencao.AMBIGUA:
            if context.caminho is None:
                # Primeira interacao sem intencao clara
                menu_text = await self._responder.generate_menu(context.idioma)
                context.caminho = None
                context.etapa = ETAPA_MENU
                updates["etapa_mapa_mestre"] = ETAPA_MENU
                return FlowResult(
                    response_text=menu_text,
                    action="continue",
                    caminho=None,
                    etapa=ETAPA_MENU,
                    updates=updates,
                )
            # Ja tem caminho: intencao ambigua pode ser objecao ou continuacao
            caminho_ativo = context.caminho

        # Garantir caminho valido
        if caminho_ativo is None:
            menu_text = await self._responder.generate_menu(context.idioma)
            return FlowResult(
                response_text=menu_text,
                action="continue",
                caminho=None,
                etapa=ETAPA_MENU,
                updates=updates,
            )

        # 5. Processar caminho especifico
        if caminho_ativo == CaminhoMapaMestre.PACIENTE_MODELO:
            return await self._handle_paciente_modelo(context, updates)

        if caminho_ativo == CaminhoMapaMestre.LICENCIAMENTO_FRANQUIA:
            return await self._handle_licenciamento(
                context, user_message, updates
            )

        # Caminhos 1-4: requerem elegibilidade medica
        return await self._handle_curso(
            caminho_ativo, context, user_message, updates
        )

    # ------------------------------------------------------------------
    # Handlers por caminho
    # ------------------------------------------------------------------

    async def _handle_paciente_modelo(
        self, context: SessionContext, updates: dict
    ) -> FlowResult:
        """
        Caminho 5: paciente modelo.
        Envia SOMENTE o contato da Nidia; nao responde mais nada (FR-014).
        """
        resposta = await self._responder.generate_paciente_modelo(
            self._nidia_phone, context.idioma
        )
        updates["caminho_atual"] = CaminhoMapaMestre.PACIENTE_MODELO
        updates["etapa_mapa_mestre"] = ETAPA_PACIENTE
        return FlowResult(
            response_text=resposta,
            action="end",  # finaliza o atendimento apos fornecer o contato
            caminho=CaminhoMapaMestre.PACIENTE_MODELO,
            etapa=ETAPA_PACIENTE,
            updates=updates,
        )

    async def _handle_licenciamento(
        self, context: SessionContext, user_message: str, updates: dict
    ) -> FlowResult:
        """
        Caminho 6: licenciamento / franquia.
        Qualificar interesse e conduzir para reuniao; NUNCA vender diretamente.
        """
        knowledge = await self._load_knowledge(
            caminho=CaminhoMapaMestre.LICENCIAMENTO_FRANQUIA,
            idioma=context.idioma,
        )

        etapa = context.etapa or ETAPA_LICENCIAMENTO
        history = self._memory.build_messages_for_llm(context, max_msgs=8)

        response_text, handoff = await self._responder.generate(
            user_message=user_message,
            caminho=CaminhoMapaMestre.LICENCIAMENTO_FRANQUIA,
            etapa=etapa,
            knowledge_context=knowledge,
            session_history=history,
            session_summary=context.resumo_rolante,
            idioma=context.idioma,
        )

        updates["caminho_atual"] = CaminhoMapaMestre.LICENCIAMENTO_FRANQUIA
        updates["etapa_mapa_mestre"] = etapa
        action = "handoff" if handoff else "continue"

        return FlowResult(
            response_text=response_text,
            action=action,
            caminho=CaminhoMapaMestre.LICENCIAMENTO_FRANQUIA,
            etapa=etapa,
            updates=updates,
        )

    async def _handle_curso(
        self,
        caminho: int,
        context: SessionContext,
        user_message: str,
        updates: dict,
    ) -> FlowResult:
        """
        Caminhos 1-4: cursos (online/presenciais).
        Fluxo: qualif medico → qualif experiencia (presenciais) → apresentacao → link
        """
        # Atualizar caminho no contexto e updates
        context.caminho = caminho
        updates["caminho_atual"] = caminho

        # --- Verificacao de elegibilidade ---

        # Etapa 1: verificar se e medico
        if context.eh_medico is None:
            # Ainda nao sabemos se e medico
            etapa = ETAPA_QUALIF_MEDICO
            resposta = await self._gerar_pergunta_medico(context.idioma)
            updates["etapa_mapa_mestre"] = etapa
            return FlowResult(
                response_text=resposta,
                action="continue",
                caminho=caminho,
                etapa=etapa,
                updates=updates,
            )

        # Verificar resposta sobre ser medico (se estamos na etapa de qualificacao)
        if context.etapa == ETAPA_QUALIF_MEDICO and context.eh_medico is None:
            eh_medico = _detectar_confirmacao(user_message)
            if eh_medico is not None:
                context.eh_medico = eh_medico
                updates["eh_medico"] = eh_medico

        # Lead nao e medico → nao elegivel
        if context.eh_medico is False:
            resposta = await self._responder.generate_not_eligible(context.idioma)
            updates["etapa_mapa_mestre"] = ETAPA_HANDOFF
            return FlowResult(
                response_text=resposta,
                action="handoff",
                caminho=caminho,
                etapa=ETAPA_HANDOFF,
                updates=updates,
            )

        # Etapa 2 (caminhos 2-4): verificar experiencia em Harmonizacao Corporal
        if caminho in (
            CaminhoMapaMestre.HG_MODULO_1,
            CaminhoMapaMestre.HG360_SP,
            CaminhoMapaMestre.HG360_BARCELONA,
        ):
            if context.experiencia_corporal is None:
                # Verificar se a resposta atual contem essa info
                exp = _detectar_experiencia_corporal(user_message)
                if exp is not None:
                    context.experiencia_corporal = exp
                    updates["experiencia_corporal"] = exp
                else:
                    # Perguntar sobre experiencia
                    etapa = ETAPA_QUALIF_EXPERIENCIA
                    resposta = await self._gerar_pergunta_experiencia(
                        context.idioma, caminho
                    )
                    updates["etapa_mapa_mestre"] = etapa
                    return FlowResult(
                        response_text=resposta,
                        action="continue",
                        caminho=caminho,
                        etapa=etapa,
                        updates=updates,
                    )

            # "apenas facial" ou sem experiencia corporal → nao elegivel (FR-009)
            if context.experiencia_corporal is False:
                resposta = await self._gerar_nao_elegivel_experiencia(
                    context.idioma, caminho
                )
                updates["etapa_mapa_mestre"] = ETAPA_HANDOFF
                return FlowResult(
                    response_text=resposta,
                    action="handoff",
                    caminho=caminho,
                    etapa=ETAPA_HANDOFF,
                    updates=updates,
                )

        # --- Elegivel: carregar base de conhecimento e gerar resposta ---
        knowledge = await self._load_knowledge(caminho=caminho, idioma=context.idioma)

        etapa = context.etapa or ETAPA_APRESENTACAO
        history = self._memory.build_messages_for_llm(context, max_msgs=8)

        response_text, handoff = await self._responder.generate(
            user_message=user_message,
            caminho=caminho,
            etapa=etapa,
            knowledge_context=knowledge,
            session_history=history,
            session_summary=context.resumo_rolante,
            idioma=context.idioma,
        )

        updates["etapa_mapa_mestre"] = etapa
        action = "handoff" if handoff else "continue"

        return FlowResult(
            response_text=response_text,
            action=action,
            caminho=caminho,
            etapa=etapa,
            updates=updates,
        )

    # ------------------------------------------------------------------
    # Carregamento de conhecimento do banco
    # ------------------------------------------------------------------

    async def _load_knowledge(self, caminho: int, idioma: str) -> str:
        """
        Carrega base de conhecimento do banco para o caminho e idioma.

        Hierarquia: Apresentacao + Objecoes (por idioma).
        """
        slug = _CAMINHO_PARA_SLUG.get(caminho)
        if slug is None:
            return ""

        # Buscar curso pelo slug
        stmt_curso = select(Curso).where(Curso.slug == slug, Curso.ativo.is_(True))
        result = await self._db.execute(stmt_curso)
        curso = result.scalar_one_or_none()
        if curso is None:
            logger.warning(
                "flow: curso nao encontrado slug=%s caminho=%s", slug, caminho
            )
            return ""

        sections: list[str] = []

        # Apresentacao verbatim no idioma do lead
        stmt_apres = select(CursoApresentacao).where(
            CursoApresentacao.curso_id == curso.id,
            CursoApresentacao.idioma == idioma,
        )
        result = await self._db.execute(stmt_apres)
        apres = result.scalar_one_or_none()

        if apres is None and idioma != "pt":
            # Fallback para PT
            stmt_apres_pt = select(CursoApresentacao).where(
                CursoApresentacao.curso_id == curso.id,
                CursoApresentacao.idioma == "pt",
            )
            result = await self._db.execute(stmt_apres_pt)
            apres = result.scalar_one_or_none()

        if apres:
            sections.append(f"=== APRESENTACAO OFICIAL ({idioma}) ===\n{apres.texto}")

        # Banco de objecoes (idioma do lead, fallback PT)
        stmt_obj = select(CursoObjecao).where(
            CursoObjecao.curso_id == curso.id,
            CursoObjecao.idioma == idioma,
        )
        result = await self._db.execute(stmt_obj)
        objecoes = result.scalars().all()

        if not objecoes and idioma != "pt":
            stmt_obj_pt = select(CursoObjecao).where(
                CursoObjecao.curso_id == curso.id,
                CursoObjecao.idioma == "pt",
            )
            result = await self._db.execute(stmt_obj_pt)
            objecoes = result.scalars().all()

        if objecoes:
            obj_text = "\n".join(
                f"- Objecao: {o.objecao}\n  Resposta: {o.resposta}"
                for o in objecoes
            )
            sections.append(f"=== BANCO DE OBJECOES OFICIAL ===\n{obj_text}")

        # Turmas ativas (datas/locais)
        stmt_turmas = select(CursoTurma).where(
            CursoTurma.curso_id == curso.id,
            CursoTurma.ativo.is_(True),
        )
        result = await self._db.execute(stmt_turmas)
        turmas = result.scalars().all()
        if turmas:
            turmas_text = "\n".join(
                f"- {t.cidade} ({t.pais or ''}): {t.data_inicio or 'data a confirmar'}"
                f"{', lote: ' + t.lote_preco if t.lote_preco else ''}"
                for t in turmas
            )
            sections.append(f"=== TURMAS DISPONIVEIS ===\n{turmas_text}")

        # Links de inscricao no idioma
        stmt_links = select(CursoLink).where(
            CursoLink.curso_id == curso.id,
            CursoLink.idioma == idioma,
        )
        result = await self._db.execute(stmt_links)
        link = result.scalar_one_or_none()

        if link is None and idioma != "pt":
            stmt_links_pt = select(CursoLink).where(
                CursoLink.curso_id == curso.id,
                CursoLink.idioma == "pt",
            )
            result = await self._db.execute(stmt_links_pt)
            link = result.scalar_one_or_none()

        if link:
            sections.append(f"=== LINK DE INSCRICAO ({idioma}) ===\n{link.url}")

        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Helpers de geracao de perguntas padrao
    # ------------------------------------------------------------------

    async def _gerar_pergunta_medico(self, idioma: str) -> str:
        """Pergunta padrao: e medico? (anti-alucinacao: texto fixo)"""
        if idioma == "en":
            return (
                "Great! To verify your eligibility, could you confirm: "
                "are you a licensed physician (MD)? 🩺"
            )
        elif idioma == "es":
            return (
                "¡Genial! Para verificar tu elegibilidad, ¿puedes confirmar "
                "si eres médico con registro activo? 🩺"
            )
        else:
            return (
                "Ótimo! Para verificar sua elegibilidade, pode confirmar: "
                "você é médico com CRM ativo? 🩺"
            )

    async def _gerar_pergunta_experiencia(self, idioma: str, caminho: int) -> str:
        """Pergunta sobre experiencia em harmonizacao corporal (nao facial)."""
        if idioma == "en":
            return (
                "To access this advanced course, we need to verify: "
                "do you have experience in Corporal Harmonization or gluteal fillers? "
                "(facial experience alone does not qualify) 💉"
            )
        elif idioma == "es":
            return (
                "Para acceder a este curso avanzado, necesitamos verificar: "
                "¿tienes experiencia en Armonización Corporal o rellenos glúteos? "
                "(la experiencia solo facial no es suficiente) 💉"
            )
        else:
            return (
                "Para acessar este curso avançado, precisamos verificar: "
                "você tem experiência em Harmonização Corporal ou preenchimento de glúteo? "
                "(experiência apenas facial não conta) 💉"
            )

    async def _gerar_nao_elegivel_experiencia(self, idioma: str, caminho: int) -> str:
        """Lead nao tem experiencia corporal: nao elegivel para presenciais avancados."""
        if idioma == "en":
            return (
                "Thank you for your interest! 🙏\n\n"
                "This advanced presential course requires prior experience in "
                "Corporal Harmonization or gluteal fillers.\n\n"
                "I recommend starting with our Online Course in Gluteal Harmonization "
                "to build the foundational knowledge. Would you like to know more about it?"
            )
        elif idioma == "es":
            return (
                "¡Gracias por tu interés! 🙏\n\n"
                "Este curso presencial avanzado requiere experiencia previa en "
                "Armonización Corporal o rellenos glúteos.\n\n"
                "Te recomiendo comenzar con nuestro Curso Online de Armonización Glútea "
                "para construir la base necesaria. ¿Te gustaría saber más?"
            )
        else:
            return (
                "Obrigado pelo interesse! 🙏\n\n"
                "Este curso presencial avançado exige experiência prévia em "
                "Harmonização Corporal ou preenchimento de glúteo.\n\n"
                "Recomendo começar pelo nosso Curso Online de Harmonização Glútea "
                "para construir a base necessária. Gostaria de saber mais?"
            )


# ---------------------------------------------------------------------------
# Helpers de NLU simples (sem LLM — heuristica de baixo custo)
# ---------------------------------------------------------------------------

def _detectar_confirmacao(texto: str) -> Optional[bool]:
    """
    Detecta confirmacao simples de sim/nao no texto.
    Retorna True (sim), False (nao) ou None (indeterminado).

    Negativos sao checados antes dos positivos para evitar falsos positivos
    (ex: "nao sou medico" nao deve bater em "sou").
    """
    t = texto.lower().strip()

    # Negativos — verificar PRIMEIRO (prioridade sobre positivos)
    negativos = [
        "nao sou", "não sou", "nao tenho", "não tenho",
        "nao soy", "no soy", "i am not", "i'm not",
        "nunca", "nenhum",
        "nao", "não",
    ]
    # Positivos
    positivos = [
        "sim", "yes", "si soy", "soy medico", "i am a", "i'm a",
        "sou medico", "tenho crm", "possuo crm", "confirmando",
        "confirmo", "afirmativo", "claro", "com certeza",
        "sou", "tenho", "possuo", "crm",
    ]

    for neg in negativos:
        if neg in t:
            return False
    for pos in positivos:
        if pos in t:
            return True
    return None


def _detectar_experiencia_corporal(texto: str) -> Optional[bool]:
    """
    Detecta se o lead tem experiencia em harmonizacao corporal.
    Retorna True/False/None.

    Negativos sao checados PRIMEIRO para evitar falso positivo em
    "nao tenho experiencia corporal" (bateria em "corporal").
    """
    t = texto.lower().strip()

    # Negativos — verificar PRIMEIRO
    negativos = [
        "so facial", "só facial", "apenas facial", "only facial", "solo facial",
        "nao tenho", "não tenho", "nao possuo", "não possuo",
        "nunca", "sem experiencia", "nao", "não", "no tengo",
    ]
    # Positivos
    positivos = [
        "sim", "yes", "si", "tenho", "possuo", "experiencia corporal",
        "harmonizacao corporal", "preenchimento de gluteo",
        "preenchimento glúteo", "gluteal", "corporal harmony",
        "corporal", "gluteo", "glúteo",
    ]

    for neg in negativos:
        if neg in t:
            return False
    for pos in positivos:
        if pos in t:
            return True
    return None
