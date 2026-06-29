"""
Motor do Mapa Mestre — orquestra os 6 caminhos do fluxo conversacional.

Principios (REGRAS GERAIS DO AGENTE COMERCIAL GOLDINCISION + MAPA MESTRE):
- Anti-alucinacao rigida: so Base Oficial como fonte (Principio II / Regra 7)
- Hierarquia: Mapa Mestre → Base → Objecoes → FAQ (Regra 5)
- Lacuna fora da base → recusa + handoff imediato (Regra 8)
- Elegibilidade medica inflexivel (FR-009 / Regra 20)
- Apresentacoes verbatim (FR-010 / Regra 15) — enviadas direto da Base
- Objecoes EXCLUSIVAMENTE do Banco Oficial (FR-011 / Regra 16)
- Identidade "Consultor Virtual Oficial" (FR-013 / Regra 27)
- Blocos curtos, 1 pergunta/msg (FR-015 / Regras 13-14)
- Nunca repetir perguntas ja respondidas (FR-021 / Regra 9)
- Mudanca de assunto redireciona (Regra 10), mas de forma conservadora:
  nao reinicia a jornada enquanto aguarda a resposta de uma pergunta.
- Pergunta direta → resposta direta, sem reiniciar o fluxo (Mapa Mestre, Caminho 1).

Humanizacao da ENTREGA (sem alterar a ESTRUTURA): reconhecer o que o lead disse,
usar o nome quando houver, transicoes suaves, tom consultivo premium.

Taxonomia (6 caminhos oficiais do MAPA MESTRE DO ATENDIMENTO):
  1. Curso Online HG
  2. Cursos Presenciais HG (HG Modulo 1 e HG360 como sub-fluxos internos)
  3. Sistema GoldIncision (ETAPA 1 → ETAPA 2 → Licenciamento / Franquia / diagnostico)
  4. Aluno que precisa de suporte (submenu 6 opcoes → encaminhamento)
  5. Paciente modelo (contato da Nidia)
  6. Outro assunto (handoff cordial)
"""
from __future__ import annotations

import json
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
    ALUNO_SUPORTE = 4         # Aluno precisa de suporte → submenu → encaminhamento
    PACIENTE_MODELO = 5       # Lead quer ser paciente modelo (Nidia)
    OUTRO_ASSUNTO = 6         # Outro assunto → handoff cordial


# Slugs dos cursos mapeados por caminho (1 e 3). O Caminho 2 resolve o slug
# dinamicamente pelo sub-caminho (produto_interesse). Mantido estavel para o
# catalogo em runtime (FR-026).
_CAMINHO_PARA_SLUG: dict[int, str | None] = {
    1: "curso-online-hg",
    2: None,
    3: "licenciamento-internacional",
    4: None,
    5: None,
    6: None,
}

# Sub-slugs dos presenciais (dentro do Caminho 2)
_SLUG_HG_MODULO_1 = "hg-modulo-1"
_SLUG_HG360_SP = "hg360-sp"
_SLUG_HG360_BARCELONA = "hg360-barcelona"
_SLUG_LICENCIAMENTO = "licenciamento-internacional"

# Especialidades medicas que qualificam ao HG360 (conforme MAPA MESTRE)
_ESPECIALIDADES_HG360 = {"dermatologia", "cirurgia plastica", "cirurgia vascular"}

# ---------------------------------------------------------------------------
# Etapas finas por caminho (estado da maquina, persistido em ticket.etapa)
# ---------------------------------------------------------------------------
ETAPA_MENU = "menu"
ETAPA_QUALIF_MEDICO = "qualif_medico"
ETAPA_QUALIF_EXPERIENCIA = "qualif_experiencia"
ETAPA_QUALIF_ESPECIALIDADE = "qualif_especialidade"
ETAPA_ESCOLHA_TURMA = "escolha_turma"
ETAPA_APRESENTACAO = "apresentacao"
ETAPA_DUVIDAS = "duvidas"
ETAPA_FECHAMENTO = "fechamento"
ETAPA_LINK = "link"
ETAPA_PACIENTE = "paciente_modelo"
ETAPA_HANDOFF = "handoff"
# Caminho 3 (Sistema GoldIncision)
ETAPA_SISTEMA_OBJETIVO = "sistema_objetivo"
ETAPA_SISTEMA_LICENCIAMENTO = "sistema_licenciamento"
ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS = "sistema_licenciamento_duvidas"
ETAPA_SISTEMA_FRANQUIA = "sistema_franquia"
ETAPA_SISTEMA_DIAGNOSTICO = "sistema_diagnostico"
# Caminho 4 (Aluno/suporte)
ETAPA_ALUNO_MENU = "aluno_menu"
# Aliases historicos (compatibilidade)
ETAPA_SISTEMA = ETAPA_SISTEMA_OBJETIVO
ETAPA_ALUNO = ETAPA_ALUNO_MENU

# Etapas em que o motor AGUARDA a resposta de uma pergunta especifica. Durante
# essas etapas a troca de caminho por reclassificacao e bloqueada (conservadora),
# evitando reiniciar a jornada por classificacao equivocada de uma resposta.
_ETAPAS_AGUARDANDO_RESPOSTA = frozenset({
    ETAPA_QUALIF_MEDICO,
    ETAPA_QUALIF_EXPERIENCIA,
    ETAPA_QUALIF_ESPECIALIDADE,
    ETAPA_ESCOLHA_TURMA,
    ETAPA_FECHAMENTO,
    ETAPA_LINK,
    ETAPA_SISTEMA_OBJETIVO,
    ETAPA_SISTEMA_LICENCIAMENTO,
    ETAPA_SISTEMA_FRANQUIA,
    ETAPA_SISTEMA_DIAGNOSTICO,
    ETAPA_ALUNO_MENU,
})

# Etapas pos-apresentacao (apresentacao ja foi enviada; mensagens entram como
# duvidas/fechamento).
_ETAPAS_POS_APRESENTACAO = frozenset({
    ETAPA_APRESENTACAO, ETAPA_DUVIDAS, ETAPA_FECHAMENTO, ETAPA_LINK,
})

# Numero da Nidia (fallback; ideal: carregar de settings)
_NIDIA_DEFAULT = "+55 21 97423-9844"

# Destinos LOGICOS de handoff (devem existir na HANDOFF_QUEUE_ALLOWLIST do
# ChatMaster; o queueId concreto vem da config do operador, nunca do LLM).
DEST_CONSULTORES = "consultores"    # generico de vendas / fallback
DEST_PRESENCIAL = "presencial"      # consultor de cursos presenciais (C2)
DEST_LICENCIAMENTO = "licenciamento"  # reuniao de Licenciamento (C3)
DEST_FRANQUIA = "franquia"          # especialista de Franquia (C3)
DEST_ESPECIALISTA = "especialista"  # diagnostico do Sistema (C3)
DEST_SUPORTE = "suporte"            # equipe de suporte ao aluno (C4)

# Limite de tentativas nao reconhecidas por etapa antes de encaminhar a humano.
_MAX_TENTATIVAS = 3


# ---------------------------------------------------------------------------
# i18n — textos deterministicos (anti-alucinacao: nunca passam pelo LLM)
# ---------------------------------------------------------------------------
_T: dict[str, dict[str, str]] = {
    "invite_duvidas": {
        "pt": "Fique à vontade para esclarecer qualquer dúvida sobre o treinamento. 😊",
        "en": "Feel free to ask any questions about the training. 😊",
        "es": "Quedo a tu disposición para cualquier duda sobre la formación. 😊",
    },
    "fechar_link": {
        "pt": "Gostaria de receber o link para realizar sua inscrição?",
        "en": "Would you like to receive the link to complete your registration?",
        "es": "¿Te gustaría recibir el enlace para realizar tu inscripción?",
    },
    "fechar_consultor": {
        "pt": (
            "Gostaria que eu encaminhasse seu interesse para um consultor da "
            "GoldIncision dar continuidade à sua inscrição?"
        ),
        "en": (
            "Would you like me to forward your interest to a GoldIncision "
            "consultant to continue your registration?"
        ),
        "es": (
            "¿Te gustaría que dirija tu interés a un consultor de GoldIncision "
            "para dar continuidad a tu inscripción?"
        ),
    },
    "fechar_recusa": {
        "pt": (
            "Sem problemas! Agradeço muito o seu interesse e fico à disposição "
            "sempre que precisar. 🙏"
        ),
        "en": (
            "No problem! Thank you very much for your interest — I'm here whenever "
            "you need. 🙏"
        ),
        "es": (
            "¡Sin problema! Agradezco mucho tu interés y quedo a tu disposición "
            "siempre que lo necesites. 🙏"
        ),
    },
    "link_leadin": {
        "pt": "Perfeito! Aqui está o link para realizar a sua inscrição:",
        "en": "Perfect! Here is the link to complete your registration:",
        "es": "¡Perfecto! Aquí está el enlace para realizar tu inscripción:",
    },
    "link_pos": {
        "pt": "Qualquer dúvida no processo, é só me chamar. 😊",
        "en": "If you have any questions during the process, just let me know. 😊",
        "es": "Cualquier duda en el proceso, solo avísame. 😊",
    },
    "consultor_handoff": {
        "pt": (
            "Perfeito! Vou encaminhar o seu interesse para um de nossos consultores, "
            "que dará continuidade à sua inscrição. Em breve entrarão em contato. 😊"
        ),
        "en": (
            "Perfect! I'll forward your interest to one of our consultants, who will "
            "continue your registration. They'll be in touch soon. 😊"
        ),
        "es": (
            "¡Perfecto! Voy a dirigir tu interés a uno de nuestros consultores, que "
            "dará continuidad a tu inscripción. Te contactarán pronto. 😊"
        ),
    },
    "nao_entendi": {
        "pt": "Desculpe, acho que não entendi bem. ",
        "en": "Sorry, I don't think I understood that. ",
        "es": "Disculpa, creo que no te entendí bien. ",
    },
    "desistir_handoff": {
        "pt": (
            "Para garantir o melhor atendimento, vou conectar você com um de nossos "
            "especialistas, que dará sequência pessoalmente. 🙏"
        ),
        "en": (
            "To make sure you get the best support, I'll connect you with one of our "
            "specialists, who will assist you personally. 🙏"
        ),
        "es": (
            "Para asegurar la mejor atención, voy a conectarte con uno de nuestros "
            "especialistas, que te atenderá personalmente. 🙏"
        ),
    },
    "humano_handoff": {
        "pt": (
            "Claro! Vou conectar você com um de nossos especialistas para dar "
            "continuidade ao seu atendimento. 🙏"
        ),
        "en": (
            "Of course! I'll connect you with one of our specialists to continue "
            "your service. 🙏"
        ),
        "es": (
            "¡Claro! Voy a conectarte con uno de nuestros especialistas para dar "
            "continuidad a tu atención. 🙏"
        ),
    },
    # Caminho 3 — ETAPA 1 (sistema nao e curso) + ETAPA 2 (objetivo)
    "sistema_etapa1_2": {
        "pt": (
            "Perfeito! 😊\n\n"
            "A técnica GoldIncision para Tratamento Avançado da Celulite não é "
            "disponibilizada por meio de um curso avulso.\n"
            "Atualmente, ela pode ser incorporada por meio de dois programas oficiais "
            "da GoldIncision:\n"
            "🔹 *Licenciamento GoldIncision* – exclusivo para médicos e disponível "
            "para o mercado internacional.\n"
            "🔹 *Franquia GoldIncision* – destinada a médicos ou investidores, "
            "disponível para cidades selecionadas no Brasil e também para o mercado "
            "internacional.\n\n"
            "Qual destas opções representa melhor o seu objetivo?\n"
            "1️⃣ Incorporar a técnica GoldIncision à minha clínica atual.\n"
            "2️⃣ Abrir uma Clínica GoldIncision completa.\n"
            "3️⃣ Ainda não tenho certeza e gostaria de entender qual modelo faz mais "
            "sentido para mim."
        ),
        "en": (
            "Perfect! 😊\n\n"
            "The GoldIncision technique for Advanced Cellulite Treatment is not "
            "offered through a standalone course.\n"
            "It can currently be incorporated through two official GoldIncision "
            "programs:\n"
            "🔹 *GoldIncision Licensing* – exclusive to physicians and available for "
            "the international market.\n"
            "🔹 *GoldIncision Franchise* – for physicians or investors, available in "
            "selected cities in Brazil and also internationally.\n\n"
            "Which of these options best represents your goal?\n"
            "1️⃣ Incorporate the GoldIncision technique into my current clinic.\n"
            "2️⃣ Open a complete GoldIncision Clinic.\n"
            "3️⃣ I'm not sure yet and would like to understand which model fits me best."
        ),
        "es": (
            "¡Perfecto! 😊\n\n"
            "La técnica GoldIncision para el Tratamiento Avanzado de la Celulitis no "
            "se ofrece mediante un curso suelto.\n"
            "Actualmente puede incorporarse a través de dos programas oficiales de "
            "GoldIncision:\n"
            "🔹 *Licenciamiento GoldIncision* – exclusivo para médicos y disponible "
            "para el mercado internacional.\n"
            "🔹 *Franquicia GoldIncision* – destinada a médicos o inversores, "
            "disponible en ciudades seleccionadas de Brasil y también a nivel "
            "internacional.\n\n"
            "¿Cuál de estas opciones representa mejor tu objetivo?\n"
            "1️⃣ Incorporar la técnica GoldIncision a mi clínica actual.\n"
            "2️⃣ Abrir una Clínica GoldIncision completa.\n"
            "3️⃣ Aún no estoy seguro y me gustaría entender qué modelo tiene más "
            "sentido para mí."
        ),
    },
    "sistema_lic_naomedico": {
        "pt": (
            "Obrigado por compartilhar! No momento, o Licenciamento GoldIncision é "
            "destinado exclusivamente a médicos.\n"
            "Caso o seu interesse seja investir em uma Clínica GoldIncision, terei o "
            "maior prazer em apresentar o modelo de Franquia. Vou conectar você com "
            "um especialista que detalhará essa oportunidade pessoalmente. 🙏"
        ),
        "en": (
            "Thank you for sharing! At the moment, GoldIncision Licensing is intended "
            "exclusively for physicians.\n"
            "If your interest is to invest in a GoldIncision Clinic, I'd be glad to "
            "present the Franchise model. I'll connect you with a specialist who will "
            "detail this opportunity personally. 🙏"
        ),
        "es": (
            "¡Gracias por compartir! Por el momento, el Licenciamiento GoldIncision "
            "está destinado exclusivamente a médicos.\n"
            "Si tu interés es invertir en una Clínica GoldIncision, con gusto te "
            "presentaré el modelo de Franquicia. Voy a conectarte con un especialista "
            "que detallará esta oportunidad personalmente. 🙏"
        ),
    },
    "sistema_franquia_pergunta": {
        "pt": (
            "Ótimo! Para que eu possa direcioná-lo corretamente, poderia me informar: "
            "você é médico ou investidor?"
        ),
        "en": (
            "Great! So I can direct you correctly, could you tell me: are you a "
            "physician or an investor?"
        ),
        "es": (
            "¡Genial! Para poder dirigirte correctamente, ¿podrías indicarme: eres "
            "médico o inversor?"
        ),
    },
    "sistema_franquia_handoff": {
        "pt": (
            "Perfeito! O modelo de Franquia GoldIncision é apresentado em detalhes "
            "por um de nossos especialistas, que cuidará disso pessoalmente com você. "
            "Vou encaminhar o seu interesse para que agendem uma reunião. 😊"
        ),
        "en": (
            "Perfect! The GoldIncision Franchise model is presented in detail by one "
            "of our specialists, who will handle this with you personally. I'll "
            "forward your interest so they can schedule a meeting. 😊"
        ),
        "es": (
            "¡Perfecto! El modelo de Franquicia GoldIncision lo presenta en detalle "
            "uno de nuestros especialistas, que lo atenderá personalmente contigo. "
            "Voy a dirigir tu interés para que agenden una reunión. 😊"
        ),
    },
    "sistema_reuniao_handoff": {
        "pt": (
            "Maravilha! O próximo passo é uma conversa com um de nossos especialistas, "
            "que vai apresentar todos os detalhes e tirar suas dúvidas pessoalmente. "
            "Vou encaminhar o seu interesse para agendarmos essa reunião. 😊"
        ),
        "en": (
            "Wonderful! The next step is a conversation with one of our specialists, "
            "who will present all the details and answer your questions personally. "
            "I'll forward your interest so we can schedule this meeting. 😊"
        ),
        "es": (
            "¡Maravilloso! El siguiente paso es una conversación con uno de nuestros "
            "especialistas, que presentará todos los detalles y resolverá tus dudas "
            "personalmente. Voy a dirigir tu interés para agendar esa reunión. 😊"
        ),
    },
    "sistema_diagnostico": {
        "pt": (
            "Perfeito, vamos entender juntos qual modelo faz mais sentido para você. "
            "Me conte um pouco:\n"
            "• Você pretende usar uma clínica que já possui ou abrir uma nova unidade?\n"
            "• O projeto seria no Brasil ou no exterior?\n"
            "• Você é médico ou investidor?"
        ),
        "en": (
            "Perfect, let's figure out together which model fits you best. Tell me a "
            "bit:\n"
            "• Do you intend to use a clinic you already own or open a new unit?\n"
            "• Would the project be in Brazil or abroad?\n"
            "• Are you a physician or an investor?"
        ),
        "es": (
            "Perfecto, entendamos juntos qué modelo tiene más sentido para ti. "
            "Cuéntame un poco:\n"
            "• ¿Piensas usar una clínica que ya tienes o abrir una nueva unidad?\n"
            "• ¿El proyecto sería en Brasil o en el exterior?\n"
            "• ¿Eres médico o inversor?"
        ),
    },
    "sistema_diagnostico_handoff": {
        "pt": (
            "Obrigado pelas informações! Com base no seu perfil, o melhor caminho é "
            "uma conversa com um de nossos especialistas, que vai recomendar o modelo "
            "ideal e conduzir os próximos passos com você. Vou encaminhar agora. 😊"
        ),
        "en": (
            "Thank you for the information! Based on your profile, the best path is a "
            "conversation with one of our specialists, who will recommend the ideal "
            "model and guide the next steps with you. I'll forward you now. 😊"
        ),
        "es": (
            "¡Gracias por la información! Según tu perfil, el mejor camino es una "
            "conversación con uno de nuestros especialistas, que recomendará el "
            "modelo ideal y guiará los próximos pasos contigo. Te dirijo ahora. 😊"
        ),
    },
    # Caminho 4 — submenu + encaminhamento
    "aluno_menu": {
        "pt": (
            "Perfeito! Ficarei feliz em direcionar o seu atendimento. 😊\n"
            "Para que nossa equipe possa ajudá-lo com mais agilidade, qual destes "
            "assuntos melhor representa sua necessidade?\n"
            "1️⃣ Plataforma e acesso ao curso\n"
            "2️⃣ Certificado de conclusão\n"
            "3️⃣ Grupo de suporte técnico\n"
            "4️⃣ Pagamento ou inscrição\n"
            "5️⃣ Dúvidas sobre o curso\n"
            "6️⃣ Outro assunto"
        ),
        "en": (
            "Perfect! I'll be happy to direct your request. 😊\n"
            "So our team can help you faster, which of these best represents your "
            "need?\n"
            "1️⃣ Platform and course access\n"
            "2️⃣ Completion certificate\n"
            "3️⃣ Technical support group\n"
            "4️⃣ Payment or registration\n"
            "5️⃣ Questions about the course\n"
            "6️⃣ Other subject"
        ),
        "es": (
            "¡Perfecto! Estaré encantado de dirigir tu atención. 😊\n"
            "Para que nuestro equipo pueda ayudarte con más agilidad, ¿cuál de estos "
            "asuntos representa mejor tu necesidad?\n"
            "1️⃣ Plataforma y acceso al curso\n"
            "2️⃣ Certificado de finalización\n"
            "3️⃣ Grupo de soporte técnico\n"
            "4️⃣ Pago o inscripción\n"
            "5️⃣ Dudas sobre el curso\n"
            "6️⃣ Otro asunto"
        ),
    },
    "aluno_encaminhamento": {
        "pt": (
            "Perfeito! Vou encaminhar sua solicitação para nossa equipe responsável, "
            "que dará continuidade ao seu atendimento.\n"
            "Caso seja necessário, nossa equipe poderá entrar em contato para "
            "solicitar informações complementares ou lhe chamar de outro número de "
            "WhatsApp. 😊"
        ),
        "en": (
            "Perfect! I'll forward your request to our responsible team, who will "
            "continue your service.\n"
            "If necessary, our team may contact you to request additional information "
            "or reach you from another WhatsApp number. 😊"
        ),
        "es": (
            "¡Perfecto! Voy a dirigir tu solicitud a nuestro equipo responsable, que "
            "dará continuidad a tu atención.\n"
            "Si es necesario, nuestro equipo podrá contactarte para solicitar "
            "información adicional o escribirte desde otro número de WhatsApp. 😊"
        ),
    },
    "outro_assunto": {
        "pt": (
            "Obrigado pela mensagem! Vou conectar você com nossa equipe para que "
            "possam auxiliá-lo com este assunto da melhor forma. 🙏"
        ),
        "en": (
            "Thank you for your message! I'll connect you with our team so they can "
            "assist you with this matter in the best way. 🙏"
        ),
        "es": (
            "¡Gracias por tu mensaje! Te voy a conectar con nuestro equipo para que "
            "puedan ayudarte con este tema de la mejor manera. 🙏"
        ),
    },
}


def _t(key: str, idioma: str) -> str:
    """Retorna o texto deterministico no idioma (fallback PT)."""
    bloco = _T.get(key, {})
    return bloco.get(idioma) or bloco.get("pt", "")


def _saudacao(context: SessionContext) -> str:
    """Saudacao curta com o nome do lead, quando disponivel (humanizacao)."""
    nome = (context.nome or "").strip()
    if nome:
        primeiro = nome.split()[0]
        return f"Perfeito, {primeiro}!"
    return "Perfeito!"


# ---------------------------------------------------------------------------
# Contador de tentativas (em Contato.etapa_funil — JSON, sem migration)
# ---------------------------------------------------------------------------

def _tent_count(context: SessionContext, etapa: str) -> int:
    """Quantas respostas nao reconhecidas ja houve para esta etapa."""
    raw = context.etapa_funil
    if not raw:
        return 0
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return 0
    if isinstance(data, dict) and data.get("et") == etapa:
        return int(data.get("n", 0))
    return 0


def _tent_bump(context: SessionContext, etapa: str, updates: dict) -> int:
    """Incrementa e persiste o contador de tentativas da etapa. Retorna o novo valor."""
    n = _tent_count(context, etapa) + 1
    payload = json.dumps({"et": etapa, "n": n})
    context.etapa_funil = payload
    updates["etapa_funil"] = payload
    return n


def _tent_clear(context: SessionContext, updates: dict) -> None:
    """Zera o contador de tentativas (resposta reconhecida / etapa avancou)."""
    if context.etapa_funil:
        context.etapa_funil = None
        updates["etapa_funil"] = None


class FlowResult:
    """Resultado do processamento de uma mensagem."""

    def __init__(
        self,
        response_text: str,
        action: str,  # "continue" | "handoff" | "end"
        caminho: Optional[int],
        etapa: Optional[str],
        updates: Optional[dict] = None,
        handoff_destino: Optional[str] = None,
        handoff_motivo: Optional[str] = None,
    ):
        self.response_text = response_text
        self.action = action
        self.caminho = caminho
        self.etapa = etapa
        self.updates = updates or {}
        # Destino LOGICO da fila de handoff (resolvido pelo caller via allowlist/
        # config; NUNCA vem do LLM — SEC-LLM-3). So relevante quando action="handoff".
        self.handoff_destino = handoff_destino
        self.handoff_motivo = handoff_motivo


class FlowEngine:
    """
    Motor de fluxo conversacional baseado no Mapa Mestre.

    Toda a leitura de Base Oficial passa por metodos `_load_*` (faceis de stubar
    em testes). A logica de estado (process + handlers) e deterministica; o LLM
    (responder.generate) e usado apenas para responder DUVIDAS/objecoes com
    grounding estrito na Base.
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
        """Processa a mensagem do lead e retorna FlowResult (resposta + acao + updates)."""
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

        # Atualizar idioma se mudou (US5)
        if idioma.value != context.idioma:
            logger.info(
                "flow: idioma alterado %s → %s ticket_id=%s",
                context.idioma, idioma.value, ticket_id,
            )
            context.idioma = idioma.value
            updates["idioma"] = idioma.value

        # 2. Pedido explicito de atendimento humano → handoff imediato (Regra 26),
        #    em qualquer ponto da jornada.
        if _pede_humano(user_message):
            return self._handoff(
                context, updates, context.caminho,
                _t("humano_handoff", context.idioma), etapa=ETAPA_HANDOFF,
                destino=DEST_CONSULTORES, motivo="pedido_humano",
            )

        # 3. Troca de caminho conservadora (Regra 10, mas sem reiniciar a jornada
        #    enquanto aguardamos a resposta de uma pergunta — fix #9).
        novo_caminho = INTENCAO_PARA_CAMINHO.get(intencao)
        aguardando = (
            context.caminho is not None
            and context.etapa in _ETAPAS_AGUARDANDO_RESPOSTA
        )
        if (
            novo_caminho is not None
            and context.caminho is not None
            and novo_caminho != context.caminho
            and intencao != ClassificacaoIntencao.AMBIGUA
            and not aguardando
        ):
            logger.info(
                "flow: mudanca de caminho %s → %s ticket_id=%s",
                context.caminho, novo_caminho, ticket_id,
            )
            context.caminho = novo_caminho
            context.etapa = None
            _tent_clear(context, updates)
            updates["caminho_atual"] = novo_caminho
            updates["etapa_mapa_mestre"] = None

        # 4. Determinar caminho ativo
        caminho_ativo = context.caminho or novo_caminho

        # 5. Sem caminho ou intencao ambigua → menu (apenas na 1a interacao)
        if caminho_ativo is None or intencao == ClassificacaoIntencao.AMBIGUA:
            if context.caminho is None:
                menu_text = await self._responder.generate_menu(context.idioma)
                context.caminho = None
                context.etapa = ETAPA_MENU
                updates["etapa_mapa_mestre"] = ETAPA_MENU
                return FlowResult(menu_text, "continue", None, ETAPA_MENU, updates)
            caminho_ativo = context.caminho

        if caminho_ativo is None:
            menu_text = await self._responder.generate_menu(context.idioma)
            return FlowResult(menu_text, "continue", None, ETAPA_MENU, updates)

        # 6. Despacho por caminho
        if caminho_ativo == CaminhoMapaMestre.PACIENTE_MODELO:
            return await self._handle_paciente_modelo(context, updates)
        if caminho_ativo == CaminhoMapaMestre.ALUNO_SUPORTE:
            return await self._handle_aluno_suporte(context, user_message, updates)
        if caminho_ativo == CaminhoMapaMestre.OUTRO_ASSUNTO:
            return await self._handle_outro_assunto(context, updates)
        if caminho_ativo == CaminhoMapaMestre.SISTEMA_GOLDINCISION:
            return await self._handle_sistema_goldincision(context, user_message, updates)
        if caminho_ativo == CaminhoMapaMestre.CURSOS_PRESENCIAIS:
            return await self._handle_cursos_presenciais(context, user_message, updates)
        if caminho_ativo == CaminhoMapaMestre.CURSO_ONLINE_HG:
            return await self._handle_curso_online(context, user_message, updates)

        # Fallback
        menu_text = await self._responder.generate_menu(context.idioma)
        return FlowResult(menu_text, "continue", None, ETAPA_MENU, updates)

    # ------------------------------------------------------------------
    # Helpers de resultado
    # ------------------------------------------------------------------

    def _handoff(
        self, context: SessionContext, updates: dict,
        caminho: Optional[int], texto: str, etapa: str = ETAPA_HANDOFF,
        destino: str = DEST_CONSULTORES, motivo: Optional[str] = None,
    ) -> FlowResult:
        if caminho is not None:
            updates["caminho_atual"] = caminho
        updates["etapa_mapa_mestre"] = etapa
        _tent_clear(context, updates)
        return FlowResult(
            texto, "handoff", caminho, etapa, updates,
            handoff_destino=destino, handoff_motivo=motivo,
        )

    async def _reformular_ou_handoff(
        self, context: SessionContext, updates: dict, caminho: int,
        etapa: str, pergunta: str,
    ) -> FlowResult:
        """
        Resposta nao reconhecida na etapa: incrementa tentativas. Apos N tentativas,
        encaminha a humano em vez de repetir a pergunta para sempre (robustez, #8).
        """
        n = _tent_bump(context, etapa, updates)
        if n >= _MAX_TENTATIVAS:
            return self._handoff(
                context, updates, caminho, _t("desistir_handoff", context.idioma),
                destino=DEST_CONSULTORES, motivo=f"nao_reconhecido:{etapa}",
            )
        updates["etapa_mapa_mestre"] = etapa
        texto = pergunta
        if n >= 2:
            # Reformula: prefixa um reconhecimento + repete a pergunta com clareza.
            texto = _t("nao_entendi", context.idioma) + pergunta
        return FlowResult(texto, "continue", caminho, etapa, updates)

    # ------------------------------------------------------------------
    # Caminho 5 — Paciente modelo
    # ------------------------------------------------------------------

    async def _handle_paciente_modelo(
        self, context: SessionContext, updates: dict
    ) -> FlowResult:
        resposta = await self._responder.generate_paciente_modelo(
            self._nidia_phone, context.idioma
        )
        updates["caminho_atual"] = CaminhoMapaMestre.PACIENTE_MODELO
        updates["etapa_mapa_mestre"] = ETAPA_PACIENTE
        return FlowResult(
            resposta, "end", CaminhoMapaMestre.PACIENTE_MODELO, ETAPA_PACIENTE, updates
        )

    # ------------------------------------------------------------------
    # Caminho 4 — Aluno/suporte (submenu 6 opcoes → encaminhamento)
    # ------------------------------------------------------------------

    async def _handle_aluno_suporte(
        self, context: SessionContext, user_message: str, updates: dict
    ) -> FlowResult:
        updates["caminho_atual"] = CaminhoMapaMestre.ALUNO_SUPORTE

        # ETAPA 2 — ja apresentamos o submenu: registrar a opcao e encaminhar.
        if context.etapa == ETAPA_ALUNO_MENU:
            opcao = _detectar_opcao_aluno(user_message)
            if opcao is None:
                return await self._reformular_ou_handoff(
                    context, updates, CaminhoMapaMestre.ALUNO_SUPORTE,
                    ETAPA_ALUNO_MENU, _t("aluno_menu", context.idioma),
                )
            # Observabilidade: registrar a opcao escolhida no handoff.
            logger.info(
                "flow: aluno_suporte opcao escolhida=%s contato_id=%s",
                opcao, context.contato_id,
            )
            _tent_clear(context, updates)
            return self._handoff(
                context, updates, CaminhoMapaMestre.ALUNO_SUPORTE,
                _t("aluno_encaminhamento", context.idioma),
                destino=DEST_SUPORTE, motivo=f"aluno:{opcao}",
            )

        # ETAPA 1 — apresentar o submenu
        updates["etapa_mapa_mestre"] = ETAPA_ALUNO_MENU
        return FlowResult(
            _t("aluno_menu", context.idioma), "continue",
            CaminhoMapaMestre.ALUNO_SUPORTE, ETAPA_ALUNO_MENU, updates,
        )

    # ------------------------------------------------------------------
    # Caminho 6 — Outro assunto
    # ------------------------------------------------------------------

    async def _handle_outro_assunto(
        self, context: SessionContext, updates: dict
    ) -> FlowResult:
        return self._handoff(
            context, updates, CaminhoMapaMestre.OUTRO_ASSUNTO,
            _t("outro_assunto", context.idioma),
            destino=DEST_CONSULTORES, motivo="outro_assunto",
        )

    # ------------------------------------------------------------------
    # Caminho 3 — Sistema GoldIncision (ETAPA 1 → ETAPA 2 → sub-caminhos)
    # ------------------------------------------------------------------

    async def _handle_sistema_goldincision(
        self, context: SessionContext, user_message: str, updates: dict
    ) -> FlowResult:
        context.caminho = CaminhoMapaMestre.SISTEMA_GOLDINCISION
        updates["caminho_atual"] = CaminhoMapaMestre.SISTEMA_GOLDINCISION
        idioma = context.idioma

        # ETAPA 2 — objetivo (menu de 3 opcoes)
        if context.etapa == ETAPA_SISTEMA_OBJETIVO:
            objetivo = _detectar_objetivo_sistema(user_message)
            if objetivo is None:
                return await self._reformular_ou_handoff(
                    context, updates, CaminhoMapaMestre.SISTEMA_GOLDINCISION,
                    ETAPA_SISTEMA_OBJETIVO, _t("sistema_etapa1_2", idioma),
                )
            _tent_clear(context, updates)
            if objetivo == "incorporar":
                # Sub-caminho 1 → qualificar medico (Licenciamento)
                updates["etapa_mapa_mestre"] = ETAPA_SISTEMA_LICENCIAMENTO
                pergunta = await self._gerar_pergunta_medico(idioma)
                return FlowResult(
                    pergunta, "continue", CaminhoMapaMestre.SISTEMA_GOLDINCISION,
                    ETAPA_SISTEMA_LICENCIAMENTO, updates,
                )
            if objetivo == "abrir":
                # Sub-caminho 2 → Franquia: "medico ou investidor?"
                updates["etapa_mapa_mestre"] = ETAPA_SISTEMA_FRANQUIA
                return FlowResult(
                    _t("sistema_franquia_pergunta", idioma), "continue",
                    CaminhoMapaMestre.SISTEMA_GOLDINCISION, ETAPA_SISTEMA_FRANQUIA, updates,
                )
            # objetivo == "nao_sei" → Sub-caminho 3: diagnostico
            updates["etapa_mapa_mestre"] = ETAPA_SISTEMA_DIAGNOSTICO
            return FlowResult(
                _t("sistema_diagnostico", idioma), "continue",
                CaminhoMapaMestre.SISTEMA_GOLDINCISION, ETAPA_SISTEMA_DIAGNOSTICO, updates,
            )

        # Sub-caminho 1 — Licenciamento (qualificacao medica)
        if context.etapa == ETAPA_SISTEMA_LICENCIAMENTO:
            eh_medico = _detectar_confirmacao(user_message)
            if eh_medico is None:
                return await self._reformular_ou_handoff(
                    context, updates, CaminhoMapaMestre.SISTEMA_GOLDINCISION,
                    ETAPA_SISTEMA_LICENCIAMENTO, await self._gerar_pergunta_medico(idioma),
                )
            context.eh_medico = eh_medico
            updates["eh_medico"] = eh_medico
            _tent_clear(context, updates)
            if eh_medico is False:
                # Nao medico → Licenciamento e exclusivo; oferecer Franquia → especialista
                return self._handoff(
                    context, updates, CaminhoMapaMestre.SISTEMA_GOLDINCISION,
                    _t("sistema_lic_naomedico", idioma),
                    destino=DEST_FRANQUIA, motivo="licenciamento_nao_medico_oferece_franquia",
                )
            # Medico → apresentar Licenciamento (verbatim da Base) + abrir duvidas
            apres = await self._load_apresentacao(_SLUG_LICENCIAMENTO, idioma)
            leadin = f"{_saudacao(context)} 😊\n\n"
            corpo = apres or ""
            texto = (leadin + corpo + "\n\n" + _t("invite_duvidas", idioma)).strip()
            updates["etapa_mapa_mestre"] = ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS
            return FlowResult(
                texto, "continue", CaminhoMapaMestre.SISTEMA_GOLDINCISION,
                ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS, updates,
            )

        # Sub-caminho 1 — Licenciamento: fase de DUVIDAS
        if context.etapa == ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS:
            fech = _detectar_fechamento(user_message)
            if fech == "aceita" or _sem_mais_duvidas(user_message):
                # Pronto / sem mais duvidas → convidar para reuniao (handoff)
                return self._handoff(
                    context, updates, CaminhoMapaMestre.SISTEMA_GOLDINCISION,
                    _t("sistema_reuniao_handoff", idioma),
                    destino=DEST_LICENCIAMENTO, motivo="reuniao_licenciamento",
                )
            # Duvida OU objecao (mesmo sem "?") → responder com grounding na Base
            # e no Banco de Objecoes do Licenciamento (nao encaminhar prematuramente).
            knowledge = await self._load_knowledge_by_slug(_SLUG_LICENCIAMENTO, idioma)
            history = self._memory.build_messages_for_llm(context, max_msgs=8)
            resposta, handoff = await self._responder.generate(
                user_message=user_message, caminho=_SLUG_LICENCIAMENTO,
                etapa=ETAPA_DUVIDAS, knowledge_context=knowledge,
                session_history=history, session_summary=context.resumo_rolante,
                idioma=idioma,
            )
            updates["etapa_mapa_mestre"] = ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS
            action = "handoff" if handoff else "continue"
            return FlowResult(
                resposta, action, CaminhoMapaMestre.SISTEMA_GOLDINCISION,
                ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS, updates,
            )

        # Sub-caminho 2 — Franquia: apos "medico ou investidor?" → especialista
        if context.etapa == ETAPA_SISTEMA_FRANQUIA:
            # Conteudo de Franquia ainda nao existe na Base (ver Pendencias): encaminhar
            # a um especialista, sem inventar.
            logger.info(
                "flow: sistema franquia perfil=%s contato_id=%s",
                _detectar_medico_investidor(user_message), context.contato_id,
            )
            return self._handoff(
                context, updates, CaminhoMapaMestre.SISTEMA_GOLDINCISION,
                _t("sistema_franquia_handoff", idioma),
                destino=DEST_FRANQUIA, motivo="franquia",
            )

        # Sub-caminho 3 — Diagnostico: apos respostas → recomenda + especialista
        if context.etapa == ETAPA_SISTEMA_DIAGNOSTICO:
            return self._handoff(
                context, updates, CaminhoMapaMestre.SISTEMA_GOLDINCISION,
                _t("sistema_diagnostico_handoff", idioma),
                destino=DEST_ESPECIALISTA, motivo="diagnostico_sistema",
            )

        # ETAPA 1 + ETAPA 2 (entrada): explicar o sistema e perguntar o objetivo
        updates["etapa_mapa_mestre"] = ETAPA_SISTEMA_OBJETIVO
        return FlowResult(
            _t("sistema_etapa1_2", idioma), "continue",
            CaminhoMapaMestre.SISTEMA_GOLDINCISION, ETAPA_SISTEMA_OBJETIVO, updates,
        )

    # ------------------------------------------------------------------
    # Caminho 1 — Curso Online HG
    # ------------------------------------------------------------------

    async def _handle_curso_online(
        self, context: SessionContext, user_message: str, updates: dict
    ) -> FlowResult:
        context.caminho = CaminhoMapaMestre.CURSO_ONLINE_HG
        updates["caminho_atual"] = CaminhoMapaMestre.CURSO_ONLINE_HG
        idioma = context.idioma
        cam = CaminhoMapaMestre.CURSO_ONLINE_HG

        # Etapa de fechamento (oferta de link) — interpretar sim/nao
        if context.etapa == ETAPA_FECHAMENTO:
            ans = _detectar_confirmacao(user_message)
            if ans is True:
                _tent_clear(context, updates)
                return await self._close_curso_online_link(context, updates)
            if ans is False:
                _tent_clear(context, updates)
                updates["etapa_mapa_mestre"] = ETAPA_DUVIDAS
                return FlowResult(
                    _t("fechar_recusa", idioma), "continue", cam, ETAPA_DUVIDAS, updates,
                )
            return await self._reformular_ou_handoff(
                context, updates, cam, ETAPA_FECHAMENTO, _t("fechar_link", idioma),
            )

        # Etapa de link pendente de qualificacao medica (gate de fechamento)
        if context.etapa == ETAPA_LINK:
            eh_medico = _detectar_confirmacao(user_message)
            if eh_medico is None:
                return await self._reformular_ou_handoff(
                    context, updates, cam, ETAPA_LINK,
                    await self._gerar_pergunta_medico(idioma),
                )
            context.eh_medico = eh_medico
            updates["eh_medico"] = eh_medico
            _tent_clear(context, updates)
            if eh_medico is False:
                return await self._encerra_nao_elegivel(context, updates, cam)
            return await self._enviar_link_curso_online(context, updates)

        # Confirmacao de medico (etapa de qualificacao)
        if context.etapa == ETAPA_QUALIF_MEDICO:
            eh_medico = _detectar_confirmacao(user_message)
            if eh_medico is not None:
                context.eh_medico = eh_medico
                updates["eh_medico"] = eh_medico
                _tent_clear(context, updates)
            elif not _eh_pergunta_informativa(user_message):
                return await self._reformular_ou_handoff(
                    context, updates, cam, ETAPA_QUALIF_MEDICO,
                    await self._gerar_pergunta_medico(idioma),
                )

        # Sinal forte de fechamento (quer o link / inscrever-se): conduzir ao
        # fechamento mesmo que ainda nao tenhamos qualificado — o gate medico e
        # aplicado dentro de _close_curso_online_link.
        fech = _detectar_fechamento(user_message)
        if fech == "aceita":
            updates["etapa_mapa_mestre"] = ETAPA_FECHAMENTO
            return await self._close_curso_online_link(context, updates)

        # Pergunta informativa direta (preco/conteudo/duracao/certificado) ANTES
        # de qualificar → responder da Base na hora (Mapa Mestre, REGRA do Caminho 1).
        # Limpa o contador de tentativas: a mudanca de etapa (→ duvidas) nao deve
        # deixar preso o contador de qualif_medico (evita handoff prematuro).
        if context.eh_medico is None and _eh_pergunta_informativa(user_message):
            _tent_clear(context, updates)
            return await self._responder_duvida_online(context, user_message, updates)

        # Qualificacao medica (so quando nao e pergunta direta)
        if context.eh_medico is None:
            updates["etapa_mapa_mestre"] = ETAPA_QUALIF_MEDICO
            return FlowResult(
                await self._gerar_pergunta_medico(idioma), "continue",
                cam, ETAPA_QUALIF_MEDICO, updates,
            )
        if context.eh_medico is False:
            return await self._encerra_nao_elegivel(context, updates, cam)

        # Elegivel: decidir entre apresentar, responder duvida ou fechar
        if fech == "recusa":
            updates["etapa_mapa_mestre"] = ETAPA_DUVIDAS
            return FlowResult(
                _t("fechar_recusa", idioma), "continue", cam, ETAPA_DUVIDAS, updates,
            )

        ja_apresentou = context.etapa in _ETAPAS_POS_APRESENTACAO
        if ja_apresentou or _eh_pergunta(user_message):
            return await self._responder_duvida_online(context, user_message, updates)

        # Primeira vez elegivel e nao e pergunta → apresentar o curso (verbatim)
        return await self._apresentar_curso_online(context, updates)

    async def _apresentar_curso_online(
        self, context: SessionContext, updates: dict
    ) -> FlowResult:
        idioma = context.idioma
        apres = await self._load_apresentacao("curso-online-hg", idioma)
        leadin = (
            f"{_saudacao(context)} Que ótimo o seu interesse no Curso Online de "
            "Harmonização Glútea! 😊\n\n"
        )
        texto = (leadin + (apres or "") + "\n\n" + _t("invite_duvidas", idioma)).strip()
        updates["etapa_mapa_mestre"] = ETAPA_DUVIDAS
        return FlowResult(
            texto, "continue", CaminhoMapaMestre.CURSO_ONLINE_HG, ETAPA_DUVIDAS, updates,
        )

    async def _responder_duvida_online(
        self, context: SessionContext, user_message: str, updates: dict
    ) -> FlowResult:
        idioma = context.idioma
        knowledge = await self._load_knowledge_by_slug("curso-online-hg", idioma)
        history = self._memory.build_messages_for_llm(context, max_msgs=8)
        resposta, handoff = await self._responder.generate(
            user_message=user_message, caminho="curso-online-hg",
            etapa=ETAPA_DUVIDAS, knowledge_context=knowledge,
            session_history=history, session_summary=context.resumo_rolante,
            idioma=idioma,
        )
        updates["etapa_mapa_mestre"] = ETAPA_DUVIDAS
        action = "handoff" if handoff else "continue"
        return FlowResult(
            resposta, action, CaminhoMapaMestre.CURSO_ONLINE_HG, ETAPA_DUVIDAS, updates,
        )

    async def _close_curso_online_link(
        self, context: SessionContext, updates: dict
    ) -> FlowResult:
        """Fechamento do Caminho 1: qualificacao medica gateia o envio do link."""
        cam = CaminhoMapaMestre.CURSO_ONLINE_HG
        if context.eh_medico is True:
            return await self._enviar_link_curso_online(context, updates)
        if context.eh_medico is False:
            return await self._encerra_nao_elegivel(context, updates, cam)
        # Medico desconhecido → qualificar antes de liberar o link (gate de fechamento)
        updates["etapa_mapa_mestre"] = ETAPA_LINK
        return FlowResult(
            await self._gerar_pergunta_medico(context.idioma), "continue",
            cam, ETAPA_LINK, updates,
        )

    async def _enviar_link_curso_online(
        self, context: SessionContext, updates: dict
    ) -> FlowResult:
        idioma = context.idioma
        cam = CaminhoMapaMestre.CURSO_ONLINE_HG
        url = await self._load_curso_link("curso-online-hg", idioma)
        if not url:
            # Sem link cadastrado → encaminhar a um humano (nao inventar)
            return self._handoff(
                context, updates, cam, _t("desistir_handoff", idioma),
                destino=DEST_CONSULTORES, motivo="link_indisponivel",
            )
        texto = f"{_t('link_leadin', idioma)}\n{url}\n\n{_t('link_pos', idioma)}"
        updates["produto_interesse"] = "curso-online-hg"
        updates["etapa_mapa_mestre"] = ETAPA_DUVIDAS
        return FlowResult(texto, "continue", cam, ETAPA_DUVIDAS, updates)

    async def _encerra_nao_elegivel(
        self, context: SessionContext, updates: dict, caminho: int
    ) -> FlowResult:
        resposta = await self._responder.generate_not_eligible(context.idioma)
        updates["caminho_atual"] = caminho
        updates["etapa_mapa_mestre"] = ETAPA_HANDOFF
        _tent_clear(context, updates)
        # Mapa Mestre: nao-medico → agradece e ENCERRA o atendimento.
        return FlowResult(resposta, "end", caminho, ETAPA_HANDOFF, updates)

    # ------------------------------------------------------------------
    # Caminho 2 — Cursos Presenciais HG
    # ------------------------------------------------------------------

    async def _handle_cursos_presenciais(
        self, context: SessionContext, user_message: str, updates: dict
    ) -> FlowResult:
        context.caminho = CaminhoMapaMestre.CURSOS_PRESENCIAIS
        updates["caminho_atual"] = CaminhoMapaMestre.CURSOS_PRESENCIAIS
        idioma = context.idioma
        cam = CaminhoMapaMestre.CURSOS_PRESENCIAIS

        # --- Interpretar respostas conforme a etapa corrente ---
        if context.etapa == ETAPA_QUALIF_MEDICO:
            eh_medico = _detectar_confirmacao(user_message)
            if eh_medico is not None:
                context.eh_medico = eh_medico
                updates["eh_medico"] = eh_medico
                _tent_clear(context, updates)
            else:
                return await self._reformular_ou_handoff(
                    context, updates, cam, ETAPA_QUALIF_MEDICO,
                    await self._gerar_pergunta_medico(idioma),
                )

        if context.etapa == ETAPA_QUALIF_EXPERIENCIA and context.experiencia_corporal is None:
            exp = _detectar_experiencia_corporal(user_message)
            if exp is not None:
                context.experiencia_corporal = exp
                updates["experiencia_corporal"] = exp
                _tent_clear(context, updates)
            else:
                return await self._reformular_ou_handoff(
                    context, updates, cam, ETAPA_QUALIF_EXPERIENCIA,
                    await self._gerar_pergunta_experiencia(idioma),
                )

        if context.etapa == ETAPA_QUALIF_ESPECIALIDADE and not context.especialidade:
            especialidade = _detectar_especialidade(user_message)
            if especialidade is not None:
                context.especialidade = especialidade
                updates["especialidade"] = especialidade
                _tent_clear(context, updates)
            else:
                return await self._reformular_ou_handoff(
                    context, updates, cam, ETAPA_QUALIF_ESPECIALIDADE,
                    await self._gerar_pergunta_especialidade(idioma),
                )

        if context.etapa == ETAPA_ESCOLHA_TURMA and not context.produto_interesse:
            slug_escolhido = _detectar_escolha_turma(user_message)
            if slug_escolhido:
                context.produto_interesse = slug_escolhido
                updates["produto_interesse"] = slug_escolhido
                _tent_clear(context, updates)
            else:
                return await self._reformular_ou_handoff(
                    context, updates, cam, ETAPA_ESCOLHA_TURMA,
                    await self._gerar_pergunta_escolha_turma(idioma),
                )

        # --- Fluxo de qualificacao (ETAPA 1) ---
        if context.eh_medico is None:
            updates["etapa_mapa_mestre"] = ETAPA_QUALIF_MEDICO
            return FlowResult(
                await self._gerar_pergunta_medico(idioma), "continue",
                cam, ETAPA_QUALIF_MEDICO, updates,
            )
        if context.eh_medico is False:
            return await self._encerra_nao_elegivel(context, updates, cam)

        # ETAPA 2 — experiencia corporal
        if context.experiencia_corporal is None and not context.produto_interesse:
            updates["etapa_mapa_mestre"] = ETAPA_QUALIF_EXPERIENCIA
            return FlowResult(
                await self._gerar_pergunta_experiencia(idioma), "continue",
                cam, ETAPA_QUALIF_EXPERIENCIA, updates,
            )

        # ETAPA 3 — especialidade (so quando sem experiencia corporal)
        if (
            context.experiencia_corporal is False
            and not context.especialidade
            and not context.produto_interesse
        ):
            updates["etapa_mapa_mestre"] = ETAPA_QUALIF_ESPECIALIDADE
            return FlowResult(
                await self._gerar_pergunta_especialidade(idioma), "continue",
                cam, ETAPA_QUALIF_ESPECIALIDADE, updates,
            )

        # Determinar sub-curso recomendado
        if not context.produto_interesse:
            slug_recomendado = self._recomendar_sub_curso(context)
            if slug_recomendado in (_SLUG_HG360_SP, _SLUG_HG360_BARCELONA):
                # ETAPA 4 — elegivel ao HG360: escolher turma
                updates["etapa_mapa_mestre"] = ETAPA_ESCOLHA_TURMA
                return FlowResult(
                    await self._gerar_pergunta_escolha_turma(idioma), "continue",
                    cam, ETAPA_ESCOLHA_TURMA, updates,
                )
            # HG Modulo 1 (trilha)
            context.produto_interesse = _SLUG_HG_MODULO_1
            updates["produto_interesse"] = _SLUG_HG_MODULO_1

        slug = context.produto_interesse or _SLUG_HG_MODULO_1

        # Pos-apresentacao: fechamento (consultor) / duvidas
        ja_apresentou = context.etapa in _ETAPAS_POS_APRESENTACAO
        fech = _detectar_fechamento(user_message)
        if ja_apresentou or fech is not None:
            if fech == "aceita":
                return self._handoff(
                    context, updates, cam, _t("consultor_handoff", idioma),
                    destino=DEST_PRESENCIAL, motivo=f"consultor_presencial:{slug}",
                )
            if fech == "recusa":
                updates["etapa_mapa_mestre"] = ETAPA_DUVIDAS
                return FlowResult(
                    _t("fechar_recusa", idioma), "continue", cam, ETAPA_DUVIDAS, updates,
                )
            # Duvida → responder com grounding (prompt por SLUG; corrige colisao)
            return await self._responder_duvida_presencial(context, user_message, slug, updates)

        # Primeira vez com produto definido → apresentar (trilha quando Modulo 1)
        return await self._apresentar_presencial(context, slug, updates)

    async def _apresentar_presencial(
        self, context: SessionContext, slug: str, updates: dict
    ) -> FlowResult:
        idioma = context.idioma
        cam = CaminhoMapaMestre.CURSOS_PRESENCIAIS
        leadin = f"{_saudacao(context)} 😊\n\n"

        if slug == _SLUG_HG_MODULO_1:
            # REGRA IMPORTANTE: apresentar HG Modulo 1 + HG360 SP juntos (trilha).
            apres_mod1 = await self._load_apresentacao(_SLUG_HG_MODULO_1, idioma)
            apres_360 = await self._load_apresentacao(_SLUG_HG360_SP, idioma)
            partes = [leadin.strip()]
            if apres_mod1:
                partes.append(apres_mod1)
            if apres_360:
                conector = {
                    "pt": "👉 E, como trilha de evolução recomendada, veja também o HG360:",
                    "en": "👉 And, as the recommended learning path, here is the HG360 too:",
                    "es": "👉 Y, como ruta de evolución recomendada, mira también el HG360:",
                }.get(idioma, "")
                partes.append(conector + "\n\n" + apres_360)
            partes.append(_t("invite_duvidas", idioma))
            texto = "\n\n".join(p for p in partes if p).strip()
        else:
            apres = await self._load_apresentacao(slug, idioma)
            texto = (leadin + (apres or "") + "\n\n" + _t("invite_duvidas", idioma)).strip()

        updates["etapa_mapa_mestre"] = ETAPA_DUVIDAS
        return FlowResult(texto, "continue", cam, ETAPA_DUVIDAS, updates)

    async def _responder_duvida_presencial(
        self, context: SessionContext, user_message: str, slug: str, updates: dict
    ) -> FlowResult:
        idioma = context.idioma
        cam = CaminhoMapaMestre.CURSOS_PRESENCIAIS
        knowledge = await self._load_knowledge_by_slug(slug, idioma)
        history = self._memory.build_messages_for_llm(context, max_msgs=8)
        # Despacho por SLUG (corrige o bug de colisao de prompts numericos).
        prompt_key = "trilha-hg" if slug == _SLUG_HG_MODULO_1 else slug
        resposta, handoff = await self._responder.generate(
            user_message=user_message, caminho=prompt_key,
            etapa=ETAPA_DUVIDAS, knowledge_context=knowledge,
            session_history=history, session_summary=context.resumo_rolante,
            idioma=idioma,
        )
        updates["etapa_mapa_mestre"] = ETAPA_DUVIDAS
        action = "handoff" if handoff else "continue"
        return FlowResult(resposta, action, cam, ETAPA_DUVIDAS, updates)

    def _recomendar_sub_curso(self, context: SessionContext) -> str:
        """
        Sub-curso recomendado conforme o Mapa Mestre:
        - Experiencia corporal SIM → HG360 (turma escolhida depois)
        - Sem experiencia + especialidade qualificante → HG360
        - Sem experiencia + especialidade nao qualificante → HG Modulo 1 (trilha)
        """
        if context.experiencia_corporal:
            return _SLUG_HG360_SP
        esp = (context.especialidade or "").lower().strip()
        for esp_qualif in _ESPECIALIDADES_HG360:
            if esp_qualif in esp:
                return _SLUG_HG360_SP
        return _SLUG_HG_MODULO_1

    # ------------------------------------------------------------------
    # Carregamento de conhecimento do banco
    # ------------------------------------------------------------------

    async def _load_knowledge(self, caminho: int, idioma: str) -> str:
        slug = _CAMINHO_PARA_SLUG.get(caminho)
        if slug is None:
            return ""
        return await self._load_knowledge_by_slug(slug=slug, idioma=idioma)

    async def _load_apresentacao(self, slug: str, idioma: str) -> str:
        """Carrega APENAS a apresentacao oficial verbatim (fallback PT)."""
        stmt_curso = select(Curso).where(Curso.slug == slug, Curso.ativo.is_(True))
        curso = (await self._db.execute(stmt_curso)).scalar_one_or_none()
        if curso is None:
            return ""
        stmt = select(CursoApresentacao).where(
            CursoApresentacao.curso_id == curso.id,
            CursoApresentacao.idioma == idioma,
        )
        apres = (await self._db.execute(stmt)).scalar_one_or_none()
        if apres is None and idioma != "pt":
            stmt_pt = select(CursoApresentacao).where(
                CursoApresentacao.curso_id == curso.id,
                CursoApresentacao.idioma == "pt",
            )
            apres = (await self._db.execute(stmt_pt)).scalar_one_or_none()
        return apres.texto if apres else ""

    async def _load_curso_link(self, slug: str, idioma: str) -> Optional[str]:
        """Carrega o link de inscricao do curso no idioma (fallback PT)."""
        stmt_curso = select(Curso).where(Curso.slug == slug, Curso.ativo.is_(True))
        curso = (await self._db.execute(stmt_curso)).scalar_one_or_none()
        if curso is None:
            return None
        stmt = select(CursoLink).where(
            CursoLink.curso_id == curso.id, CursoLink.idioma == idioma,
        )
        link = (await self._db.execute(stmt)).scalar_one_or_none()
        if link is None and idioma != "pt":
            stmt_pt = select(CursoLink).where(
                CursoLink.curso_id == curso.id, CursoLink.idioma == "pt",
            )
            link = (await self._db.execute(stmt_pt)).scalar_one_or_none()
        return link.url if link else None

    async def _load_knowledge_by_slug(self, slug: str, idioma: str) -> str:
        """
        Carrega base de conhecimento do banco para o slug e idioma.
        Hierarquia: Apresentacao + Objecoes (por idioma) + Turmas + Links + FAQ.
        """
        stmt_curso = select(Curso).where(Curso.slug == slug, Curso.ativo.is_(True))
        result = await self._db.execute(stmt_curso)
        curso = result.scalar_one_or_none()
        if curso is None:
            logger.warning("flow: curso nao encontrado slug=%s", slug)
            return ""

        sections: list[str] = []

        stmt_apres = select(CursoApresentacao).where(
            CursoApresentacao.curso_id == curso.id,
            CursoApresentacao.idioma == idioma,
        )
        result = await self._db.execute(stmt_apres)
        apres = result.scalar_one_or_none()
        if apres is None and idioma != "pt":
            stmt_apres_pt = select(CursoApresentacao).where(
                CursoApresentacao.curso_id == curso.id,
                CursoApresentacao.idioma == "pt",
            )
            result = await self._db.execute(stmt_apres_pt)
            apres = result.scalar_one_or_none()
        if apres:
            sections.append(f"=== APRESENTACAO OFICIAL ({idioma}) ===\n{apres.texto}")

        stmt_obj = select(CursoObjecao).where(
            CursoObjecao.curso_id == curso.id, CursoObjecao.idioma == idioma,
        )
        result = await self._db.execute(stmt_obj)
        objecoes = result.scalars().all()
        if not objecoes and idioma != "pt":
            stmt_obj_pt = select(CursoObjecao).where(
                CursoObjecao.curso_id == curso.id, CursoObjecao.idioma == "pt",
            )
            result = await self._db.execute(stmt_obj_pt)
            objecoes = result.scalars().all()
        if objecoes:
            obj_text = "\n".join(
                f"- Objecao: {o.objecao}\n  Resposta: {o.resposta}" for o in objecoes
            )
            sections.append(f"=== BANCO DE OBJECOES OFICIAL ===\n{obj_text}")

        stmt_turmas = select(CursoTurma).where(
            CursoTurma.curso_id == curso.id, CursoTurma.ativo.is_(True),
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

        stmt_links = select(CursoLink).where(
            CursoLink.curso_id == curso.id, CursoLink.idioma == idioma,
        )
        result = await self._db.execute(stmt_links)
        link = result.scalar_one_or_none()
        if link is None and idioma != "pt":
            stmt_links_pt = select(CursoLink).where(
                CursoLink.curso_id == curso.id, CursoLink.idioma == "pt",
            )
            result = await self._db.execute(stmt_links_pt)
            link = result.scalar_one_or_none()
        if link:
            sections.append(f"=== LINK DE INSCRICAO ({idioma}) ===\n{link.url}")

        faq_text = await self._load_faq(idioma)
        if faq_text:
            sections.append(
                "=== FAQ OFICIAL (consultar SOMENTE se a resposta nao estiver "
                f"nas secoes acima) ===\n{faq_text}"
            )

        return "\n\n".join(sections)

    async def _load_faq(self, idioma: str) -> str:
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
    # Helpers de geracao de perguntas padrao (texto fixo — anti-alucinacao)
    # ------------------------------------------------------------------

    async def _gerar_pergunta_medico(self, idioma: str) -> str:
        if idioma == "en":
            return (
                "Great! Before we proceed, I need to confirm one detail: are you a "
                "physician with an active professional registration in your country? 🩺"
            )
        elif idioma == "es":
            return (
                "¡Genial! Antes de continuar, necesito confirmar un detalle: ¿eres "
                "médico con registro profesional activo en tu país? 🩺"
            )
        return (
            "Ótimo! Antes de prosseguirmos, preciso confirmar uma informação: você é "
            "médico com registro profissional ativo em seu país? 🩺"
        )

    async def _gerar_pergunta_experiencia(self, idioma: str) -> str:
        if idioma == "en":
            return (
                "To indicate the most suitable training for your moment: do you "
                "already work with Corporal Harmonization or gluteal fillers? "
                "(facial experience alone does not count) 💉"
            )
        elif idioma == "es":
            return (
                "Para indicarte la formación más adecuada a tu momento: ¿ya trabajas "
                "con Armonización Corporal o rellenos glúteos? (la experiencia solo "
                "facial no cuenta) 💉"
            )
        return (
            "Para indicar a formação mais adequada ao seu momento profissional: você "
            "já atua com Harmonização Corporal ou preenchimento de glúteo? "
            "(experiência apenas facial não conta) 💉"
        )

    async def _gerar_pergunta_especialidade(self, idioma: str) -> str:
        if idioma == "en":
            return (
                "To direct you to the most suitable course, could you tell me your "
                "medical specialty?\n"
                "• Dermatology\n• Plastic Surgery\n• Vascular Surgery\n"
                "• Other specialty\n• I don't have a specialty"
            )
        elif idioma == "es":
            return (
                "Para dirigirte al curso más adecuado, ¿podrías decirme tu "
                "especialidad médica?\n"
                "• Dermatología\n• Cirugía Plástica\n• Cirugía Vascular\n"
                "• Otra especialidad\n• No tengo especialidad"
            )
        return (
            "Para indicar a formação mais adequada ao seu perfil, poderia me informar "
            "sua especialidade médica?\n"
            "• Dermatologia\n• Cirurgia Plástica\n• Cirurgia Vascular\n"
            "• Outra especialidade\n• Não possuo especialidade"
        )

    async def _gerar_pergunta_escolha_turma(self, idioma: str) -> str:
        if idioma == "en":
            return (
                "We currently have two HG360 sessions available. Which one interests "
                "you most?\n\n"
                "1️⃣ São Paulo – August 28-30, 2026\n"
                "2️⃣ Barcelona – July 24-25, 2026"
            )
        elif idioma == "es":
            return (
                "Actualmente tenemos dos grupos del HG360 disponibles. ¿Cuál te "
                "interesa más?\n\n"
                "1️⃣ São Paulo – 28 a 30/08/2026\n"
                "2️⃣ Barcelona – 24 y 25/07/2026"
            )
        return (
            "Atualmente temos duas turmas disponíveis do HG360. Qual delas desperta "
            "mais o seu interesse?\n\n"
            "1️⃣ São Paulo – 28 a 30/08/2026\n"
            "2️⃣ Barcelona – 24 e 25/07/2026"
        )


# ---------------------------------------------------------------------------
# Helpers de NLU simples (sem LLM — heuristica de baixo custo)
# ---------------------------------------------------------------------------

def _detectar_confirmacao(texto: str) -> Optional[bool]:
    """
    Detecta confirmacao sim/nao. Retorna True / False / None (indeterminado).

    Matching por PALAVRA INTEIRA para negacao curta (evita o falso-negativo de
    'no'/'na' como contracao em PT — ex.: 'atendo NO Rio', 'registro NO CRM').
    Frases especificas tratadas por substring. Positivos genericos de profissao
    ('i am a') exigem contexto medico, para nao aprovar nao-medicos (FR-009).
    """
    t = _norm(texto)
    toks = set(t.split())

    palavras = t.split()
    # Respostas secas / negacao inicial (EN: "No, I'm a nurse")
    if t in {"nao", "no", "nope", "nah", "negativo", "jamais", "n"}:
        return False
    if palavras and palavras[0] in {"no", "nope", "nah"}:
        return False
    if t in {"sim", "yes", "s", "si", "claro", "isso", "ok", "positivo",
             "afirmativo", "exato", "correto", "confirmo"}:
        return True

    # Frases negativas (substring)
    neg_frases = [
        "nao sou", "nao tenho", "nao possuo", "nao soy", "no soy", "i am not",
        "i'm not", "im not", "not a doctor", "sem registro", "sem crm",
    ]
    if any(f in t for f in neg_frases):
        return False
    # Negacao por palavra inteira (PT 'nao' nunca e contracao; EN 'not')
    if {"nao", "not", "nunca", "nenhum", "jamais"} & toks:
        return False

    # Frases/positivos especificos (medico)
    pos_frases = [
        "sou medico", "sou medica", "soy medico", "soy medica", "tenho crm",
        "possuo crm", "registro ativo", "registro profissional", "medico com crm",
        "i am a doctor", "i'm a doctor", "im a doctor", "i am a physician",
        "i'm a physician",
    ]
    if any(f in t for f in pos_frases):
        return True
    if {"sim", "yes", "si", "claro", "isso", "exato", "correto",
        "afirmativo", "confirmo", "positivo"} & toks:
        return True
    return None


def _detectar_experiencia_corporal(texto: str) -> Optional[bool]:
    """Detecta experiencia em harmonizacao corporal. True / False / None.

    Matching por palavra inteira para negacao curta (evita 'no'/'na' contracao
    em PT, ex.: 'faco gluteo NO consultorio'). Frases por substring.
    """
    t = _norm(texto)
    toks = set(t.split())

    if t in {"nao", "no", "nope", "nah", "n"}:
        return False
    if t in {"sim", "yes", "si", "s"}:
        return True

    neg_frases = [
        "so facial", "apenas facial", "only facial", "solo facial", "somente facial",
        "nao tenho", "nao possuo", "nao atuo", "nao faco", "sem experiencia",
        "nunca atuei", "no tengo",
    ]
    if any(f in t for f in neg_frases):
        return False
    if {"nao", "not", "nunca"} & toks:
        return False

    pos_frases = [
        "ja atuo", "ja faco", "ja fiz", "tenho experiencia", "experiencia corporal",
        "harmonizacao corporal", "preenchimento de gluteo", "preenchimento gluteo",
        "gluteal", "corporal harmony", "faco gluteo", "atuo com",
    ]
    if any(f in t for f in pos_frases):
        return True
    if {"corporal", "gluteo", "gluteal"} & toks:
        return True
    if {"sim", "yes", "si"} & toks:
        return True
    return None


def _detectar_especialidade(texto: str) -> Optional[str]:
    """Detecta especialidade medica. Retorna string normalizada ou None."""
    t = _norm(texto)

    mapeamento = [
        (["dermatolog"], "dermatologia"),
        (["cirurgia plastica", "cirugia plastica", "plastic surgery", "plastica"],
         "cirurgia plastica"),
        (["cirurgia vascular", "vascular surgery", "vascular"], "cirurgia vascular"),
    ]
    for termos, especialidade in mapeamento:
        for termo in termos:
            if termo in t:
                return especialidade

    # Indicadores de "nao qualificante" — frases especificas (evita casar 'clinica'
    # ou 'geral' isolados dentro de respostas comuns como 'atendo na minha clinica').
    sem_especialidade = [
        "nao possuo", "sem especialidade", "nenhuma especialidade", "outra especialidade",
        "clinico geral", "clinica geral", "clinico-geral", "general practitioner",
        "no tengo especialidad", "no specialty", "other specialty",
    ]
    for termo in sem_especialidade:
        if termo in t:
            return "outra"
    toks = set(t.split())
    if {"outra", "nenhuma", "other"} & toks:
        return "outra"
    return None


def _detectar_escolha_turma(texto: str) -> Optional[str]:
    """Detecta escolha de turma HG360 (SP ou Barcelona). Retorna slug ou None."""
    t = _norm(texto)
    toks = set(t.replace("️⃣", " ").split())
    # Barcelona
    if "barcelona" in t or {"espanha", "spain", "espana"} & toks or \
            any(k in t for k in ["julho", "july", "julio"]):
        return _SLUG_HG360_BARCELONA
    # Sao Paulo ('sp' so como palavra inteira — evita casar dentro de 'esperar')
    if "sao paulo" in t or {"sp", "sampa"} & toks or \
            {"brasil", "brazil"} & toks or any(k in t for k in ["agosto", "august"]):
        return _SLUG_HG360_SP
    # Numeros do menu (palavras inteiras)
    if toks & {"1", "1.", "um", "one", "uno", "primeira", "primeiro"}:
        return _SLUG_HG360_SP
    if toks & {"2", "2.", "dois", "two", "dos", "segunda", "segundo"}:
        return _SLUG_HG360_BARCELONA
    return None


def _detectar_objetivo_sistema(texto: str) -> Optional[str]:
    """Caminho 3 / ETAPA 2 — objetivo: 'incorporar' | 'abrir' | 'nao_sei' | None."""
    t = _norm(texto)
    # Opcao 1 — incorporar a clinica atual (checada antes das demais: termos de
    # acao concreta tem prioridade sobre marcadores genericos de incerteza)
    if any(k in t for k in [
        "incorporar", "minha clinica", "clinica atual", "ja possuo",
        "ja tenho clinica", "licenciamento", "licenciar", "licensing",
        "incorporate",
    ]):
        return "incorporar"
    # Opcao 2 — abrir clinica completa
    if any(k in t for k in [
        "abrir", "nova clinica", "clinica completa", "clinica goldincision",
        "franquia", "franchise", "franquicia", "investir", "open clinic",
    ]):
        return "abrir"
    # Opcao 3 — incerteza ('duvida' generico REMOVIDO — interceptava opcao 1/2)
    if any(k in t for k in [
        "nao tenho certeza", "nao sei", "ainda nao", "incerto", "qual modelo",
        "not sure", "no estoy seguro", "ayuda a decidir",
    ]):
        return "nao_sei"
    # Numeros do menu (palavras inteiras)
    toks = set(t.replace("️⃣", " ").split())
    if toks & {"1", "1.", "um", "one", "uno"}:
        return "incorporar"
    if toks & {"2", "2.", "dois", "two", "dos"}:
        return "abrir"
    if toks & {"3", "3.", "tres", "three"}:
        return "nao_sei"
    return None


def _detectar_opcao_aluno(texto: str) -> Optional[str]:
    """Caminho 4 — submenu (6 opcoes). Retorna rotulo curto ou None."""
    t = _norm(texto)
    mapa = [
        (["1", "plataforma", "acesso", "acessar", "login", "platform"], "plataforma_acesso"),
        (["2", "certificado", "certificate", "conclusao", "diploma"], "certificado"),
        (["3", "suporte tecnico", "grupo", "tecnico", "support group"], "suporte_tecnico"),
        (["4", "pagamento", "inscricao", "boleto", "pix", "payment", "pago"], "pagamento"),
        (["5", "duvida", "duvidas", "sobre o curso", "conteudo", "question"], "duvidas_curso"),
        (["6", "outro", "outra", "other", "otro"], "outro"),
    ]
    toks = set(t.replace("️⃣", " ").split())
    for chaves, rotulo in mapa:
        for k in chaves:
            if k.isdigit():
                if k in toks:
                    return rotulo
            elif k in t:
                return rotulo
    return None


def _detectar_medico_investidor(texto: str) -> Optional[str]:
    """Caminho 3 / Franquia — 'medico' | 'investidor' | None (apenas observabilidade)."""
    t = _norm(texto)
    if any(k in t for k in ["investidor", "investid", "investor", "inversor", "investir"]):
        return "investidor"
    if any(k in t for k in ["medico", "médic", "doctor", "physician", "crm"]):
        return "medico"
    return None


def _eh_pergunta_informativa(texto: str) -> bool:
    """
    Heuristica: a mensagem e uma pergunta informativa direta (preco/conteudo/
    duracao/certificado/turma/data)? Usada para responder ANTES de qualificar
    (Mapa Mestre, REGRA do Caminho 1).
    """
    t = _norm(texto)
    chaves = [
        "preco", "valor", "quanto custa", "custa", "investimento", "preço",
        "conteudo", "conteúdo", "ementa", "programa", "modulo", "o que inclui",
        "duracao", "duração", "carga horaria", "quanto tempo", "quantas horas",
        "certificad", "diploma", "turma", "data", "quando", "onde", "local",
        "parcel", "desconto", "como funciona", "price", "cost", "how much",
        "duration", "certificate", "when", "where", "content", "precio",
        "cuanto", "cuánto", "duracion", "certificado",
    ]
    return any(k in t for k in chaves)


def _eh_pergunta(texto: str) -> bool:
    """Heuristica ampla: a mensagem parece uma pergunta/duvida?"""
    t = _norm(texto)
    if "?" in texto:
        return True
    if _eh_pergunta_informativa(texto):
        return True
    palavras = ["qual", "quais", "como", "quando", "onde", "quanto", "porque",
                "pode me", "gostaria de saber", "queria saber", "what", "how",
                "which", "tem ", "existe", "ha ", "há "]
    return any(t.startswith(p) or f" {p}" in f" {t}" for p in palavras)


def _detectar_fechamento(texto: str) -> Optional[str]:
    """
    Sinal forte de fechamento: 'aceita' (quer link/inscrever/consultor) ou
    'recusa' (nao quer agora). None = trata-se de duvida/continuacao.
    """
    t = _norm(texto)

    recusa = [
        "nao quero", "nao tenho interesse", "sem interesse", "agora nao",
        "deixa pra la", "depois eu", "mais tarde", "talvez depois", "no quiero",
        "no gracias", "no thanks", "not now", "not interested",
    ]
    for r in recusa:
        if r in t:
            return "recusa"

    aceita = [
        "quero o link", "manda o link", "mande o link", "envia o link",
        "enviar o link", "pode enviar o link", "me envia o link", "quero me inscrever",
        "quero inscrever", "fazer a inscricao", "fazer minha inscricao",
        "como faco a inscricao", "como me inscrevo", "como faco para me inscrever",
        "quero garantir", "pode encaminhar", "pode me encaminhar", "encaminhe",
        "encaminhar", "falar com consultor", "falar com um consultor", "com o consultor",
        "sim quero", "vamos fechar", "quero fechar", "send the link", "i want the link",
        "sign me up", "enroll me", "quiero el enlace", "quiero inscribirme",
        "envia el enlace", "hablar con un consultor",
    ]
    for a in aceita:
        if a in t:
            return "aceita"
    return None


def _sem_mais_duvidas(texto: str) -> bool:
    """Lead sinaliza que nao tem mais duvidas / esta pronto para avancar."""
    t = _norm(texto)
    cues = [
        "nao tenho duvida", "nao tenho mais duvida", "sem duvida", "sem mais duvida",
        "esta claro", "ficou claro", "entendi tudo", "era so isso", "era isso",
        "so isso", "nada mais", "tudo certo", "podemos seguir", "pode prosseguir",
        "pode marcar", "vamos marcar", "vamos agendar", "quero a reuniao",
        "no more questions", "that's all", "thats all", "eso es todo",
    ]
    return any(c in t for c in cues)


def _pede_humano(texto: str) -> bool:
    """Lead pede explicitamente atendimento humano (Regra 26)."""
    t = _norm(texto)
    cues = [
        "falar com um humano", "falar com humano", "falar com uma pessoa",
        "atendente", "falar com alguem", "atendimento humano",
        "quero um humano", "me passa pra alguem", "talk to a human",
        "speak to a human", "talk to someone", "real person", "human agent",
        "hablar con una persona", "hablar con un humano", "atencion humana",
    ]
    return any(c in t for c in cues)


def _norm(texto: str) -> str:
    """Normaliza para matching: minusculas, sem acentos, sem pontuacao, espacos
    colapsados. Remove vírgula/ponto etc. (ex.: 'nao, sou' → 'nao sou') mas
    preserva apóstrofo (necessario para "i'm not")."""
    if not texto:
        return ""
    t = texto.lower().strip()
    tabela = str.maketrans(
        "áàâãäéèêëíìîïóòôõöúùûüç", "aaaaaeeeeiiiiooooouuuuc",
    )
    t = t.translate(tabela)
    # Pontuacao → espaco (mantem apostrofo)
    for ch in ",.;:!?¿¡()[]{}\"":
        t = t.replace(ch, " ")
    return " ".join(t.split())
