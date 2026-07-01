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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel

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

    async def chat_reasoning_json(
        self,
        messages: list[dict],
        response_model: type["BaseModel"],
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> str:
        """
        Gera resposta ESTRUTURADA (Pilar 6, FR-002/FR-003) usando o modelo de
        raciocinio via `response_format=json_schema` (Structured Outputs).

        Retorna o JSON bruto (string) — validacao/parsing contra o modelo
        Pydantic e responsabilidade do chamador (`GroundedResponder.generate()`),
        que trata payload malformado com 1 retry antes de cair para handoff
        (nunca conteudo improvisado).
        """
        schema = response_model.model_json_schema()
        # Structured Outputs (modo strict) exige todo campo em "required" e
        # additionalProperties=False — jah garantido por extra="forbid", mas
        # reforcado aqui defensivamente.
        schema["additionalProperties"] = False
        schema["required"] = list(schema.get("properties", {}).keys())
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": response_model.__name__,
                "schema": schema,
                "strict": True,
            },
        }
        response = await self._client.chat.completions.create(
            model=self._model_reasoning,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
        )
        content = response.choices[0].message.content or ""
        logger.debug(
            "chat_reasoning_json: model=%s schema=%s tokens_in=%s tokens_out=%s",
            self._model_reasoning,
            response_model.__name__,
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

    async def chat_cheap_json(
        self,
        messages: list[dict],
        response_model: type["BaseModel"],
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> str:
        """
        Gera resposta ESTRUTURADA usando o modelo BARATO (gpt-4o-mini) via
        `response_format=json_schema` (Structured Outputs) — usado pelo
        `FidelityGate.verificar()` (Pilar 7, FR-009) e por `SlotExtractor`
        (Pilar 8, FR-014).

        Mesma abordagem de `chat_reasoning_json`, mas roteada ao modelo
        barato (custo/latencia menores; classificacao/verificacao, nunca
        redacao — FR-001/FR-005 do routing).

        Retorna o JSON bruto (string); validacao/parsing contra o modelo
        Pydantic e responsabilidade do chamador.
        """
        schema = response_model.model_json_schema()
        schema["additionalProperties"] = False
        schema["required"] = list(schema.get("properties", {}).keys())
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": response_model.__name__,
                "schema": schema,
                "strict": True,
            },
        }
        response = await self._client.chat.completions.create(
            model=self._model_cheap,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
        )
        content = response.choices[0].message.content or ""
        logger.debug(
            "chat_cheap_json: model=%s schema=%s tokens_in=%s tokens_out=%s",
            self._model_cheap,
            response_model.__name__,
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
