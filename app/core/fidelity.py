"""
Portao de Verificacao de Fidelidade (Pilar 7, FR-008..FR-012).

`FidelityGate.verificar()` confere, via gpt-4o-mini, se um texto de resposta
JA REDIGIDO (pelo contrato JSON do Pilar 6 — `app/core/contracts.py`) esta
100% sustentado pelo `knowledge_context` oficial, ANTES do envio ao lead —
mas SOMENTE quando o texto toca uma "condicao comercial" (dec-010): preco/
valor, parcelamento, desconto/promocao, data/prazo, disponibilidade de
turma/vaga, elegibilidade medica.

Fail-closed (FR-012): qualquer erro/timeout (`VERIFY_TIMEOUT_SECONDS`, hard
cap ~3s) e SEMPRE tratado como `fiel=False` — nunca aprovacao por omissao.

APRESENTACOES VERBATIM continuam FORA do portao (mesma excecao do FR-007):
o call-site (`GroundedResponder.generate()`) so invoca este gate para texto
gerado via LLM, nunca para blocos canonicos/verbatim.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)

# Marcador usado quando a verificacao falha/expira (fail-closed) — nunca
# afirmacao real do texto, apenas sinalizacao de indisponibilidade.
INDISPONIVEL = "<indisponivel>"

# Gatilho de "condicao comercial" (Decision 5 / dec-010, FR-008/FR-011): mesma
# lista textual usada por `responder._e_contexto_factual` para acionar
# temperatura baixa — reaproveitada aqui como fonte unica de verdade do que
# conta como condicao comercial para o portao de fidelidade.
CONDICOES_COMERCIAIS: tuple[str, ...] = (
    "preço",
    "preco",
    "valor",
    "valores",
    "parcel",
    "desconto",
    "promo",
    "data",
    "prazo",
    "turma",
    "vaga",
    "disponibilidade",
    "elegib",
    "crm",
)


def gatilho_condicao_comercial(texto: str) -> bool:
    """True se `texto` toca alguma condicao comercial (dec-010) — UNICO caso
    em que o portao de fidelidade deve ser acionado (FR-008/FR-011)."""
    texto_lower = (texto or "").lower()
    return any(palavra in texto_lower for palavra in CONDICOES_COMERCIAIS)


class VeredictoFidelidade(BaseModel):
    """Resultado da verificacao que antecede o envio de uma resposta gerada
    sensivel (preco/data/elegibilidade/condicao comercial). Ver data-model.md §2."""

    model_config = ConfigDict(extra="forbid")

    fiel: bool  # FR-009: texto sustentado pela base oficial?
    afirmacoes_nao_sustentadas: list[str] = Field(default_factory=list)  # FR-010

    @model_validator(mode="after")
    def _fiel_apenas_sem_afirmacoes_nao_sustentadas(self) -> "VeredictoFidelidade":
        """Invariante (data-model.md §2): `fiel=True` so quando a lista de
        afirmacoes nao sustentadas esta vazia. Defensivo/fail-closed: se o
        LLM devolver um veredito inconsistente (fiel=true COM afirmacoes
        listadas), forcamos fiel=False em vez de confiar cegamente."""
        if self.fiel and self.afirmacoes_nao_sustentadas:
            logger.warning(
                "fidelity: veredito inconsistente do LLM (fiel=true com "
                "afirmacoes nao sustentadas) — forcando fail-closed"
            )
            self.fiel = False
        return self


_SYSTEM_VERIFICACAO = """\
Você é um verificador de fidelidade (fact-checker). Sua ÚNICA tarefa é \
conferir se o TEXTO DE RESPOSTA abaixo está 100% sustentado pelo CONTEXTO \
DE CONHECIMENTO OFICIAL fornecido — nunca use conhecimento externo, bom \
senso ou suposições.

Regras:
1. Toda afirmação factual do TEXTO DE RESPOSTA sobre preço, valor, \
parcelamento, desconto, data, prazo, disponibilidade de turma/vaga ou \
elegibilidade deve estar EXPLICITAMENTE sustentada pelo CONTEXTO DE \
CONHECIMENTO OFICIAL.
2. Se QUALQUER afirmação não estiver sustentada pelo contexto, retorne \
fiel=false e liste cada uma delas em afirmacoes_nao_sustentadas.
3. Se todas as afirmações estiverem sustentadas (ou o texto não fizer \
nenhuma afirmação factual verificável), retorne fiel=true e \
afirmacoes_nao_sustentadas=[].
4. Não invente conteúdo do contexto nem do texto — apenas compare os dois.
"""


class FidelityGate:
    """Verifica groundedness de uma resposta ja redigida, fail-closed."""

    def __init__(self, openai_client: Any, timeout_seconds: float = 3.0) -> None:
        self._client = openai_client
        self._timeout_seconds = timeout_seconds

    async def verificar(self, texto: str, knowledge_context: str) -> VeredictoFidelidade:
        """
        Confere `texto` contra `knowledge_context` via gpt-4o-mini.

        Fail-closed (FR-012): qualquer excecao (parsing/API) OU timeout
        (`VERIFY_TIMEOUT_SECONDS`, hard cap) retorna
        `VeredictoFidelidade(fiel=False, afirmacoes_nao_sustentadas=[INDISPONIVEL])`
        — nunca aprovacao por omissao.
        """
        messages = [
            {"role": "system", "content": _SYSTEM_VERIFICACAO},
            {
                "role": "user",
                "content": (
                    "=== CONTEXTO DE CONHECIMENTO OFICIAL ===\n"
                    + (knowledge_context or "Nenhum conteudo de base carregado.")
                    + "\n=== FIM DO CONTEXTO DE CONHECIMENTO OFICIAL ===\n\n"
                    + "=== TEXTO DE RESPOSTA A VERIFICAR ===\n"
                    + (texto or "")
                    + "\n=== FIM DO TEXTO DE RESPOSTA ==="
                ),
            },
        ]

        try:
            raw = await asyncio.wait_for(
                self._client.chat_cheap_json(messages, VeredictoFidelidade),
                timeout=self._timeout_seconds,
            )
            veredito = VeredictoFidelidade.model_validate_json(raw)
        except Exception as exc:
            logger.warning(
                "fidelity: verificacao falhou/expirou (fail-closed). "
                "timeout=%ss err=%s: %s",
                self._timeout_seconds,
                type(exc).__name__,
                exc,
            )
            return VeredictoFidelidade(
                fiel=False, afirmacoes_nao_sustentadas=[INDISPONIVEL]
            )

        logger.info(
            "fidelity: veredito fiel=%s n_afirmacoes_nao_sustentadas=%s",
            veredito.fiel,
            len(veredito.afirmacoes_nao_sustentadas),
        )
        return veredito
