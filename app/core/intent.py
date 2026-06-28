"""
Classificacao de intencao e deteccao de idioma.

Usa o modelo barato (gpt-4o-mini) para:
- Classificar intencao do lead (qual caminho do Mapa Mestre)
- Detectar idioma (pt/en/es)
- Decidir entre entrada direta no fluxo (intencao clara) ou menu de 6 opcoes

Implementacao completa: FASE 4, task 4.1.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class Idioma(str, Enum):
    PT = "pt"
    EN = "en"
    ES = "es"


class ClassificacaoIntencao(str, Enum):
    CURSO_ONLINE = "curso_online"
    HG_MODULO_1 = "hg_modulo_1"
    HG360_SP = "hg360_sp"
    HG360_BARCELONA = "hg360_barcelona"
    PACIENTE_MODELO = "paciente_modelo"
    LICENCIAMENTO_FRANQUIA = "licenciamento_franquia"
    AMBIGUA = "ambigua"   # Menu de 6 opcoes


class IntentClassifier:
    """
    Classifica intencao e idioma usando modelo barato.
    - Intencao clara → entrada direta no caminho (FR-007)
    - Intencao ambigua → menu de 6 opcoes

    STUB: implementacao completa em FASE 4, task 4.1.
    """

    async def classify(
        self, message: str, session_context: Optional[dict] = None
    ) -> tuple[ClassificacaoIntencao, Idioma]:
        """
        Classifica intencao e idioma de uma mensagem.

        Returns:
            (ClassificacaoIntencao, Idioma) detectados
        """
        # TODO (FASE 4): chamar openai_client com modelo barato
        # TODO (FASE 4): retornar classificacao estruturada
        raise NotImplementedError("IntentClassifier implementado em FASE 4")
