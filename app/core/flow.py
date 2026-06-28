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

Implementacao completa: FASE 4 (tasks 4.1, 4.2, 4.3).
"""
from __future__ import annotations

from enum import IntEnum


class CaminhoMapaMestre(IntEnum):
    """
    Os 6 caminhos do Mapa Mestre de Atendimento GoldIncision.
    Mapeados aos cursos/programas da GoldIncision.
    """
    CURSO_ONLINE_HG = 1       # Curso Online Harmonizacao Glutea
    HG_MODULO_1 = 2           # Harmonizacao Glutea Modulo 1 (presencial SP)
    HG360_SP = 3              # HG360 Sao Paulo
    HG360_BARCELONA = 4       # HG360 Barcelona
    PACIENTE_MODELO = 5       # Lead quer ser paciente modelo (Nidia)
    LICENCIAMENTO_FRANQUIA = 6  # Licenciamento / Franquia


class FlowEngine:
    """
    Motor de fluxo conversacional baseado no Mapa Mestre.

    Responsabilidades:
    - Receber input de usuario + contexto de sessao
    - Determinar caminho/etapa
    - Verificar elegibilidade (FR-009)
    - Orquestrar chamadas ao responder (com grounding na base)
    - Determinar handoff quando necessario
    - Garantir 1 pergunta por mensagem (FR-015)

    STUB: implementacao completa em FASE 4.
    """

    async def process(self, ticket_id: int, user_message: str, context: dict) -> dict:
        """
        Processa mensagem do lead e retorna resposta gerada + acao.

        Returns:
            dict com keys: response_text, action (continue/handoff/end), caminho
        """
        # TODO (FASE 4): implementar os 6 caminhos
        # TODO (FASE 4): grounding estrito na hierarquia
        # TODO (FASE 4): elegibilidade medica
        # TODO (FASE 4): identificacao de intencao (via intent.py)
        # TODO (FASE 4): geracao via responder.py ancorada no contexto
        raise NotImplementedError("FlowEngine implementado em FASE 4")
