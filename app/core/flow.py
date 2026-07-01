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

from app.config import settings
from app.core.intent import INTENCAO_PARA_CAMINHO, ClassificacaoIntencao, IntentClassifier
from app.core.interpret import SlotExtractor, permitir_reversao
from app.core.memory import MemoryManager, SessionContext
from app.core.responder import GroundedResponder, _fallback_indisponivel_response
from app.core.retrieval import HybridRetriever, ResultadoRecuperacao
from app.repository.models import (
    Curso,
    CursoApresentacao,
    CursoLink,
    CursoTurma,
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
# Slot schemas do fallback agentico (Pilar 8, FR-013..FR-018) — cobertura
# minima das 5 etapas (data-model.md §3). So descrevem o PROMPT passado a
# `SlotExtractor.extract()`; o formato de saida e sempre `SlotQualificacao`.
# ---------------------------------------------------------------------------
_SLOT_SCHEMA_EH_MEDICO = {
    "nome": "elegibilidade_medica",
    "descricao": (
        "Se o lead confirma ser medico com registro profissional (CRM) ativo. "
        "'sim' = e medico com registro ativo; 'nao' = nao e medico (ou nao tem "
        "registro ativo)."
    ),
    "valores_esperados": ["sim", "nao"],
}
_SLOT_SCHEMA_OBJETIVO_SISTEMA = {
    "nome": "objetivo_sistema",
    "descricao": (
        "O que o lead busca no Sistema GoldIncision: 'incorporar' (ja tem "
        "clinica e quer licenciar o sistema), 'abrir' (quer abrir uma clinica "
        "nova/franquia) ou 'nao_sei' (ainda incerto sobre qual modelo)."
    ),
    "valores_esperados": ["incorporar", "abrir", "nao_sei"],
}
_SLOT_SCHEMA_EXPERIENCIA_CORPORAL = {
    "nome": "experiencia_corporal",
    "descricao": (
        "Se o lead ja tem experiencia previa em Harmonizacao Corporal (gluteo/"
        "preenchimento corporal) — nao apenas facial. 'sim' ou 'nao'."
    ),
    "valores_esperados": ["sim", "nao"],
}
_SLOT_SCHEMA_ESPECIALIDADE = {
    "nome": "especialidade",
    "descricao": (
        "Especialidade medica do lead, quando relevante para elegibilidade ao "
        "HG360 (dermatologia, cirurgia plastica, cirurgia vascular). Se o lead "
        "nao tiver nenhuma dessas ou disser que nao possui especialidade, use "
        "'outra'."
    ),
    "valores_esperados": ["dermatologia", "cirurgia plastica", "cirurgia vascular", "outra"],
}
_SLOT_SCHEMA_ESCOLHA_TURMA = {
    "nome": "escolha_turma",
    "descricao": (
        "Turma do HG360 escolhida pelo lead: 'sp' (Sao Paulo/Brasil) ou "
        "'barcelona' (Espanha/Julho)."
    ),
    "valores_esperados": ["sp", "barcelona"],
}

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

# Destino LOGICO de handoff por CAMINHO ativo, usado pelo orcamento de turnos
# (US1, FR-004/FR-020) quando o teto de turnos-de-sessao e atingido — sempre
# resolvido a partir desta allowlist estatica, NUNCA decidido pelo LLM
# (SEC-LLM-3). Fallback generico (DEST_CONSULTORES) para caminho
# desconhecido/None (Edge Case: sessao ainda sem caminho definido).
_DESTINO_POR_CAMINHO: dict[int, str] = {
    CaminhoMapaMestre.CURSO_ONLINE_HG: DEST_CONSULTORES,
    CaminhoMapaMestre.CURSOS_PRESENCIAIS: DEST_PRESENCIAL,
    CaminhoMapaMestre.SISTEMA_GOLDINCISION: DEST_ESPECIALISTA,
    CaminhoMapaMestre.ALUNO_SUPORTE: DEST_SUPORTE,
    CaminhoMapaMestre.PACIENTE_MODELO: DEST_CONSULTORES,
    CaminhoMapaMestre.OUTRO_ASSUNTO: DEST_CONSULTORES,
}


def _destino_logico_por_caminho(caminho: Optional[int]) -> str:
    """
    Destino logico de handoff por caminho ativo (US1, FR-004).

    Sempre resolvido pela allowlist estatica `_DESTINO_POR_CAMINHO` — nunca
    decidido pelo LLM (SEC-LLM-3). Caminho None/desconhecido cai no fallback
    generico DEST_CONSULTORES.
    """
    if caminho is None:
        return DEST_CONSULTORES
    return _DESTINO_POR_CAMINHO.get(caminho, DEST_CONSULTORES)


# ---------------------------------------------------------------------------
# i18n — textos deterministicos (anti-alucinacao: nunca passam pelo LLM)
# ---------------------------------------------------------------------------
_T: dict[str, dict[str, str]] = {
    "invite_duvidas": {
        "pt": "Fique à vontade para esclarecer qualquer dúvida sobre o treinamento. 😊",
        "en": "Feel free to ask any questions about the training. 😊",
        "es": "Quedo a tu disposición para cualquier duda sobre la formación. 😊",
    },
    # Confirmacao curta quando, no convite de overflow, o lead pede especialista.
    "overflow_especialista": {
        "pt": (
            "Perfeito! Vou te conectar com um de nossos especialistas, que "
            "apresenta tudo em detalhe e tira suas dúvidas pessoalmente. 🙏"
        ),
        "en": (
            "Perfect! I'll connect you with one of our specialists, who will walk "
            "you through everything and answer your questions personally. 🙏"
        ),
        "es": (
            "¡Perfecto! Voy a conectarte con uno de nuestros especialistas, que te "
            "lo presenta todo en detalle y resuelve tus dudas personalmente. 🙏"
        ),
    },
    # Reconhecimento curto para "glue" conversacional (saudacao/agradecimento/
    # afirmacao pura) num no de DUVIDAS — evita mandar isso ao RAG e cair em
    # abstencao/handoff ("engessado"). Nao passa pelo LLM (anti-alucinacao).
    "glue_ack": {
        "pt": "Claro! 😊 Fique à vontade para tirar qualquer dúvida sobre o treinamento.",
        "en": "Of course! 😊 Feel free to ask any questions about the training.",
        "es": "¡Claro! 😊 Quedo a tu disposición para cualquier duda sobre la formación.",
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
        "pt": "Ótimo! Aqui está o link para realizar a sua inscrição:",
        "en": "Great! Here is the link to complete your registration:",
        "es": "¡Genial! Aquí está el enlace para realizar tu inscripción:",
    },
    "link_pos": {
        "pt": "Qualquer dúvida no processo, é só me chamar. 😊",
        "en": "If you have any questions during the process, just let me know. 😊",
        "es": "Cualquier duda en el proceso, solo avísame. 😊",
    },
    "consultor_handoff": {
        "pt": (
            "Combinado! Vou encaminhar o seu interesse para um de nossos consultores, "
            "que dará continuidade à sua inscrição. Em breve entrarão em contato. 😊"
        ),
        "en": (
            "All set! I'll forward your interest to one of our consultants, who will "
            "continue your registration. They'll be in touch soon. 😊"
        ),
        "es": (
            "¡Listo! Voy a dirigir tu interés a uno de nuestros consultores, que "
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
            "Excelente! O modelo de Franquia GoldIncision é apresentado em detalhes "
            "por um de nossos especialistas, que cuidará disso pessoalmente com você. "
            "Vou encaminhar o seu interesse para que agendem uma reunião. 😊"
        ),
        "en": (
            "Excellent! The GoldIncision Franchise model is presented in detail by one "
            "of our specialists, who will handle this with you personally. I'll "
            "forward your interest so they can schedule a meeting. 😊"
        ),
        "es": (
            "¡Excelente! El modelo de Franquicia GoldIncision lo presenta en detalle "
            "uno de nuestros especialistas, que lo atenderá personalmente contigo. "
            "Voy a dirigir tu interés para que agenden una reunión. 😊"
        ),
    },
    # Resumo curto do Licenciamento (C3): o objetivo e qualificar e conduzir a uma
    # reuniao com especialista, NUNCA vender nem despejar a apresentacao inteira.
    "sistema_lic_resumo": {
        "pt": (
            "O Licenciamento Internacional GoldIncision é um programa exclusivo para "
            "médicos que querem levar o método para a sua região com todo o suporte da "
            "nossa estrutura. Os detalhes completos — condições, formato e próximos "
            "passos — são apresentados por um especialista em uma conversa dedicada. "
            "Posso esclarecer suas dúvidas iniciais por aqui; o que gostaria de saber "
            "primeiro? 😊"
        ),
        "en": (
            "GoldIncision International Licensing is an exclusive program for physicians "
            "who want to bring the method to their region with the full support of our "
            "structure. The complete details — terms, format and next steps — are "
            "presented by a specialist in a dedicated conversation. I can clear up your "
            "initial questions here; what would you like to know first? 😊"
        ),
        "es": (
            "El Licenciamiento Internacional GoldIncision es un programa exclusivo para "
            "médicos que desean llevar el método a su región con todo el soporte de "
            "nuestra estructura. Los detalles completos — condiciones, formato y "
            "próximos pasos — los presenta un especialista en una conversación dedicada. "
            "Puedo aclarar tus dudas iniciales por aquí; ¿qué te gustaría saber primero? 😊"
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
            "Vamos entender juntos qual modelo faz mais sentido para você. "
            "Me conte um pouco:\n"
            "• Você pretende usar uma clínica que já possui ou abrir uma nova unidade?\n"
            "• O projeto seria no Brasil ou no exterior?\n"
            "• Você é médico ou investidor?"
        ),
        "en": (
            "Let's figure out together which model fits you best. Tell me a "
            "bit:\n"
            "• Do you intend to use a clinic you already own or open a new unit?\n"
            "• Would the project be in Brazil or abroad?\n"
            "• Are you a physician or an investor?"
        ),
        "es": (
            "Entendamos juntos qué modelo tiene más sentido para ti. "
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
            "Pode deixar! Vou encaminhar sua solicitação para nossa equipe responsável, "
            "que dará continuidade ao seu atendimento.\n"
            "Caso seja necessário, nossa equipe poderá entrar em contato para "
            "solicitar informações complementares ou lhe chamar de outro número de "
            "WhatsApp. 😊"
        ),
        "en": (
            "Will do! I'll forward your request to our responsible team, who will "
            "continue your service.\n"
            "If necessary, our team may contact you to request additional information "
            "or reach you from another WhatsApp number. 😊"
        ),
        "es": (
            "¡Hecho! Voy a dirigir tu solicitud a nuestro equipo responsable, que "
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
    # Perguntas de qualificacao (texto fixo — anti-alucinacao)
    "pergunta_medico": {
        "pt": (
            "Ótimo! Antes de prosseguirmos, preciso confirmar uma informação: você é "
            "médico com registro profissional ativo em seu país? 🩺"
        ),
        "en": (
            "Great! Before we proceed, I need to confirm one detail: are you a "
            "physician with an active professional registration in your country? 🩺"
        ),
        "es": (
            "¡Genial! Antes de continuar, necesito confirmar un detalle: ¿eres "
            "médico con registro profesional activo en tu país? 🩺"
        ),
    },
    # Qualificacao medica fiel ao Mapa Mestre (texto especifico por caminho)
    "qualif_medico_c1": {
        "pt": (
            "Perfeito! O Curso Online de Harmonização Glútea é uma formação exclusiva "
            "para médicos. 🩺\nAntes de prosseguirmos, preciso confirmar uma informação: "
            "você é médico com registro profissional ativo em seu país?"
        ),
        "en": (
            "Perfect! The Online Gluteal Harmonization course is a training exclusive "
            "to physicians. 🩺\nBefore we proceed, I need to confirm one detail: are you "
            "a physician with an active professional registration in your country?"
        ),
        "es": (
            "¡Perfecto! El Curso Online de Armonización Glútea es una formación "
            "exclusiva para médicos. 🩺\nAntes de continuar, necesito confirmar un dato: "
            "¿eres médico con registro profesional activo en tu país?"
        ),
    },
    "qualif_medico_c2": {
        "pt": (
            "Perfeito! Os Cursos Presenciais de Harmonização Glútea são exclusivos "
            "para médicos. 🩺\nAntes de prosseguirmos, preciso confirmar uma informação: "
            "você é médico com registro profissional ativo em seu país?"
        ),
        "en": (
            "Perfect! The in-person Gluteal Harmonization courses are exclusive to "
            "physicians. 🩺\nBefore we proceed, I need to confirm one detail: are you a "
            "physician with an active professional registration in your country?"
        ),
        "es": (
            "¡Perfecto! Los Cursos Presenciales de Armonización Glútea son exclusivos "
            "para médicos. 🩺\nAntes de continuar, necesito confirmar un dato: ¿eres "
            "médico con registro profesional activo en tu país?"
        ),
    },
    "qualif_medico_lic": {
        "pt": (
            "O Licenciamento GoldIncision é um programa exclusivo para médicos. 🩺\n"
            "Você é médico com registro profissional ativo em seu país?"
        ),
        "en": (
            "GoldIncision Licensing is a program exclusive to physicians. 🩺\n"
            "Are you a physician with an active professional registration in your country?"
        ),
        "es": (
            "El Licenciamiento GoldIncision es un programa exclusivo para médicos. 🩺\n"
            "¿Eres médico con registro profesional activo en tu país?"
        ),
    },
    "pergunta_experiencia": {
        "pt": (
            "Para indicar a formação mais adequada ao seu momento profissional: você "
            "já atua com Harmonização Corporal ou preenchimento de glúteo? "
            "(experiência apenas facial não conta) 💉"
        ),
        "en": (
            "To indicate the most suitable training for your moment: do you already "
            "work with Corporal Harmonization or gluteal fillers? "
            "(facial experience alone does not count) 💉"
        ),
        "es": (
            "Para indicarte la formación más adecuada a tu momento: ¿ya trabajas con "
            "Armonización Corporal o rellenos glúteos? (la experiencia solo facial "
            "no cuenta) 💉"
        ),
    },
    "pergunta_especialidade": {
        "pt": (
            "Para indicar a formação mais adequada ao seu perfil, poderia me informar "
            "sua especialidade médica?\n"
            "• Dermatologia\n• Cirurgia Plástica\n• Cirurgia Vascular\n"
            "• Outra especialidade\n• Não possuo especialidade"
        ),
        "en": (
            "To direct you to the most suitable course, could you tell me your "
            "medical specialty?\n"
            "• Dermatology\n• Plastic Surgery\n• Vascular Surgery\n"
            "• Other specialty\n• I don't have a specialty"
        ),
        "es": (
            "Para dirigirte al curso más adecuado, ¿podrías decirme tu especialidad "
            "médica?\n"
            "• Dermatología\n• Cirugía Plástica\n• Cirugía Vascular\n"
            "• Otra especialidad\n• No tengo especialidad"
        ),
    },
    "pergunta_turma": {
        "pt": (
            "Atualmente temos duas turmas disponíveis do HG360. Qual delas desperta "
            "mais o seu interesse?\n\n"
            "1️⃣ São Paulo – 28 a 30/08/2026\n"
            "2️⃣ Barcelona – 24 e 25/07/2026"
        ),
        "en": (
            "We currently have two HG360 sessions available. Which one interests you "
            "most?\n\n"
            "1️⃣ São Paulo – August 28-30, 2026\n"
            "2️⃣ Barcelona – July 24-25, 2026"
        ),
        "es": (
            "Actualmente tenemos dos grupos del HG360 disponibles. ¿Cuál te interesa "
            "más?\n\n"
            "1️⃣ São Paulo – 28 a 30/08/2026\n"
            "2️⃣ Barcelona – 24 y 25/07/2026"
        ),
    },
    "trilha_conector": {
        "pt": "👉 E, como trilha de evolução recomendada, veja também o HG360:",
        "en": "👉 And, as the recommended learning path, here is the HG360 too:",
        "es": "👉 Y, como ruta de evolución recomendada, mira también el HG360:",
    },
    # Orcamento de turnos (US1, FASE 3) — nudge de no: reforca a oferta de
    # conexao com especialista SEM encerrar a conversa (FR-003).
    "turnos_no_no_nudge": {
        "pt": (
            "A propósito, se preferir agilizar, posso conectar você agora mesmo "
            "com um de nossos especialistas — mas fico à disposição para "
            "continuar por aqui também. 😊"
        ),
        "en": (
            "By the way, if you'd like to speed things up, I can connect you "
            "with one of our specialists right now — but I'm happy to keep "
            "going here too. 😊"
        ),
        "es": (
            "Por cierto, si prefieres agilizar, puedo conectarte ahora mismo "
            "con uno de nuestros especialistas — pero quedo a tu disposición "
            "para continuar por aquí también. 😊"
        ),
    },
    # Orcamento de turnos (US1, FASE 3) — handoff cordial por teto de sessao
    # (FR-004), distinto do "desistir_handoff" do anti-loop por etapa.
    "turnos_sessao_handoff": {
        "pt": (
            "Para dar a você um atendimento ainda mais completo, vou conectar "
            "você agora com um de nossos especialistas, que continua "
            "pessoalmente. 🙏"
        ),
        "en": (
            "To give you an even more complete experience, I'll connect you "
            "now with one of our specialists, who will continue personally. 🙏"
        ),
        "es": (
            "Para darte una atención aún más completa, voy a conectarte ahora "
            "con uno de nuestros especialistas, que continuará "
            "personalmente. 🙏"
        ),
    },
    # Timeout de inatividade e reengajamento (US2, FASE 5, FR-009) —
    # retomada cordial reconhecendo a pausa, SEM reiniciar a jornada (etapa
    # e caminho permanecem intactos; este texto e apenas prefixado a
    # resposta normal do turno).
    "retomada_cordial": {
        "pt": (
            "Oi de novo! 😊 Notei que faz um tempinho desde sua última "
            "mensagem — vamos continuar de onde paramos."
        ),
        "en": (
            "Hi again! 😊 I noticed it's been a little while since your last "
            "message — let's pick up right where we left off."
        ),
        "es": (
            "¡Hola de nuevo! 😊 Noté que pasó un tiempito desde tu último "
            "mensaje — sigamos donde lo dejamos."
        ),
    },
}


def _t(key: str, idioma: str) -> str:
    """Retorna o texto deterministico no idioma (fallback PT)."""
    bloco = _T.get(key, {})
    return bloco.get(idioma) or bloco.get("pt", "")


# Aberturas curtas e VARIADAS por idioma (evita repetir "Perfeito" a cada resposta).
_ACKS = {
    "pt": ["Perfeito", "Ótimo", "Que bom", "Combinado", "Maravilha", "Excelente"],
    "en": ["Perfect", "Great", "Wonderful", "Got it", "Excellent", "Sounds good"],
    "es": ["Perfecto", "Genial", "Estupendo", "De acuerdo", "Maravilloso", "Excelente"],
}


def _saudacao(context: SessionContext) -> str:
    """
    Abertura curta, VARIADA e no idioma do lead (humanizacao sem repeticao).

    Alterna o termo a cada turno (indice derivado do nº de mensagens ja trocadas),
    para nao iniciar toda resposta com a mesma palavra. Inclui o nome quando
    disponivel. Idioma-aware (antes retornava sempre PT, ate em jornadas EN/ES).
    """
    pool = _ACKS.get(context.idioma, _ACKS["pt"])
    idx = len(context.historico_recente or []) % len(pool)
    ack = pool[idx]
    nome = (context.nome or "").strip()
    if nome:
        return f"{ack}, {nome.split()[0]}!"
    return f"{ack}!"


def _perfil_conhecido(context: SessionContext) -> str:
    """
    Bloco compacto com os fatos JA CONHECIDOS do lead (qualificacao duravel do
    Contato), para o LLM personalizar e NAO re-perguntar (anti-redundancia).

    Retorna "" quando nada e conhecido. O idioma ja e passado a parte ao responder,
    por isso nao e repetido aqui.
    """
    fatos: list[str] = []
    if (context.nome or "").strip():
        fatos.append(f"- Nome: {context.nome.strip()}")
    if context.eh_medico is True:
        fatos.append("- Ja confirmou que e medico — NAO pergunte de novo se e medico.")
    elif context.eh_medico is False:
        fatos.append("- Ja informou que NAO e medico.")
    if context.especialidade:
        fatos.append(f"- Especialidade: {context.especialidade}")
    if context.experiencia_corporal is True:
        fatos.append("- Tem experiencia em Harmonizacao Corporal.")
    elif context.experiencia_corporal is False:
        fatos.append("- Sem experiencia em Harmonizacao Corporal.")
    if context.produto_interesse:
        fatos.append(f"- Interesse/curso: {context.produto_interesse}")
    # Perfil livre/incremental (caracteristicas e preferencias arbitrarias).
    for chave, valor in (context.perfil or {}).items():
        if valor is None or valor == "":
            continue
        rotulo = str(chave).replace("_", " ").capitalize()
        fatos.append(f"- {rotulo}: {valor}")
    if not fatos:
        return ""
    return (
        "=== FATOS JA CONHECIDOS DO LEAD (use para personalizar; NAO pergunte "
        "novamente o que ja esta aqui) ===\n" + "\n".join(fatos)
    )


def _merge_perfil(context: SessionContext, updates: dict, novos_fatos: dict) -> None:
    """
    Acumula caracteristicas/preferencias livres no perfil do lead (anti-redundancia).

    Mescla `novos_fatos` em `context.perfil` (in-memory) e propaga o dict COMPLETO
    em `updates["perfil"]` para persistencia (Contato.perfil — JSONB e gravado
    inteiro). Valores None/"" sao ignorados; nao apaga fatos ja conhecidos.
    """
    limpos = {k: v for k, v in (novos_fatos or {}).items() if v is not None and v != ""}
    if not limpos:
        return
    perfil = dict(context.perfil or {})
    perfil.update(limpos)
    context.perfil = perfil
    updates["perfil"] = perfil


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
        turno_acao: Optional[str] = None,
        motivo: Optional[str] = None,
        confianca_slot: Optional[float] = None,
        fidelidade_fiel: Optional[bool] = None,
        fidelidade_afirmacoes_nao_sustentadas: Optional[list] = None,
        fonte_ids: Optional[list] = None,
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
        # Orcamento de turnos (US1, FASE 3, FR-006): sinaliza para o chamador
        # (webhook.py) que `acao` do evento de observabilidade deve ser
        # "nudge" (result.action permanece "continue" — nudge NAO e handoff).
        # `motivo` ∈ {"turnos_no_no", "turnos_sessao"} quando aplicavel;
        # distinto de `handoff_motivo` (motivo de negocio do handoff, ex.:
        # "pedido_humano", usado tambem para persistencia em Ticket).
        self.turno_acao = turno_acao
        self.motivo = motivo
        # Observabilidade aditiva (FASE 4, task 4.3 — sdr-fidelidade-json):
        # atribuidos POS-HOC por `FlowEngine.process()` (nao pelos handlers
        # que constroem FlowResult), a partir do estado do turno corrente —
        # ver `process()`. None quando o mecanismo correspondente nao foi
        # acionado neste turno (fast-path deterministico / sem condicao
        # comercial / verbatim).
        self.confianca_slot = confianca_slot
        self.fidelidade_fiel = fidelidade_fiel
        self.fidelidade_afirmacoes_nao_sustentadas = fidelidade_afirmacoes_nao_sustentadas
        # Rastreabilidade aditiva (FASE 5, Onda 3 — FR-011, FR-018): ids dos
        # chunks (`HybridRetriever`) que embasaram a resposta deste turno,
        # atribuido POS-HOC por `FlowEngine.process()` a partir de
        # `GroundedResponder.last_fonte_ids`. None quando RAG nao foi
        # acionado neste turno (fast-path/verbatim/sem duvida).
        self.fonte_ids = fonte_ids


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
        slot_extractor: Optional[SlotExtractor] = None,
        retriever: Optional[HybridRetriever] = None,
    ) -> None:
        self._db = db_session
        self._intent = intent_classifier
        self._memory = memory_manager
        self._responder = responder
        self._nidia_phone = nidia_phone
        # RAG hibrido (Onda 3, FASE 5 — FR-001..FR-006, FR-011..FR-018).
        # Opcional (default None = retrocompat com testes legados que nao o
        # injetam, mesmo padrao de `slot_extractor`/`fidelity_gate`): quando
        # None, `_load_knowledge_by_slug` NAO tenta recuperacao (sem RAG,
        # sem abstencao) — producao (`app/api/webhook.py`) SEMPRE injeta um.
        self._retriever = retriever
        # Pilar 8 (FR-013..FR-018): fallback agentico, opcional (default None =
        # desativado, retrocompat com testes legados que nao o injetam — mesmo
        # padrao de `fidelity_gate` em GroundedResponder). Quando None, os
        # resolvers de slot caem direto no fast-path/"nao entendido".
        self._slot_extractor = slot_extractor
        # Observabilidade aditiva (FASE 4, task 4.3): confianca do ULTIMO
        # fallback agentico de slot-filling acionado no turno corrente.
        # Resetado a cada `process()` (evita vazamento entre turnos) e
        # setado pelos resolvers (`_resolver_slot_booleano` e demais) SOMENTE
        # quando o `SlotExtractor` de fato roda (fast-path resolvido nao
        # define confianca — nao ha "confianca do fallback" a reportar).
        self._last_confianca_slot: Optional[float] = None

    async def process(
        self, ticket_id: int, user_message: str, context: SessionContext
    ) -> FlowResult:
        """
        Processa a mensagem do lead e retorna FlowResult (resposta + acao + updates).

        Ponto de entrada UNICO do motor (US1/US2, FASE 3/FASE 5):
        1. Deteccao LAZY de inatividade (US2, FR-009/FR-010) — ANTES da
           maquina de estados, pois "sessao nova" precisa resetar
           caminho/etapa antes que `_process_core` decida o roteamento.
        2. Delega a maquina de estados a `_process_core`.
        3. Aplica a retomada cordial (prefixo de texto, sem alterar
           etapa/caminho) quando o gap e moderado.
        4. Aplica o orcamento de turnos (nudge/handoff por teto de no ou de
           sessao — FR-003 a FR-006) sobre o resultado, usando os
           contadores `context.turnos_sessao`/`context.turnos_no_no` ja
           incrementados 1x por turno pelo chamador (webhook.py, ANTES
           desta chamada — FR-002). Precedencia: orcamento de turnos
           sobrescreve `turno_acao`/`response_text` de reengajamento se
           ambos disparam no mesmo turno (escalonamento de negocio e mais
           urgente que uma mensagem de boas-vindas).
        """
        # Observabilidade aditiva (task 4.3): resetar ANTES do turno para
        # nunca vazar o veredito/confianca de um turno anterior quando o
        # mecanismo correspondente nao e acionado no turno corrente.
        self._last_confianca_slot = None
        if self._responder is not None:
            self._responder.last_fidelidade_fiel = None
            self._responder.last_fidelidade_afirmacoes_nao_sustentadas = None
            self._responder.last_fonte_ids = None

        estado_inatividade = self._aplicar_reengajamento_pre(context)
        # Retomada de overflow (anti-rajada) ANTES do roteamento normal: se ha
        # blocos bufferizados e o lead respondeu ao convite "continuar ou
        # especialista?", tratamos aqui (continuar = devolve o restante verbatim;
        # especialista = handoff). Pulado quando a sessao expirou (buffer stale).
        overflow_result: Optional[FlowResult] = None
        if estado_inatividade != "sessao_nova":
            overflow_result = await self._aplicar_overflow_resume(context, user_message)
        if overflow_result is not None:
            result = overflow_result
        else:
            result = await self._process_core(ticket_id, user_message, context)
            result = self._aplicar_reengajamento_pos(context, result, estado_inatividade)
            result = self._aplicar_orcamento_turnos(context, result)

        # Anexar observabilidade aditiva ao FlowResult final (pos-hoc — task
        # 4.3): nunca altera decisao de fluxo, apenas repassa o que os
        # mecanismos de Pilar 7/Pilar 8 registraram neste turno (ou None,
        # se nenhum foi acionado). `self._responder` pode ser None em
        # cenarios de teste legados que nao o injetam.
        result.confianca_slot = self._last_confianca_slot
        if self._responder is not None:
            result.fidelidade_fiel = self._responder.last_fidelidade_fiel
            result.fidelidade_afirmacoes_nao_sustentadas = (
                self._responder.last_fidelidade_afirmacoes_nao_sustentadas
            )
            result.fonte_ids = self._responder.last_fonte_ids
        return result

    # ------------------------------------------------------------------
    # Retomada de overflow de turno (anti-rajada) — "continuar ou especialista?"
    # ------------------------------------------------------------------

    async def _aplicar_overflow_resume(
        self, context: SessionContext, user_message: str
    ) -> Optional[FlowResult]:
        """
        Retoma o overflow de turno: se ha blocos bufferizados (o lead recebeu o
        convite "continuar explicando o restante ou especialista?"), interpreta a
        resposta:
          - continuacao afirmativa -> devolve o RESTANTE (verbatim, sem LLM/RAG),
            nunca cai em abstencao/handoff (era a causa do "engessado");
          - pedido de especialista -> handoff ao destino LOGICO do caminho atual
            (allowlist/config, SEC-LLM-3; nunca do LLM);
          - "outro" (mensagem/pergunta nova real) -> limpa o buffer e retorna None
            (o processamento normal segue).
        Fast-path deterministico primeiro (ZERO LLM); fallback agentico via
        SlotExtractor so quando o fast-path nao resolve.
        """
        blocos = context.overflow_blocos or []
        if not blocos:
            return None
        idioma = context.overflow_idioma or context.idioma or "pt"

        intent = _classificar_overflow_fastpath(user_message)
        if intent is None and self._slot_extractor is not None:
            intent = await self._classificar_overflow_llm(context, user_message)

        if intent == "continuar":
            # Consome o buffer local; o webhook re-bufferiza o remainder do envio
            # (se ainda exceder o teto por turno, o proximo turno retoma de novo).
            context.overflow_blocos = []
            texto = "\n\n".join(b for b in blocos if b)
            return FlowResult(texto, "continue", context.caminho, context.etapa, {})

        if intent == "especialista":
            context.overflow_blocos = []
            destino = _destino_logico_por_caminho(context.caminho)
            texto = _t("overflow_especialista", idioma)
            return FlowResult(
                texto, "handoff", context.caminho, context.etapa, {},
                handoff_destino=destino, handoff_motivo="pedido_humano",
            )

        # "outro" / indeterminado: abandona o overflow e processa normalmente
        # (uma pergunta nova genuina ainda precisa funcionar).
        context.overflow_blocos = []
        return None

    async def _classificar_overflow_llm(
        self, context: SessionContext, user_message: str
    ) -> Optional[str]:
        """
        Fallback agentico (gpt-4o-mini) para classificar a resposta ao convite de
        overflow em {continuar|especialista|outro} quando o fast-path nao resolve.
        Baixa confianca (< limiar) -> None (tratado como "outro"). Fail-safe: o
        proprio SlotExtractor nunca propaga excecao.
        """
        slot_schema = {
            "nome": "resposta_overflow",
            "descricao": (
                "O agente ofereceu CONTINUAR explicando o restante do conteudo OU "
                "conectar com um especialista. Classifique a intencao do lead."
            ),
            "valores_esperados": ["continuar", "especialista", "outro"],
        }
        slot = await self._slot_extractor.extract(
            slot_schema, user_message, _perfil_conhecido(context),
        )
        self._last_confianca_slot = slot.confianca  # observabilidade aditiva
        if not SlotExtractor.aceitar(slot, settings.slot_confidence_threshold):
            return None
        valor = _norm(slot.valor or "")
        if valor in {"continuar", "continue", "seguir", "sim"}:
            return "continuar"
        if valor in {"especialista", "humano", "consultor", "atendente"}:
            return "especialista"
        return None

    # ------------------------------------------------------------------
    # Timeout de inatividade e reengajamento (US2, FASE 5 — FR-008 a FR-010)
    # ------------------------------------------------------------------

    def _aplicar_reengajamento_pre(self, context: SessionContext) -> str:
        """
        Deteccao lazy de inatividade, executada ANTES da maquina de estados
        (task 5.2.1/5.3.1).

        `context.horas_inatividade` e calculado pelo CALLER (webhook.py
        `_handle_engine`, via `_bump_ultima_interacao` — HGET do valor
        anterior + HSET do novo timestamp, fail-open) — mesmo padrao dos
        contadores de orcamento de turnos (US1). `None` significa: primeiro
        turno da sessao OU leitura ausente/corrompida do Redis — tratado
        como interacao recente (fail-open — task 5.1.2/5.2.4), ou seja,
        NENHUMA retomada/expiracao e disparada.

        Retorna: "normal" | "retomada" | "sessao_nova".

        Efeito colateral (SOMENTE no caso "sessao_nova", task 5.3.1): reseta
        `context.caminho`/`context.etapa` para None, fazendo `_process_core`
        tratar o turno como uma sessao nova (retorna a saudacao/menu
        inicial — mesmo caminho de codigo de um ticket genuinamente novo) e
        limpa o contador anti-loop por etapa (`etapa_funil`). Os dados de
        qualificacao do Contato (`eh_medico`, `idioma`, `especialidade`,
        `experiencia_corporal`, `produto_interesse`, `perfil`, `nome`) NAO
        sao tocados aqui — permanecem no `context` e sao usados por
        `_perfil_conhecido`/prompts para nao re-perguntar nada ja capturado
        (task 5.3.2, CHK005; SC-004).
        """
        horas = context.horas_inatividade
        if horas is None:
            return "normal"

        if horas > settings.expira_sessao_horas:
            logger.info(
                "flow: sessao expirada (gap=%.1fh > %sh) — tratando como "
                "sessao nova, perfil preservado ticket_id=%s",
                horas, settings.expira_sessao_horas, context.ticket_id,
            )
            context.caminho = None
            context.etapa = None
            context.etapa_funil = None
            return "sessao_nova"

        if horas > settings.reengajamento_horas:
            logger.info(
                "flow: gap de reengajamento (gap=%.1fh > %sh) — retomada "
                "cordial ticket_id=%s",
                horas, settings.reengajamento_horas, context.ticket_id,
            )
            return "retomada"

        return "normal"

    def _aplicar_reengajamento_pos(
        self, context: SessionContext, result: FlowResult, estado_inatividade: str,
    ) -> FlowResult:
        """
        Aplica o efeito de reengajamento SOBRE o resultado ja computado por
        `_process_core` (task 5.2.1/5.2.2).

        - "retomada": prefixa a mensagem cordial ANTES da resposta normal do
          turno (etapa/caminho ja permaneceram intactos — `_process_core`
          nunca foi informado do gap, entao uma pergunta/menu pendente
          continua valido e NAO e reapresentado do zero — task 5.2.2).
          Marca `turno_acao="retomada"` para observabilidade (US5).
        - "sessao_nova": nao altera o texto (a saudacao/menu inicial ja
          vem de `_process_core`, que rodou com caminho/etapa resetados por
          `_aplicar_reengajamento_pre`); apenas marca
          `turno_acao="sessao_nova"` para observabilidade.
        - "normal": nao faz nada.
        - Nunca aplica sobre um resultado que ja e handoff (pedido explicito
          de humano tem precedencia sobre uma mensagem de boas-vindas).
        """
        if estado_inatividade == "normal" or result.action == "handoff":
            return result

        if estado_inatividade == "retomada":
            retomada_txt = _t("retomada_cordial", context.idioma)
            result.response_text = (
                retomada_txt + "\n\n" + (result.response_text or "")
            ).strip()
            result.turno_acao = "retomada"
        elif estado_inatividade == "sessao_nova":
            result.turno_acao = "sessao_nova"

        return result

    async def _process_core(
        self, ticket_id: int, user_message: str, context: SessionContext
    ) -> FlowResult:
        """Maquina de estados do Mapa Mestre (inalterada) — ver `process` para o
        orcamento de turnos aplicado ao resultado final."""
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

        # Expor a intencao classificada para observabilidade de turno
        # (US5/FR-015 — webhook.py le context.ultima_intencao apos process()).
        context.ultima_intencao = intencao.value

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

        # 2.bis Menu — escolha da opcao de forma DETERMINISTICA (numero ou palavra:
        #    "3", "3️⃣", "tres") tem prioridade sobre o classificador LLM. Um numero
        #    seco no menu nao deve depender da intencao (que o rebaixa a "ambigua" e
        #    prende o lead no menu). Resolve o relato: digitar "3" nao entrava no C3.
        if context.caminho is None and context.etapa == ETAPA_MENU:
            opcao = _opcao_numerica(user_message, 6)
            if opcao is not None:
                logger.info(
                    "flow: opcao de menu detectada=%s ticket_id=%s", opcao, ticket_id
                )
                context.caminho = opcao
                context.etapa = None
                _tent_clear(context, updates)
                updates["caminho_atual"] = opcao
                updates["etapa_mapa_mestre"] = None
                return await self._despachar_caminho(
                    context, user_message, updates, opcao
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
        return await self._despachar_caminho(
            context, user_message, updates, caminho_ativo
        )

    async def _despachar_caminho(
        self, context: SessionContext, user_message: str, updates: dict,
        caminho_ativo: Optional[int],
    ) -> FlowResult:
        """Despacha para o handler do caminho ativo (pode vir do contexto ou da intencao)."""
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
    # Orcamento de turnos (US1, FASE 3 — FR-001 a FR-007)
    # ------------------------------------------------------------------

    def _aplicar_orcamento_turnos(
        self, context: SessionContext, result: FlowResult,
    ) -> FlowResult:
        """
        Aplica o escalonamento de orcamento de turnos sobre o resultado ja
        computado pela maquina de estados (`_process_core`).

        Contadores (`context.turnos_sessao`/`context.turnos_no_no`) sao
        incrementados 1x por turno pelo CALLER (webhook.py `_handle_engine`,
        via HINCRBY fail-open em Redis — reusa `_bump_turnos_sessao`),
        ortogonais ao contador anti-loop `_MAX_TENTATIVAS`/`etapa_funil`
        (FR-001, Acceptance Scenario 5 US1) — nunca fundidos aqui.

        Precedencia (FR-004, Edge Case item 1 — colisao teto-sessao +
        teto-no no mesmo turno): teto de SESSAO sempre prevalece.

        Nunca escalona sobre um resultado que JA e handoff (pedido explicito
        de humano — Regra 26; ou anti-loop `_MAX_TENTATIVAS` esgotado) — nao
        ha o que reforcar sobre uma escalada ja em curso.
        """
        if result.action == "handoff":
            return result

        etapa_corrente = context.etapa or ""

        # 1. Teto de SESSAO (precedencia maxima — FR-004, FR-020/SEC-LLM-3:
        #    destino sempre da allowlist estatica, nunca do LLM).
        if context.turnos_sessao >= settings.max_turnos_sessao:
            destino = _destino_logico_por_caminho(result.caminho or context.caminho)
            handoff_result = self._handoff(
                context, result.updates,
                result.caminho or context.caminho,
                _t("turnos_sessao_handoff", context.idioma),
                etapa=ETAPA_HANDOFF, destino=destino, motivo="turnos_sessao",
            )
            handoff_result.motivo = "turnos_sessao"
            return handoff_result

        # 2. Nudge de no (FR-003) com limiar diferenciado para duvidas
        #    abertas (FR-005) — nunca corta uma duvida legitima. Aplica-se a
        #    qualquer no/etapa com pergunta ou escolha pendente (inclusive o
        #    menu inicial); nao se aplica a etapas ja terminais (handoff) nem
        #    ao turno 1 (sem etapa ainda — `etapa_corrente` vazio).
        if etapa_corrente and etapa_corrente != ETAPA_HANDOFF:
            limiar_no = (
                settings.max_turnos_duvidas if etapa_corrente == ETAPA_DUVIDAS
                else settings.max_turnos_no_no
            )
            if context.turnos_no_no >= limiar_no:
                nudge_txt = _t("turnos_no_no_nudge", context.idioma)
                result.response_text = (
                    (result.response_text or "").rstrip() + "\n\n" + nudge_txt
                ).strip()
                result.turno_acao = "nudge"
                result.motivo = "turnos_no_no"

        return result

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
    # Interpretacao Agentica / Slot-Filling por etapa (Pilar 8, FR-013..FR-018)
    #
    # Cada resolver: 1) fast-path deterministico primeiro (FR-013, sem LLM);
    # 2) SO se nao resolver, fallback agentico via SlotExtractor (FR-014/016);
    # 3) confianca < limiar OU slot invalido -> None ("nao entendido", nunca
    #    adivinha — FR-015); 4) guarda contra reversao silenciosa de fato ja
    #    consolidado quando o resolver e usado para revisitar um valor
    #    conhecido (FASE 3.5/CHK014).
    # ------------------------------------------------------------------

    async def _resolver_slot_booleano(
        self,
        user_message: str,
        contexto: str,
        *,
        fast_path_valor: Optional[bool],
        slot_schema: dict,
        valor_atual: Optional[bool] = None,
    ) -> Optional[bool]:
        """Resolver generico para slots booleanos (sim/nao) — usado por
        `eh_medico` e `experiencia_corporal`."""
        if fast_path_valor is not None:
            if not permitir_reversao(
                valor_atual, fast_path_valor, veio_de_fastpath=True, confianca=1.0,
            ):
                return valor_atual
            return fast_path_valor
        if self._slot_extractor is None:
            return None
        slot = await self._slot_extractor.extract(slot_schema, user_message, contexto)
        self._last_confianca_slot = slot.confianca  # observabilidade aditiva (task 4.3)
        limiar = settings.slot_confidence_threshold
        if not SlotExtractor.aceitar(slot, limiar):
            return None
        novo = _norm(slot.valor or "") in {"sim", "yes", "si", "true"}
        if not permitir_reversao(
            valor_atual, novo, veio_de_fastpath=False, confianca=slot.confianca,
        ):
            return valor_atual
        return novo

    async def _resolver_eh_medico(
        self, context: SessionContext, user_message: str
    ) -> Optional[bool]:
        """ETAPA_QUALIF_MEDICO (data-model.md §3, linha 1)."""
        return await self._resolver_slot_booleano(
            user_message, _perfil_conhecido(context),
            fast_path_valor=_detectar_confirmacao(user_message),
            slot_schema=_SLOT_SCHEMA_EH_MEDICO,
            valor_atual=context.eh_medico,
        )

    async def _resolver_experiencia_corporal(
        self, context: SessionContext, user_message: str
    ) -> Optional[bool]:
        """ETAPA_QUALIF_EXPERIENCIA (data-model.md §3, linha 3)."""
        return await self._resolver_slot_booleano(
            user_message, _perfil_conhecido(context),
            fast_path_valor=_detectar_experiencia_corporal(user_message),
            slot_schema=_SLOT_SCHEMA_EXPERIENCIA_CORPORAL,
            valor_atual=context.experiencia_corporal,
        )

    async def _resolver_objetivo_sistema(
        self, context: SessionContext, user_message: str
    ) -> Optional[str]:
        """ETAPA_SISTEMA_OBJETIVO (data-model.md §3, linha 2)."""
        fast = _detectar_objetivo_sistema(user_message)
        if fast is not None:
            return fast
        if self._slot_extractor is None:
            return None
        slot = await self._slot_extractor.extract(
            _SLOT_SCHEMA_OBJETIVO_SISTEMA, user_message, _perfil_conhecido(context),
        )
        self._last_confianca_slot = slot.confianca  # observabilidade aditiva (task 4.3)
        if not SlotExtractor.aceitar(slot, settings.slot_confidence_threshold):
            return None
        valor = _norm(slot.valor or "")
        if valor in {"incorporar", "abrir", "nao_sei"}:
            return valor
        return None

    async def _resolver_especialidade(
        self, context: SessionContext, user_message: str
    ) -> Optional[str]:
        """ETAPA_QUALIF_ESPECIALIDADE (data-model.md §3, linha 4)."""
        fast = _detectar_especialidade(user_message)
        if fast is not None:
            if not permitir_reversao(
                context.especialidade, fast, veio_de_fastpath=True, confianca=1.0,
            ):
                return context.especialidade
            return fast
        if self._slot_extractor is None:
            return None
        slot = await self._slot_extractor.extract(
            _SLOT_SCHEMA_ESPECIALIDADE, user_message, _perfil_conhecido(context),
        )
        self._last_confianca_slot = slot.confianca  # observabilidade aditiva (task 4.3)
        if not SlotExtractor.aceitar(slot, settings.slot_confidence_threshold):
            return None
        valor = _norm(slot.valor or "")
        mapa = {
            "dermatologia": "dermatologia",
            "cirurgia plastica": "cirurgia plastica",
            "cirurgia vascular": "cirurgia vascular",
            "outra": "outra",
        }
        novo = mapa.get(valor)
        if novo is None:
            return None
        if not permitir_reversao(
            context.especialidade, novo, veio_de_fastpath=False, confianca=slot.confianca,
        ):
            return context.especialidade
        return novo

    async def _resolver_escolha_turma(
        self, context: SessionContext, user_message: str
    ) -> Optional[str]:
        """ETAPA_ESCOLHA_TURMA (data-model.md §3, linha 5). Opcao numerica de
        menu permanece SEMPRE deterministica (dentro de `_detectar_escolha_turma`,
        FR-019) — nunca delegada ao fallback agentico."""
        fast = _detectar_escolha_turma(user_message)
        if fast is not None:
            return fast
        if self._slot_extractor is None:
            return None
        slot = await self._slot_extractor.extract(
            _SLOT_SCHEMA_ESCOLHA_TURMA, user_message, _perfil_conhecido(context),
        )
        self._last_confianca_slot = slot.confianca  # observabilidade aditiva (task 4.3)
        if not SlotExtractor.aceitar(slot, settings.slot_confidence_threshold):
            return None
        valor = _norm(slot.valor or "")
        if valor in {"sp", "sao paulo", "brasil"}:
            return _SLUG_HG360_SP
        if valor in {"barcelona", "espanha"}:
            return _SLUG_HG360_BARCELONA
        return None

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
            objetivo = await self._resolver_objetivo_sistema(context, user_message)
            if objetivo is None:
                return await self._reformular_ou_handoff(
                    context, updates, CaminhoMapaMestre.SISTEMA_GOLDINCISION,
                    ETAPA_SISTEMA_OBJETIVO, _t("sistema_etapa1_2", idioma),
                )
            _tent_clear(context, updates)
            if objetivo == "incorporar":
                # Sub-caminho 1 → Licenciamento. ANTI-REDUNDANCIA: nao re-perguntar se
                # ja sabemos (a info e duravel e pode ter sido coletada em outro caminho,
                # ex.: presencial). So perguntar quando eh_medico for desconhecido.
                if context.eh_medico is True:
                    return self._abrir_licenciamento_duvidas(context, updates)
                if context.eh_medico is False:
                    # Ja sabemos que nao e medico → Licenciamento e exclusivo; Franquia.
                    return self._handoff(
                        context, updates, CaminhoMapaMestre.SISTEMA_GOLDINCISION,
                        _t("sistema_lic_naomedico", idioma),
                        destino=DEST_FRANQUIA,
                        motivo="licenciamento_nao_medico_oferece_franquia",
                    )
                updates["etapa_mapa_mestre"] = ETAPA_SISTEMA_LICENCIAMENTO
                pergunta = await self._gerar_pergunta_medico(
                    idioma, CaminhoMapaMestre.SISTEMA_GOLDINCISION
                )
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
                    ETAPA_SISTEMA_LICENCIAMENTO,
                    await self._gerar_pergunta_medico(
                        idioma, CaminhoMapaMestre.SISTEMA_GOLDINCISION
                    ),
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
            # Medico → abrir duvidas do Licenciamento com resumo objetivo.
            return self._abrir_licenciamento_duvidas(context, updates)

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
            # RAG hibrido (FASE 5, research.md Decision 7): abster=True curto-
            # circuita ANTES do LLM de redacao (nunca gera texto sem fonte).
            _glue = _resposta_glue_pura(context, user_message)
            if _glue is not None:
                updates["etapa_mapa_mestre"] = ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS
                return FlowResult(
                    _glue, "continue", CaminhoMapaMestre.SISTEMA_GOLDINCISION,
                    ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS, updates,
                )
            knowledge, resultado_rag = await self._load_knowledge_by_slug(
                _SLUG_LICENCIAMENTO, idioma, user_message
            )
            if resultado_rag.abster:
                resposta, handoff = _fallback_indisponivel_response(idioma), True
            else:
                history = self._memory.build_messages_for_llm(context, max_msgs=8)
                resposta, handoff = await self._responder.generate(
                    user_message=user_message, caminho=_SLUG_LICENCIAMENTO,
                    etapa=ETAPA_DUVIDAS, knowledge_context=knowledge,
                    chunks_recuperados=resultado_rag.chunks,
                    session_history=history, session_summary=context.resumo_rolante,
                    idioma=idioma, known_facts=_perfil_conhecido(context),
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
            perfil_fr = _detectar_medico_investidor(user_message)
            logger.info(
                "flow: sistema franquia perfil=%s contato_id=%s",
                perfil_fr, context.contato_id,
            )
            # Guardar o perfil declarado (medico/investidor) para reuso (anti-redundancia
            # e contexto ao especialista no handoff).
            if perfil_fr:
                _merge_perfil(context, updates, {"perfil_franquia": perfil_fr})
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

    def _abrir_licenciamento_duvidas(
        self, context: SessionContext, updates: dict
    ) -> FlowResult:
        """
        Abre a fase de DUVIDAS do Licenciamento com um resumo objetivo (sem dump
        verbatim da apresentacao). O objetivo do C3 e qualificar e conduzir a uma
        reuniao com especialista (nunca vender).

        Reusado tanto quando o lead acabou de confirmar que e medico quanto quando
        ja sabiamos disso de outro caminho (anti-redundancia: nao re-perguntar).
        """
        idioma = context.idioma
        leadin = f"{_saudacao(context)}\n\n"
        texto = (leadin + _t("sistema_lic_resumo", idioma)).strip()
        updates["etapa_mapa_mestre"] = ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS
        return FlowResult(
            texto, "continue", CaminhoMapaMestre.SISTEMA_GOLDINCISION,
            ETAPA_SISTEMA_LICENCIAMENTO_DUVIDAS, updates,
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
                    await self._gerar_pergunta_medico(idioma, cam),
                )
            context.eh_medico = eh_medico
            updates["eh_medico"] = eh_medico
            _tent_clear(context, updates)
            if eh_medico is False:
                return await self._encerra_nao_elegivel(context, updates, cam)
            return await self._enviar_link_curso_online(context, updates)

        # Confirmacao de medico (etapa de qualificacao)
        if context.etapa == ETAPA_QUALIF_MEDICO:
            eh_medico = await self._resolver_eh_medico(context, user_message)
            if eh_medico is not None:
                context.eh_medico = eh_medico
                updates["eh_medico"] = eh_medico
                _tent_clear(context, updates)
            elif not _eh_pergunta(user_message):
                return await self._reformular_ou_handoff(
                    context, updates, cam, ETAPA_QUALIF_MEDICO,
                    await self._gerar_pergunta_medico(idioma, cam),
                )

        # Sinal forte de fechamento (quer o link / inscrever-se): conduzir ao
        # fechamento mesmo que ainda nao tenhamos qualificado — o gate medico e
        # aplicado dentro de _close_curso_online_link.
        fech = _detectar_fechamento(user_message)
        if fech == "aceita":
            updates["etapa_mapa_mestre"] = ETAPA_FECHAMENTO
            return await self._close_curso_online_link(context, updates)

        # Pergunta direta (preco/conteudo/duracao/certificado OU pergunta geral sobre
        # o curso) ANTES de qualificar → responder da Base na hora, sem disparar o gate
        # medico (Mapa Mestre, REGRA do Caminho 1: evita "Quanto custa? → Você é médico?").
        # Limpa o contador de tentativas: a mudanca de etapa (→ duvidas) nao deve
        # deixar preso o contador de qualif_medico (evita handoff prematuro).
        if context.eh_medico is None and _eh_pergunta(user_message):
            _tent_clear(context, updates)
            return await self._responder_duvida_online(context, user_message, updates)

        # Qualificacao medica (so quando nao e pergunta direta)
        if context.eh_medico is None:
            updates["etapa_mapa_mestre"] = ETAPA_QUALIF_MEDICO
            return FlowResult(
                await self._gerar_pergunta_medico(idioma, cam), "continue",
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
        _glue = _resposta_glue_pura(context, user_message)
        if _glue is not None:
            updates["etapa_mapa_mestre"] = ETAPA_DUVIDAS
            return FlowResult(
                _glue, "continue", CaminhoMapaMestre.CURSO_ONLINE_HG,
                ETAPA_DUVIDAS, updates,
            )
        knowledge, resultado_rag = await self._load_knowledge_by_slug(
            "curso-online-hg", idioma, user_message
        )
        if resultado_rag.abster:
            resposta, handoff = _fallback_indisponivel_response(idioma), True
        else:
            history = self._memory.build_messages_for_llm(context, max_msgs=8)
            resposta, handoff = await self._responder.generate(
                user_message=user_message, caminho="curso-online-hg",
                etapa=ETAPA_DUVIDAS, knowledge_context=knowledge,
                chunks_recuperados=resultado_rag.chunks,
                session_history=history, session_summary=context.resumo_rolante,
                idioma=idioma, known_facts=_perfil_conhecido(context),
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
            await self._gerar_pergunta_medico(context.idioma, cam), "continue",
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
            eh_medico = await self._resolver_eh_medico(context, user_message)
            if eh_medico is not None:
                context.eh_medico = eh_medico
                updates["eh_medico"] = eh_medico
                _tent_clear(context, updates)
            else:
                return await self._reformular_ou_handoff(
                    context, updates, cam, ETAPA_QUALIF_MEDICO,
                    await self._gerar_pergunta_medico(idioma, cam),
                )

        if context.etapa == ETAPA_QUALIF_EXPERIENCIA and context.experiencia_corporal is None:
            exp = await self._resolver_experiencia_corporal(context, user_message)
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
            especialidade = await self._resolver_especialidade(context, user_message)
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
            slug_escolhido = await self._resolver_escolha_turma(context, user_message)
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
                await self._gerar_pergunta_medico(idioma, cam), "continue",
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
                partes.append(_t("trilha_conector", idioma) + "\n\n" + apres_360)
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
        _glue = _resposta_glue_pura(context, user_message)
        if _glue is not None:
            updates["etapa_mapa_mestre"] = ETAPA_DUVIDAS
            return FlowResult(_glue, "continue", cam, ETAPA_DUVIDAS, updates)
        knowledge, resultado_rag = await self._load_knowledge_by_slug(
            slug, idioma, user_message
        )
        # Despacho por SLUG (corrige o bug de colisao de prompts numericos).
        prompt_key = "trilha-hg" if slug == _SLUG_HG_MODULO_1 else slug
        if resultado_rag.abster:
            resposta, handoff = _fallback_indisponivel_response(idioma), True
        else:
            history = self._memory.build_messages_for_llm(context, max_msgs=8)
            resposta, handoff = await self._responder.generate(
                user_message=user_message, caminho=prompt_key,
                etapa=ETAPA_DUVIDAS, knowledge_context=knowledge,
                chunks_recuperados=resultado_rag.chunks,
                session_history=history, session_summary=context.resumo_rolante,
                idioma=idioma, known_facts=_perfil_conhecido(context),
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

    async def _load_knowledge(
        self, caminho: int, idioma: str, user_message: str = ""
    ) -> tuple[str, ResultadoRecuperacao]:
        slug = _CAMINHO_PARA_SLUG.get(caminho)
        if slug is None:
            return "", ResultadoRecuperacao()
        return await self._load_knowledge_by_slug(
            slug=slug, idioma=idioma, user_message=user_message
        )

    async def _get_curso(self, slug: str) -> Optional[Curso]:
        """Busca o Curso ativo pelo slug (ponto unico de acesso — DRY)."""
        stmt = select(Curso).where(Curso.slug == slug, Curso.ativo.is_(True))
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def _scalar_idioma(self, model, curso_id: int, idioma: str):
        """SELECT escalar de `model` por (curso_id, idioma) com fallback PT."""
        stmt = select(model).where(model.curso_id == curso_id, model.idioma == idioma)
        row = (await self._db.execute(stmt)).scalar_one_or_none()
        if row is None and idioma != "pt":
            stmt = select(model).where(model.curso_id == curso_id, model.idioma == "pt")
            row = (await self._db.execute(stmt)).scalar_one_or_none()
        return row

    async def _list_idioma(self, model, curso_id: int, idioma: str) -> list:
        """SELECT em lista de `model` por (curso_id, idioma) com fallback PT."""
        stmt = select(model).where(model.curso_id == curso_id, model.idioma == idioma)
        itens = (await self._db.execute(stmt)).scalars().all()
        if not itens and idioma != "pt":
            stmt = select(model).where(model.curso_id == curso_id, model.idioma == "pt")
            itens = (await self._db.execute(stmt)).scalars().all()
        return list(itens)

    async def _load_apresentacao(self, slug: str, idioma: str) -> str:
        """Carrega APENAS a apresentacao oficial verbatim (fallback PT)."""
        curso = await self._get_curso(slug)
        if curso is None:
            return ""
        apres = await self._scalar_idioma(CursoApresentacao, curso.id, idioma)
        return apres.texto if apres else ""

    async def _load_curso_link(self, slug: str, idioma: str) -> Optional[str]:
        """Carrega o link de inscricao do curso no idioma (fallback PT)."""
        curso = await self._get_curso(slug)
        if curso is None:
            return None
        link = await self._scalar_idioma(CursoLink, curso.id, idioma)
        return link.url if link else None

    async def _load_knowledge_by_slug(
        self, slug: str, idioma: str, user_message: str = ""
    ) -> tuple[str, ResultadoRecuperacao]:
        """
        Carrega base de conhecimento para o slug e idioma.

        Hierarquia: Apresentacao (verbatim, fora do RAG, FR-014) +
        `HybridRetriever.buscar()` (Objecoes+FAQ+base, Onda 3/FASE 5,
        FR-001..FR-006, substitui os antigos SELECTs diretos de
        `CursoObjecao`/`Faq` sem ranqueamento) + Turmas (verbatim) + Link de
        inscricao (verbatim).

        Retorna `(knowledge_context, resultado)`. O CHAMADOR MUST checar
        `resultado.abster` ANTES de usar `knowledge_context`/chamar
        `GroundedResponder.generate()` — `abster=True` curto-circuita
        diretamente em `_fallback_indisponivel_response(idioma), True`, SEM
        chamar o LLM de redacao (research.md Decision 7). Quando
        `self._retriever` e None (deps nao injetadas — testes legados que
        nao cobrem RAG) o comportamento e identico ao pre-FASE5 para estas
        3 secoes: `abster=False`, sem secao de recuperacao.
        """
        curso = await self._get_curso(slug)
        if curso is None:
            logger.warning("flow: curso nao encontrado slug=%s", slug)
            return "", ResultadoRecuperacao()

        sections: list[str] = []

        apres = await self._scalar_idioma(CursoApresentacao, curso.id, idioma)
        if apres:
            sections.append(f"=== APRESENTACAO OFICIAL ({idioma}) ===\n{apres.texto}")

        resultado = ResultadoRecuperacao()
        if self._retriever is not None:
            resultado = await self._retriever.buscar(user_message, curso.id, idioma)
            if resultado.abster:
                # Sem fonte suficiente (sem_candidatos/abaixo_limiar/
                # indisponivel) -> o chamador curto-circuita ANTES do LLM;
                # o restante do knowledge_context nunca sera usado.
                return "\n\n".join(sections), resultado
            if resultado.chunks:
                chunks_text = "\n\n".join(c.conteudo for c in resultado.chunks)
                sections.append(
                    "=== BASE OFICIAL RECUPERADA (objecoes/FAQ/base) ===\n"
                    f"{chunks_text}"
                )

        stmt_turmas = select(CursoTurma).where(
            CursoTurma.curso_id == curso.id, CursoTurma.ativo.is_(True),
        )
        turmas = (await self._db.execute(stmt_turmas)).scalars().all()
        if turmas:
            turmas_text = "\n".join(
                f"- {t.cidade} ({t.pais or ''}): {t.data_inicio or 'data a confirmar'}"
                f"{', lote: ' + t.lote_preco if t.lote_preco else ''}"
                for t in turmas
            )
            sections.append(f"=== TURMAS DISPONIVEIS ===\n{turmas_text}")

        link = await self._scalar_idioma(CursoLink, curso.id, idioma)
        if link:
            sections.append(f"=== LINK DE INSCRICAO ({idioma}) ===\n{link.url}")

        return "\n\n".join(sections), resultado

    # ------------------------------------------------------------------
    # Helpers de geracao de perguntas padrao (texto fixo — anti-alucinacao)
    # ------------------------------------------------------------------

    async def _gerar_pergunta_medico(
        self, idioma: str, caminho: Optional[int] = None
    ) -> str:
        """Pergunta de qualificacao medica fiel ao Mapa Mestre (texto por caminho)."""
        chave = {
            CaminhoMapaMestre.CURSO_ONLINE_HG: "qualif_medico_c1",
            CaminhoMapaMestre.CURSOS_PRESENCIAIS: "qualif_medico_c2",
            CaminhoMapaMestre.SISTEMA_GOLDINCISION: "qualif_medico_lic",
        }.get(caminho)
        return (_t(chave, idioma) if chave else "") or _t("pergunta_medico", idioma)

    async def _gerar_pergunta_experiencia(self, idioma: str) -> str:
        return _t("pergunta_experiencia", idioma)

    async def _gerar_pergunta_especialidade(self, idioma: str) -> str:
        return _t("pergunta_especialidade", idioma)

    async def _gerar_pergunta_escolha_turma(self, idioma: str) -> str:
        return _t("pergunta_turma", idioma)


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


def _opcao_numerica(t: str, max_n: int = 6) -> Optional[int]:
    """Numero de opcao de menu (1..max_n) como PALAVRA INTEIRA (evita casar 'sp'
    em 'esperar' ou '1' em '10/2026'). Retorna o inteiro ou None."""
    toks = set(t.replace("️⃣", " ").split())
    mapa = {
        1: {"1", "1.", "um", "one", "uno", "primeira", "primeiro"},
        2: {"2", "2.", "dois", "two", "dos", "segunda", "segundo"},
        3: {"3", "3.", "tres", "three"},
        4: {"4", "4.", "quatro", "four", "cuatro"},
        5: {"5", "5.", "cinco", "five"},
        6: {"6", "6.", "seis", "six"},
    }
    for n in range(1, max_n + 1):
        if toks & mapa[n]:
            return n
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
    # Numeros do menu (1=SP, 2=Barcelona)
    n = _opcao_numerica(t, 2)
    if n == 1:
        return _SLUG_HG360_SP
    if n == 2:
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
    # Numeros do menu (1=incorporar, 2=abrir, 3=nao_sei)
    return {1: "incorporar", 2: "abrir", 3: "nao_sei"}.get(_opcao_numerica(t, 3))


def _detectar_opcao_aluno(texto: str) -> Optional[str]:
    """Caminho 4 — submenu (6 opcoes). Retorna rotulo curto ou None."""
    t = _norm(texto)
    # 1..6 → rotulo (mesma ordem do submenu apresentado)
    rotulos = [
        "plataforma_acesso", "certificado", "suporte_tecnico",
        "pagamento", "duvidas_curso", "outro",
    ]
    # Palavras-chave por opcao (prioridade sobre o numero)
    kw = [
        (["plataforma", "acesso", "acessar", "login", "platform"], "plataforma_acesso"),
        (["certificado", "certificate", "conclusao", "diploma"], "certificado"),
        (["suporte tecnico", "grupo", "tecnico", "support group"], "suporte_tecnico"),
        (["pagamento", "inscricao", "boleto", "pix", "payment", "pago"], "pagamento"),
        (["duvida", "duvidas", "sobre o curso", "conteudo", "question"], "duvidas_curso"),
        (["outro", "outra", "other", "otro"], "outro"),
    ]
    for chaves, rotulo in kw:
        if any(k in t for k in chaves):
            return rotulo
    n = _opcao_numerica(t, 6)
    return rotulos[n - 1] if n else None


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
                "pode me", "gostaria de saber", "gostaria de informac", "queria saber",
                "quero saber", "me fala", "me conta", "me explica", "fala sobre",
                "conta sobre", "sobre o curso", "saber mais", "informacoes",
                "informacao", "detalhes", "what", "how", "which", "tell me",
                "tem ", "existe", "ha ", "há "]
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


# ---------------------------------------------------------------------------
# Overflow de turno — classificacao fast-path da resposta ao convite
# "continuar explicando ou especialista?" (anti-rajada). Deterministico primeiro
# (ZERO LLM); o fallback agentico fica em FlowEngine._aplicar_overflow_resume.
# ---------------------------------------------------------------------------
# Continuacao afirmativa (PT/EN/ES) — "quero o resto da explicacao".
_OVERFLOW_CONTINUAR = {
    "sim", "s", "si", "pode", "pode continuar", "continua", "continuar", "continue",
    "continúa", "sigue", "prossiga", "segue", "seguir", "manda", "manda ver", "quero",
    "quero sim", "quero saber", "vai", "bora", "isso", "claro", "ok", "okay", "yes",
    "yeah", "yep", "yup", "por favor", "porfavor", "pfv", "explica", "explique",
    "explicar", "detalha", "detalhar", "mais", "keep going", "go on", "go ahead",
    "tell me more", "more", "dale", "adelante", "continua por favor", "pode sim",
    "pode falar", "pode explicar", "manda o resto", "quero o resto", "continua ai",
}
# Preferencia por especialista / atendimento humano.
_OVERFLOW_ESPECIALISTA = {
    "especialista", "consultor", "atendente", "humano", "pessoa", "alguem",
    "specialist", "human", "agent", "someone", "person", "especialista por favor",
    "prefiro especialista", "quero especialista", "falar com especialista",
    "falar com alguem", "falar com atendente", "quero falar com", "me conecta",
    "conectar", "presencial", "reuniao", "reunion", "meeting", "call", "ligacao",
}
# Substrings que indicam pedido de especialista/humano (frases mais longas).
_OVERFLOW_ESPECIALISTA_SUBSTR = (
    "especialista", "consultor", "atendente", "com um humano", "com uma pessoa",
    "falar com alguem", "prefiro pessoa", "specialist", "with a human",
    "with someone", "talk to a person", "hablar con", "un especialista",
    "presencial", "pessoalmente", "in person",
)


# ---------------------------------------------------------------------------
# "Glue" conversacional — saudacao/agradecimento/afirmacao pura. Num no de
# DUVIDAS, isto NAO deve ir ao RAG (que abstem -> handoff, deixando o agente
# "engessado"). Anti-alucinacao intacta: DUVIDA FACTUAL real (com "?" ou tokens
# substantivos) continua indo ao RAG e abstendo quando sem base.
# ---------------------------------------------------------------------------
_GLUE_TOKENS = {
    # afirmacoes / reconhecimentos
    "sim", "s", "si", "ok", "okay", "okey", "claro", "certo", "beleza", "blz",
    "perfeito", "otimo", "otima", "legal", "show", "isso", "ta", "tah", "entendi",
    "entendido", "bacana", "massa", "top", "joia", "tranquilo", "bom", "bem",
    "combinado", "maravilha", "excelente", "positivo", "aham", "uhum", "yes",
    "yeah", "yep", "sure", "nice", "great", "vale", "dale", "genial",
    # saudacoes
    "oi", "ola", "opa", "eai", "ai", "hey", "hi", "hello", "hola", "buenas",
    # agradecimentos
    "obrigado", "obrigada", "obg", "obgd", "vlw", "valeu", "thanks", "thx",
    "gracias", "grato", "grata",
}
_GLUE_PHRASES = {
    "bom dia", "boa tarde", "boa noite", "tudo bem", "tudo bom", "tudo certo",
    "muito obrigado", "muito obrigada", "obrigado mesmo", "obrigada mesmo",
    "thank you", "ta bom", "ta bem", "esta bem", "ok obrigado", "ok obrigada",
    "pode ser", "ta otimo", "que bom", "muy bien", "esta bien", "de acuerdo",
    "tudo otimo", "show de bola",
}


def _glue_pura(user_message: str) -> bool:
    """
    True se a mensagem e glue conversacional PURA (saudacao/agradecimento/
    afirmacao curta), SEM conteudo de pergunta. Conservador: rejeita se houver
    "?" ou > 4 tokens, e exige que TODOS os tokens sejam glue (nao captura
    "quanto custa", "onde e" etc. — duvidas factuais seguem para o RAG).
    """
    if "?" in (user_message or ""):
        return False
    t = _norm(user_message)
    if not t:
        return False
    if t in _GLUE_PHRASES:
        return True
    toks = t.split()
    if len(toks) > 4:
        return False
    return all(tok in _GLUE_TOKENS for tok in toks)


def _resposta_glue_pura(context: SessionContext, user_message: str) -> Optional[str]:
    """Texto de reconhecimento (deterministico, no idioma) se a mensagem for glue
    pura; senao None (segue para o RAG/roteamento normal)."""
    if not _glue_pura(user_message):
        return None
    return _t("glue_ack", context.idioma)


def _classificar_overflow_fastpath(texto: str) -> Optional[str]:
    """
    Classifica DETERMINISTICAMENTE a resposta ao convite de overflow.

    Retorna "continuar" | "especialista" | None (fast-path nao resolveu —
    cabe ao fallback agentico ou a tratar como mensagem nova). Especialista tem
    precedencia sobre continuar quando ambos aparecem (o lead pediu humano).
    """
    t = _norm(texto)
    if not t:
        return None
    if any(s in t for s in _OVERFLOW_ESPECIALISTA_SUBSTR):
        return "especialista"
    toks = set(t.split())
    if t in _OVERFLOW_ESPECIALISTA or (toks & _OVERFLOW_ESPECIALISTA):
        return "especialista"
    if t in _OVERFLOW_CONTINUAR:
        return "continuar"
    # afirmativos curtos de 1-3 palavras (ex.: "pode continuar sim", "sim claro")
    if len(toks) <= 3 and (toks & _OVERFLOW_CONTINUAR):
        return "continuar"
    return None


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
