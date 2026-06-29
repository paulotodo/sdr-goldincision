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

Taxonomia (6 caminhos oficiais do MAPA MESTRE DO ATENDIMENTO):
  1. Curso Online HG
  2. Cursos Presenciais HG (HG Modulo 1 e HG360 como sub-fluxos internos)
  3. Sistema GoldIncision (Licenciamento / Franquia)
  4. Aluno que precisa de suporte (handoff imediato)
  5. Paciente modelo (contato da Nidia)
  6. Outro assunto (handoff imediato)
"""
from __future__ import annotations

import logging
from enum import IntEnum
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.intent import INTENCAO_PARA_CAMINHO, ClassificacaoIntencao, IntentClassifier
from app.core.memory import MemoryManager, SessionContext
from app.core.responder import GroundedResponder
from app.repository.models import (
    Curso,
    CursoApresentacao,
    CursoLink,
    CursoObjecao,
    CursoTurma,
    Faq,
)

logger = logging.getLogger(__name__)


class CaminhoMapaMestre(IntEnum):
    """
    Os 6 caminhos oficiais do Mapa Mestre de Atendimento GoldIncision.
    Conforme MAPA MESTRE DO ATENDIMENTO.docx.
    """
    CURSO_ONLINE_HG = 1       # Curso Online Harmonizacao Glutea
    CURSOS_PRESENCIAIS = 2    # Cursos Presenciais HG (HG Modulo 1 e HG360 como sub-fluxos)
    SISTEMA_GOLDINCISION = 3  # Licenciamento / Franquia (Sistema GoldIncision)
    ALUNO_SUPORTE = 4         # Aluno precisa de suporte → handoff imediato
    PACIENTE_MODELO = 5       # Lead quer ser paciente modelo (Nidia)
    OUTRO_ASSUNTO = 6         # Outro assunto → handoff imediato


# Slugs dos cursos mapeados por sub-caminho dentro de Caminho 2 (presenciais)
# e Caminho 1 (online) e Caminho 3 (sistema)
_CAMINHO_PARA_SLUG: dict[int, str | None] = {
    1: "curso-online-hg",
    # Caminho 2: slug resolvido dinamicamente pelo sub-caminho (produto_interesse)
    2: None,
    3: "licenciamento-internacional",
    4: None,   # aluno/suporte: handoff imediato
    5: None,   # paciente modelo: nao tem curso no catalogo
    6: None,   # outro assunto: handoff imediato
}

# Sub-slugs dos presenciais (dentro do Caminho 2)
_SLUG_HG_MODULO_1 = "hg-modulo-1"
_SLUG_HG360_SP = "hg360-sp"
_SLUG_HG360_BARCELONA = "hg360-barcelona"

# Especialidades medicas que qualificam ao HG360 (conforme MAPA MESTRE)
_ESPECIALIDADES_HG360 = {"dermatologia", "cirurgia plastica", "cirurgia vascular"}

# Etapas finas por caminho
ETAPA_QUALIF_MEDICO = "qualif_medico"
ETAPA_QUALIF_EXPERIENCIA = "qualif_experiencia"
ETAPA_QUALIF_ESPECIALIDADE = "qualif_especialidade"
ETAPA_ESCOLHA_TURMA = "escolha_turma"
ETAPA_APRESENTACAO = "apresentacao"
ETAPA_OBJECAO = "objecao"
ETAPA_LINK = "link"
ETAPA_PACIENTE = "paciente_modelo"
ETAPA_SISTEMA = "sistema_goldincision"
ETAPA_ALUNO = "aluno_suporte"
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

        if caminho_ativo == CaminhoMapaMestre.ALUNO_SUPORTE:
            return await self._handle_aluno_suporte(context, updates)

        if caminho_ativo == CaminhoMapaMestre.OUTRO_ASSUNTO:
            return await self._handle_outro_assunto(context, updates)

        if caminho_ativo == CaminhoMapaMestre.SISTEMA_GOLDINCISION:
            return await self._handle_sistema_goldincision(
                context, user_message, updates
            )

        if caminho_ativo == CaminhoMapaMestre.CURSOS_PRESENCIAIS:
            return await self._handle_cursos_presenciais(
                context, user_message, updates
            )

        if caminho_ativo == CaminhoMapaMestre.CURSO_ONLINE_HG:
            return await self._handle_curso_online(context, user_message, updates)

        # Fallback (caminho desconhecido)
        menu_text = await self._responder.generate_menu(context.idioma)
        return FlowResult(
            response_text=menu_text,
            action="continue",
            caminho=None,
            etapa=ETAPA_MENU,
            updates=updates,
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

    async def _handle_aluno_suporte(
        self, context: SessionContext, updates: dict
    ) -> FlowResult:
        """
        Caminho 4: aluno precisa de suporte.
        Identifica a necessidade e encaminha para equipe responsavel.
        """
        updates["caminho_atual"] = CaminhoMapaMestre.ALUNO_SUPORTE
        updates["etapa_mapa_mestre"] = ETAPA_ALUNO

        if context.idioma == "en":
            resposta = (
                "Of course! I will direct your request to our responsible team, "
                "who will continue your service. If necessary, our team may contact "
                "you to request additional information.\n\nPlease hold on while I "
                "connect you with the right person."
            )
        elif context.idioma == "es":
            resposta = (
                "¡Por supuesto! Voy a dirigir tu solicitud a nuestro equipo responsable, "
                "que dará continuidad a tu atención. Si es necesario, nuestro equipo "
                "podrá contactarte para solicitar información adicional."
            )
        else:
            resposta = (
                "Perfeito! Vou encaminhar sua solicitação para nossa equipe responsável, "
                "que dará continuidade ao seu atendimento.\n\n"
                "Caso seja necessário, nossa equipe poderá entrar em contato para "
                "solicitar informações complementares."
            )

        return FlowResult(
            response_text=resposta,
            action="handoff",
            caminho=CaminhoMapaMestre.ALUNO_SUPORTE,
            etapa=ETAPA_ALUNO,
            updates=updates,
        )

    async def _handle_outro_assunto(
        self, context: SessionContext, updates: dict
    ) -> FlowResult:
        """
        Caminho 6: outro assunto.
        Encaminha para equipe — nao tenta resolver fora da base.
        """
        updates["caminho_atual"] = CaminhoMapaMestre.OUTRO_ASSUNTO
        updates["etapa_mapa_mestre"] = ETAPA_HANDOFF

        if context.idioma == "en":
            resposta = (
                "Thank you for your message! I will connect you with our team "
                "to assist you with this matter."
            )
        elif context.idioma == "es":
            resposta = (
                "¡Gracias por tu mensaje! Te voy a conectar con nuestro equipo "
                "para que pueda ayudarte con este tema."
            )
        else:
            resposta = (
                "Obrigado pela mensagem! Vou conectar você com nossa equipe "
                "para que possam auxiliá-lo com este assunto."
            )

        return FlowResult(
            response_text=resposta,
            action="handoff",
            caminho=CaminhoMapaMestre.OUTRO_ASSUNTO,
            etapa=ETAPA_HANDOFF,
            updates=updates,
        )

    async def _handle_sistema_goldincision(
        self, context: SessionContext, user_message: str, updates: dict
    ) -> FlowResult:
        """
        Caminho 3: Sistema GoldIncision (licenciamento / franquia).
        Qualificar interesse e conduzir para reuniao; NUNCA vender diretamente.
        """
        knowledge = await self._load_knowledge(
            caminho=CaminhoMapaMestre.SISTEMA_GOLDINCISION,
            idioma=context.idioma,
        )

        etapa = context.etapa or ETAPA_SISTEMA
        history = self._memory.build_messages_for_llm(context, max_msgs=8)

        response_text, handoff = await self._responder.generate(
            user_message=user_message,
            caminho=CaminhoMapaMestre.SISTEMA_GOLDINCISION,
            etapa=etapa,
            knowledge_context=knowledge,
            session_history=history,
            session_summary=context.resumo_rolante,
            idioma=context.idioma,
        )

        updates["caminho_atual"] = CaminhoMapaMestre.SISTEMA_GOLDINCISION
        updates["etapa_mapa_mestre"] = etapa
        action = "handoff" if handoff else "continue"

        return FlowResult(
            response_text=response_text,
            action=action,
            caminho=CaminhoMapaMestre.SISTEMA_GOLDINCISION,
            etapa=etapa,
            updates=updates,
        )

    async def _handle_curso_online(
        self,
        context: SessionContext,
        user_message: str,
        updates: dict,
    ) -> FlowResult:
        """
        Caminho 1: Curso Online HG.
        Fluxo: qualif medico → apresentacao → link
        """
        context.caminho = CaminhoMapaMestre.CURSO_ONLINE_HG
        updates["caminho_atual"] = CaminhoMapaMestre.CURSO_ONLINE_HG

        # Verificar/detectar se e medico (parseando a mensagem ANTES de checar o estado)
        if context.etapa == ETAPA_QUALIF_MEDICO and context.eh_medico is None:
            eh_medico = _detectar_confirmacao(user_message)
            if eh_medico is not None:
                context.eh_medico = eh_medico
                updates["eh_medico"] = eh_medico

        # Etapa: verificar elegibilidade medica
        if context.eh_medico is None:
            etapa = ETAPA_QUALIF_MEDICO
            resposta = await self._gerar_pergunta_medico(context.idioma)
            updates["etapa_mapa_mestre"] = etapa
            return FlowResult(
                response_text=resposta,
                action="continue",
                caminho=CaminhoMapaMestre.CURSO_ONLINE_HG,
                etapa=etapa,
                updates=updates,
            )

        if context.eh_medico is False:
            resposta = await self._responder.generate_not_eligible(context.idioma)
            updates["etapa_mapa_mestre"] = ETAPA_HANDOFF
            return FlowResult(
                response_text=resposta,
                action="handoff",
                caminho=CaminhoMapaMestre.CURSO_ONLINE_HG,
                etapa=ETAPA_HANDOFF,
                updates=updates,
            )

        # Elegivel: carregar base e gerar resposta
        knowledge = await self._load_knowledge(
            caminho=CaminhoMapaMestre.CURSO_ONLINE_HG,
            idioma=context.idioma,
        )
        etapa = context.etapa or ETAPA_APRESENTACAO
        history = self._memory.build_messages_for_llm(context, max_msgs=8)

        response_text, handoff = await self._responder.generate(
            user_message=user_message,
            caminho=CaminhoMapaMestre.CURSO_ONLINE_HG,
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
            caminho=CaminhoMapaMestre.CURSO_ONLINE_HG,
            etapa=etapa,
            updates=updates,
        )

    async def _handle_cursos_presenciais(
        self,
        context: SessionContext,
        user_message: str,
        updates: dict,
    ) -> FlowResult:
        """
        Caminho 2: Cursos Presenciais HG.

        Sub-fluxo de qualificacao:
        1. Verificar se e medico (ETAPA_QUALIF_MEDICO)
        2. Verificar experiencia corporal (ETAPA_QUALIF_EXPERIENCIA)
           - SIM → elegivel ao HG360 → ir para ETAPA_ESCOLHA_TURMA
           - NAO → verificar especialidade (ETAPA_QUALIF_ESPECIALIDADE)
             - Dermatologia / Cirurgia Plastica / Cirurgia Vascular → HG360
             - Outra / Nao possuo → HG Modulo 1
        3. Escolha da turma HG360 (SP ou Barcelona) → ETAPA_ESCOLHA_TURMA
        4. Apresentacao do curso escolhido → ETAPA_APRESENTACAO
        """
        context.caminho = CaminhoMapaMestre.CURSOS_PRESENCIAIS
        updates["caminho_atual"] = CaminhoMapaMestre.CURSOS_PRESENCIAIS

        # --- Detectar respostas na mensagem corrente ANTES de checar estados ---

        # Se estamos pedindo confirmacao de medico, parsear agora
        if context.etapa == ETAPA_QUALIF_MEDICO and context.eh_medico is None:
            eh_medico = _detectar_confirmacao(user_message)
            if eh_medico is not None:
                context.eh_medico = eh_medico
                updates["eh_medico"] = eh_medico

        # Se estamos pedindo experiencia corporal, parsear agora
        if context.etapa == ETAPA_QUALIF_EXPERIENCIA and context.experiencia_corporal is None:
            exp = _detectar_experiencia_corporal(user_message)
            if exp is not None:
                context.experiencia_corporal = exp
                updates["experiencia_corporal"] = exp

        # Se estamos pedindo especialidade, parsear agora
        if context.etapa == ETAPA_QUALIF_ESPECIALIDADE and not context.especialidade:
            especialidade = _detectar_especialidade(user_message)
            if especialidade is not None:
                context.especialidade = especialidade
                updates["especialidade"] = especialidade

        # Se estamos pedindo escolha de turma (HG360 SP vs Barcelona), parsear agora
        if context.etapa == ETAPA_ESCOLHA_TURMA and not context.produto_interesse:
            slug_escolhido = _detectar_escolha_turma(user_message)
            if slug_escolhido:
                context.produto_interesse = slug_escolhido
                updates["produto_interesse"] = slug_escolhido

        # --- Fluxo de qualificacao ---

        # Etapa 1: verificar se e medico
        if context.eh_medico is None:
            etapa = ETAPA_QUALIF_MEDICO
            resposta = await self._gerar_pergunta_medico(context.idioma)
            updates["etapa_mapa_mestre"] = etapa
            return FlowResult(
                response_text=resposta,
                action="continue",
                caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS,
                etapa=etapa,
                updates=updates,
            )

        # Lead nao e medico → nao elegivel (FR-009)
        if context.eh_medico is False:
            resposta = await self._responder.generate_not_eligible(context.idioma)
            updates["etapa_mapa_mestre"] = ETAPA_HANDOFF
            return FlowResult(
                response_text=resposta,
                action="handoff",
                caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS,
                etapa=ETAPA_HANDOFF,
                updates=updates,
            )

        # Etapa 2: verificar experiencia em Harmonizacao Corporal
        if context.experiencia_corporal is None:
            etapa = ETAPA_QUALIF_EXPERIENCIA
            resposta = await self._gerar_pergunta_experiencia(context.idioma)
            updates["etapa_mapa_mestre"] = etapa
            return FlowResult(
                response_text=resposta,
                action="continue",
                caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS,
                etapa=etapa,
                updates=updates,
            )

        # Sem experiencia corporal → verificar especialidade para determinar HG360 ou Modulo 1
        if context.experiencia_corporal is False and not context.especialidade:
            etapa = ETAPA_QUALIF_ESPECIALIDADE
            resposta = await self._gerar_pergunta_especialidade(context.idioma)
            updates["etapa_mapa_mestre"] = etapa
            return FlowResult(
                response_text=resposta,
                action="continue",
                caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS,
                etapa=etapa,
                updates=updates,
            )

        # Determinar sub-curso com base em experiencia + especialidade
        if not context.produto_interesse:
            slug_recomendado = self._recomendar_sub_curso(context)
            if slug_recomendado in (_SLUG_HG360_SP, _SLUG_HG360_BARCELONA):
                # Elegivel ao HG360: perguntar turma (SP ou Barcelona)
                if context.etapa != ETAPA_ESCOLHA_TURMA:
                    etapa = ETAPA_ESCOLHA_TURMA
                    resposta = await self._gerar_pergunta_escolha_turma(context.idioma)
                    updates["etapa_mapa_mestre"] = etapa
                    return FlowResult(
                        response_text=resposta,
                        action="continue",
                        caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS,
                        etapa=etapa,
                        updates=updates,
                    )
                # Turma ainda nao escolhida: aguardar resposta
                if not context.produto_interesse:
                    etapa = ETAPA_ESCOLHA_TURMA
                    resposta = await self._gerar_pergunta_escolha_turma(context.idioma)
                    updates["etapa_mapa_mestre"] = etapa
                    return FlowResult(
                        response_text=resposta,
                        action="continue",
                        caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS,
                        etapa=etapa,
                        updates=updates,
                    )
            else:
                # HG Modulo 1
                context.produto_interesse = _SLUG_HG_MODULO_1
                updates["produto_interesse"] = _SLUG_HG_MODULO_1

        # Apresentar o sub-curso escolhido
        slug = context.produto_interesse or _SLUG_HG_MODULO_1
        knowledge = await self._load_knowledge_by_slug(slug=slug, idioma=context.idioma)
        etapa = context.etapa or ETAPA_APRESENTACAO
        history = self._memory.build_messages_for_llm(context, max_msgs=8)

        # Mapear slug para numero de caminho interno (para o responder)
        caminho_responder = {
            _SLUG_HG_MODULO_1: 2,
            _SLUG_HG360_SP: 3,
            _SLUG_HG360_BARCELONA: 4,
        }.get(slug, 2)

        response_text, handoff = await self._responder.generate(
            user_message=user_message,
            caminho=caminho_responder,
            etapa=etapa,
            knowledge_context=knowledge,
            session_history=history,
            session_summary=context.resumo_rolante,
            idioma=context.idioma,
        )

        updates["etapa_mapa_mestre"] = ETAPA_APRESENTACAO
        action = "handoff" if handoff else "continue"

        return FlowResult(
            response_text=response_text,
            action=action,
            caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS,
            etapa=ETAPA_APRESENTACAO,
            updates=updates,
        )

    def _recomendar_sub_curso(self, context: SessionContext) -> str:
        """
        Determina o sub-curso recomendado com base em experiencia e especialidade.

        Regras do Mapa Mestre:
        - Experiencia corporal SIM → HG360 (escolha de turma pendente)
        - Experiencia corporal NAO + especialidade qualificante → HG360
        - Experiencia corporal NAO + especialidade nao qualificante → HG Modulo 1
        """
        if context.experiencia_corporal:
            # Experiencia corporal confirmada → elegivel ao HG360
            return _SLUG_HG360_SP  # default; turma sera escolhida depois

        # Sem experiencia corporal: verificar especialidade
        esp = (context.especialidade or "").lower().strip()
        for esp_qualif in _ESPECIALIDADES_HG360:
            if esp_qualif in esp:
                return _SLUG_HG360_SP  # especialidade qualificante → HG360
        # Especialidade nao qualificante ou nao informada
        return _SLUG_HG_MODULO_1

    # ------------------------------------------------------------------
    # Carregamento de conhecimento do banco
    # ------------------------------------------------------------------

    async def _load_knowledge(self, caminho: int, idioma: str) -> str:
        """
        Carrega base de conhecimento do banco para o caminho e idioma.
        Para Caminho 2 (presenciais), usar _load_knowledge_by_slug com o slug especifico.
        """
        slug = _CAMINHO_PARA_SLUG.get(caminho)
        if slug is None:
            return ""
        return await self._load_knowledge_by_slug(slug=slug, idioma=idioma)

    async def _load_knowledge_by_slug(self, slug: str, idioma: str) -> str:
        """
        Carrega base de conhecimento do banco para o slug especifico e idioma.

        Hierarquia: Apresentacao + Objecoes (por idioma) + Turmas + Links.
        """
        # Buscar curso pelo slug
        stmt_curso = select(Curso).where(Curso.slug == slug, Curso.ativo.is_(True))
        result = await self._db.execute(stmt_curso)
        curso = result.scalar_one_or_none()
        if curso is None:
            logger.warning(
                "flow: curso nao encontrado slug=%s", slug
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

        # FAQ Oficial (global) — ultimo na hierarquia (Mapa Mestre -> Base ->
        # Objecoes -> FAQ). Consultar apenas quando a resposta nao estiver acima.
        faq_text = await self._load_faq(idioma)
        if faq_text:
            sections.append(
                "=== FAQ OFICIAL (consultar SOMENTE se a resposta nao estiver "
                f"nas secoes acima) ===\n{faq_text}"
            )

        return "\n\n".join(sections)

    async def _load_faq(self, idioma: str) -> str:
        """Carrega o FAQ Oficial ativo (fallback PT) como texto para grounding."""
        stmt = select(Faq).where(Faq.ativo.is_(True), Faq.idioma == idioma)
        result = await self._db.execute(stmt)
        itens = result.scalars().all()
        if not itens and idioma != "pt":
            stmt_pt = select(Faq).where(Faq.ativo.is_(True), Faq.idioma == "pt")
            result = await self._db.execute(stmt_pt)
            itens = result.scalars().all()
        if not itens:
            return ""
        return "\n".join(f"- P: {i.pergunta}\n  R: {i.resposta}" for i in itens)

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

    async def _gerar_pergunta_experiencia(self, idioma: str) -> str:
        """Pergunta sobre experiencia em harmonizacao corporal (nao facial)."""
        if idioma == "en":
            return (
                "To indicate the most suitable training for you, I need to verify: "
                "do you have experience in Corporal Harmonization or gluteal fillers? "
                "(facial experience alone does not qualify) 💉"
            )
        elif idioma == "es":
            return (
                "Para indicarte la formación más adecuada, necesito verificar: "
                "¿tienes experiencia en Armonización Corporal o rellenos glúteos? "
                "(la experiencia solo facial no es suficiente) 💉"
            )
        else:
            return (
                "Para indicar a formação mais adequada ao seu momento profissional: "
                "você já atua com Harmonização Corporal ou preenchimento de glúteo? "
                "(experiência apenas facial não conta) 💉"
            )

    async def _gerar_pergunta_especialidade(self, idioma: str) -> str:
        """Pergunta sobre especialidade medica para determinar elegibilidade ao HG360."""
        if idioma == "en":
            return (
                "To direct you to the most suitable course, could you tell me: "
                "what is your medical specialty?\n"
                "• Dermatology\n"
                "• Plastic Surgery\n"
                "• Vascular Surgery\n"
                "• Other specialty\n"
                "• I don't have a specialty"
            )
        elif idioma == "es":
            return (
                "Para dirigirte al curso más adecuado, ¿podrías decirme "
                "tu especialidad médica?\n"
                "• Dermatología\n"
                "• Cirugía Plástica\n"
                "• Cirugía Vascular\n"
                "• Otra especialidad\n"
                "• No tengo especialidad"
            )
        else:
            return (
                "Para indicar a formação mais adequada ao seu perfil, "
                "poderia me informar sua especialidade médica?\n"
                "• Dermatologia\n"
                "• Cirurgia Plástica\n"
                "• Cirurgia Vascular\n"
                "• Outra especialidade\n"
                "• Não possuo especialidade"
            )

    async def _gerar_pergunta_escolha_turma(self, idioma: str) -> str:
        """Pergunta sobre escolha da turma HG360 (SP ou Barcelona)."""
        if idioma == "en":
            return (
                "We currently have two HG360 sessions available. "
                "Which one interests you most?\n\n"
                "1️⃣ São Paulo – August 28-30, 2026\n"
                "2️⃣ Barcelona – July 24-25, 2026"
            )
        elif idioma == "es":
            return (
                "Actualmente tenemos dos grupos del HG360 disponibles. "
                "¿Cuál te interesa más?\n\n"
                "1️⃣ São Paulo – 28 a 30/08/2026\n"
                "2️⃣ Barcelona – 24 y 25/07/2026"
            )
        else:
            return (
                "Atualmente temos duas turmas disponíveis do HG360. "
                "Qual delas desperta mais o seu interesse?\n\n"
                "1️⃣ São Paulo – 28 a 30/08/2026\n"
                "2️⃣ Barcelona – 24 e 25/07/2026"
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


def _detectar_especialidade(texto: str) -> Optional[str]:
    """
    Detecta especialidade medica no texto.
    Retorna string normalizada ou None se nao detectado.
    """
    t = texto.lower().strip()

    mapeamento = [
        (["dermatolog"], "dermatologia"),
        (["cirurgia plastica", "cirugia plastica", "plastic surgery", "plastica"], "cirurgia plastica"),
        (["cirurgia vascular", "vascular surgery", "vascular"], "cirurgia vascular"),
    ]

    for termos, especialidade in mapeamento:
        for termo in termos:
            if termo in t:
                return especialidade

    # Indicadores de "nao possuo especialidade" ou outra
    sem_especialidade = [
        "nao possuo", "não possuo", "sem especialidade", "nenhuma",
        "outra", "geral", "clinico", "clinica",
        "no tengo", "no specialty", "other",
    ]
    for termo in sem_especialidade:
        if termo in t:
            return "outra"

    return None


def _detectar_escolha_turma(texto: str) -> Optional[str]:
    """
    Detecta escolha de turma HG360 (SP ou Barcelona) no texto.
    Retorna slug do sub-curso ou None se nao detectado.
    """
    t = texto.lower().strip()

    # Barcelona
    if any(k in t for k in ["barcelona", "espanha", "spain", "españa", "julho", "july", "julio"]):
        return _SLUG_HG360_BARCELONA

    # Sao Paulo
    if any(k in t for k in ["sao paulo", "são paulo", "sp", "brasil", "brazil", "agosto", "august"]):
        return _SLUG_HG360_SP

    # Numeros do menu
    if "1" in t or "um" in t or "one" in t or "uno" in t:
        return _SLUG_HG360_SP
    if "2" in t or "dois" in t or "two" in t or "dos" in t:
        return _SLUG_HG360_BARCELONA

    return None
