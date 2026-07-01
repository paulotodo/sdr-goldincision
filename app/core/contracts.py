"""
Contrato JSON estruturado do pacote de resposta gerada (Pilar 6, FR-001..FR-007).

`RespostaEstruturada` e o que o modelo de raciocinio (gpt-4o) ENTENDEU e REDIGIU
para uma resposta de duvida — NUNCA decide o proximo passo do atendimento
(FR-006): a maquina de estados determinística do FlowEngine nunca ve este objeto,
apenas a 2-tupla `(texto, handoff)` que `GroundedResponder.generate()` extrai dele
(ver `app/core/flow.py:1403,1409`).

Validacao estrita (`extra="forbid"`): pacote que nao valida == falha de geracao
(FR-002), nunca resposta valida — `generate()` faz 1 retry e, se persistir a
falha, retorna `precisa_handoff=True` (nunca conteudo improvisado).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Idiomas suportados pelo atendimento (FR-005).
IDIOMAS_SUPORTADOS = ("pt", "en", "es")


class RespostaEstruturada(BaseModel):
    """O que o modelo entendeu e redigiu para uma resposta de duvida.

    NAO decide o proximo passo do atendimento (FR-006): apenas informa
    `precisa_handoff` como sinal para o FlowEngine, que resolve destino/queueId
    a partir da allowlist de configuracao (SEC-LLM-3), nunca do LLM.
    """

    model_config = ConfigDict(extra="forbid")

    texto: str = Field(..., min_length=1)  # FR-001: texto da resposta
    fontes: list[str] = Field(default_factory=list)  # FR-001/FR-007: base/fontes usadas
    precisa_handoff: bool = False  # FR-001: indica necessidade de humano
    confianca: float = Field(..., ge=0.0, le=1.0)  # FR-001: grau de confianca
    idioma: str = Field(..., pattern="^(pt|en|es)$")  # FR-005: idioma da resposta
