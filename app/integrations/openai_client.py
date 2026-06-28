"""
Cliente OpenAI — chat completions + transcricao de audio.

Dois modelos configurados via env (FR-001/005, Decisao 4 research.md):
- Modelo de raciocinio (OPENAI_MODEL_REASONING, default gpt-4o):
  geracao de respostas do fluxo conversacional
- Modelo barato (OPENAI_MODEL_CHEAP, default gpt-4o-mini):
  classificacao de intencao, deteccao de idioma, sumarizacao

Transcricao de audio via Whisper API (FR-005, SC-007).
NUNCA hardcodar OPENAI_API_KEY — lido de settings (via env/secret).

Implementacao completa: FASE 4 (task 4.1/4.2) e FASE 4.4 (transcricao).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class OpenAIClient:
    """
    Wrapper em torno do cliente OpenAI oficial.
    Instancia unica (singleton) com key da config.
    STUB: implementacao completa em FASE 4.
    """

    def __init__(self, api_key: str, model_reasoning: str, model_cheap: str):
        # TODO (FASE 4): instanciar openai.AsyncOpenAI(api_key=api_key)
        self._model_reasoning = model_reasoning
        self._model_cheap = model_cheap
        logger.info(
            "OpenAIClient inicializado: reasoning=%s cheap=%s",
            model_reasoning, model_cheap,
        )

    async def chat_reasoning(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> str:
        """Gera resposta usando o modelo de raciocinio."""
        # TODO (FASE 4): chamar client.chat.completions.create com model_reasoning
        raise NotImplementedError("chat_reasoning implementado em FASE 4")

    async def chat_cheap(
        self,
        messages: list[dict],
        max_tokens: int = 256,
        temperature: float = 0.0,
    ) -> str:
        """Classifica/sumariza usando o modelo barato."""
        # TODO (FASE 4): chamar client.chat.completions.create com model_cheap
        raise NotImplementedError("chat_cheap implementado em FASE 4")

    async def transcribe_audio(self, audio_bytes: bytes, filename: str = "audio.ogg") -> str:
        """
        Transcreve audio (Whisper API).
        Falha de transcricao propaga excecao; caller pede ao lead para repetir em texto.
        """
        # TODO (FASE 4.4): client.audio.transcriptions.create
        raise NotImplementedError("transcribe_audio implementado em FASE 4.4")
