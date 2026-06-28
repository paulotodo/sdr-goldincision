"""
Cliente OpenAI — chat completions + transcricao de audio.

Dois modelos configurados via env (FR-001/005, Decisao 4 research.md):
- Modelo de raciocinio (OPENAI_MODEL_REASONING, default gpt-4o):
  geracao de respostas do fluxo conversacional
- Modelo barato (OPENAI_MODEL_CHEAP, default gpt-4o-mini):
  classificacao de intencao, deteccao de idioma, sumarizacao

Transcricao de audio via Whisper API (FR-005, SC-007).
NUNCA hardcodar OPENAI_API_KEY — lido de settings (via env/secret).
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)


class OpenAIClient:
    """
    Wrapper em torno do cliente OpenAI oficial.
    Instancia unica (singleton) com key da config.
    """

    def __init__(self, api_key: str, model_reasoning: str, model_cheap: str):
        import openai  # importado aqui para nao crashar em testes sem OPENAI_API_KEY

        self._client = openai.AsyncOpenAI(api_key=api_key)
        self._model_reasoning = model_reasoning
        self._model_cheap = model_cheap
        logger.info(
            "OpenAIClient inicializado: reasoning=%s cheap=%s",
            model_reasoning,
            model_cheap,
        )

    async def chat_reasoning(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> str:
        """Gera resposta usando o modelo de raciocinio."""
        response = await self._client.chat.completions.create(
            model=self._model_reasoning,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content = response.choices[0].message.content or ""
        logger.debug(
            "chat_reasoning: model=%s tokens_in=%s tokens_out=%s",
            self._model_reasoning,
            response.usage.prompt_tokens if response.usage else "?",
            response.usage.completion_tokens if response.usage else "?",
        )
        return content.strip()

    async def chat_cheap(
        self,
        messages: list[dict],
        max_tokens: int = 256,
        temperature: float = 0.0,
    ) -> str:
        """Classifica/sumariza usando o modelo barato."""
        response = await self._client.chat.completions.create(
            model=self._model_cheap,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content = response.choices[0].message.content or ""
        logger.debug(
            "chat_cheap: model=%s tokens_in=%s tokens_out=%s",
            self._model_cheap,
            response.usage.prompt_tokens if response.usage else "?",
            response.usage.completion_tokens if response.usage else "?",
        )
        return content.strip()

    async def transcribe_audio(self, audio_bytes: bytes, filename: str = "audio.ogg") -> str:
        """
        Transcreve audio via Whisper API (FR-005, SC-007).

        Args:
            audio_bytes: conteudo binario do audio
            filename: nome do arquivo com extensao correta (ogg/mp4/wav/etc)

        Returns:
            Texto transcrito

        Raises:
            Exception: propaga falha de transcricao; caller deve pedir ao lead
                       que repita em texto (FR-005)
        """
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = filename

        transcript = await self._client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
        )
        text = transcript.text.strip()
        logger.info(
            "transcribe_audio: filename=%s chars=%s",
            filename,
            len(text),
        )
        return text
