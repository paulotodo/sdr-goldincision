"""
Gerador de respostas conversacionais ancorando na base de conhecimento oficial.

Usa o modelo de raciocinio (gpt-4o) com:
- Grounding estrito: hierarquia Mapa Mestre → Base → Objecoes → FAQ
- Recusa explicita fora da base (FR-008) com handoff imediato
- Apresentacoes verbatim (FR-010) — nunca parafrasear textos oficiais
- Objecoes EXCLUSIVAMENTE do Banco de Objecoes Oficial (FR-011)
- Identidade: "Consultor Virtual Oficial" (FR-013)
- Separacao estrutural sistema/usuario (SEC-LLM-1)
- 1 pergunta por mensagem (FR-015)
- Blocos curtos (FR-015)

Implementacao completa: FASE 4, task 4.2.
"""
from __future__ import annotations

from typing import Optional


class GroundedResponder:
    """
    Gera resposta do fluxo conversacional com grounding estrito.

    STUB: implementacao completa em FASE 4, task 4.2.
    """

    async def generate(
        self,
        user_message: str,
        caminho: int,
        etapa: str,
        knowledge_context: str,
        session_summary: Optional[str] = None,
    ) -> str:
        """
        Gera resposta grounded no contexto de conhecimento oficial.

        Args:
            user_message: mensagem do lead (tratada como nao-confiavel — SEC-LLM-1)
            caminho: caminho do Mapa Mestre (1-6)
            etapa: etapa fina dentro do caminho
            knowledge_context: trecho da base oficial para grounding
            session_summary: resumo rolante da sessao (FR-019)

        Returns:
            Texto de resposta para enviar ao lead
        """
        # TODO (FASE 4): construir prompt com separacao sistema/usuario
        # TODO (FASE 4): chamar openai_client com modelo raciocinio
        # TODO (FASE 4): verificar que resposta esta ancorada na base
        # TODO (FASE 4): garantir 1 pergunta na resposta
        raise NotImplementedError("GroundedResponder implementado em FASE 4")
