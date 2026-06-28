"""
Testes de transcricao de audio (task 4.4.3).

Cenarios:
- audio opus → texto processado via Whisper (mock)
- Falha de transcricao → excecao propagada (caller deve pedir para repetir em texto)
- chat_reasoning/chat_cheap retornam texto da resposta
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub do modulo openai para testes sem a dependencia real
# ---------------------------------------------------------------------------

def _make_fake_openai_module(
    transcript_text: str = "Texto transcrito.",
    transcript_should_raise: bool = False,
    chat_content: str = "Resposta mock.",
) -> ModuleType:
    """Cria um modulo openai falso para usar em testes."""
    fake_mod = ModuleType("openai")

    class FakeTranscriptions:
        async def create(self, model, file):
            if transcript_should_raise:
                raise RuntimeError("Whisper API error simulada")
            r = MagicMock()
            r.text = transcript_text
            return r

    class FakeAudio:
        transcriptions = FakeTranscriptions()

    class FakeChoice:
        def __init__(self, content):
            self.message = MagicMock()
            self.message.content = content

    class FakeCompletions:
        def __init__(self, content):
            self._content = content

        async def create(self, **kwargs):
            r = MagicMock()
            r.choices = [FakeChoice(self._content)]
            r.usage = None
            return r

    class FakeChat:
        def __init__(self, content):
            self.completions = FakeCompletions(content)

    class FakeAsyncOpenAI:
        def __init__(self, api_key: str = ""):
            self.audio = FakeAudio()
            self.chat = FakeChat(chat_content)

    fake_mod.AsyncOpenAI = FakeAsyncOpenAI  # type: ignore
    return fake_mod


# ---------------------------------------------------------------------------
# Fixture: injeta fake openai no sys.modules antes dos testes
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_openai_module(monkeypatch):
    """Remove importacao real do openai e injeta stub."""
    fake = _make_fake_openai_module()
    monkeypatch.setitem(sys.modules, "openai", fake)
    # Garantir que o modulo client seja reimportado limpo a cada teste
    monkeypatch.delitem(sys.modules, "app.integrations.openai_client", raising=False)
    yield
    monkeypatch.delitem(sys.modules, "app.integrations.openai_client", raising=False)


# ---------------------------------------------------------------------------
# Testes de transcricao de audio
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transcricao_audio_opus_retorna_texto():
    """Audio bytes → texto transcrito."""
    expected_text = "Quero saber sobre o curso online."
    fake_mod = _make_fake_openai_module(transcript_text=expected_text)

    import sys
    sys.modules["openai"] = fake_mod
    sys.modules.pop("app.integrations.openai_client", None)

    from app.integrations.openai_client import OpenAIClient

    client = OpenAIClient(
        api_key="test-key",
        model_reasoning="gpt-4o",
        model_cheap="gpt-4o-mini",
    )

    fake_audio = b"\x00\x01\x02\x03"
    texto = await client.transcribe_audio(fake_audio, filename="audio.ogg")
    assert texto == expected_text


@pytest.mark.asyncio
async def test_transcricao_falha_propaga_excecao():
    """Falha de transcricao propaga excecao; caller deve pedir para repetir."""
    fake_mod = _make_fake_openai_module(transcript_should_raise=True)

    import sys
    sys.modules["openai"] = fake_mod
    sys.modules.pop("app.integrations.openai_client", None)

    from app.integrations.openai_client import OpenAIClient

    client = OpenAIClient(
        api_key="test-key",
        model_reasoning="gpt-4o",
        model_cheap="gpt-4o-mini",
    )

    with pytest.raises(RuntimeError, match="Whisper API error"):
        await client.transcribe_audio(b"\x00\x01", filename="audio.opus")


@pytest.mark.asyncio
async def test_chat_reasoning_retorna_string():
    """chat_reasoning retorna texto stripped da resposta do LLM."""
    expected = "  Resposta do modelo de raciocinio.  "
    fake_mod = _make_fake_openai_module(chat_content=expected)

    import sys
    sys.modules["openai"] = fake_mod
    sys.modules.pop("app.integrations.openai_client", None)

    from app.integrations.openai_client import OpenAIClient

    client = OpenAIClient("key", "gpt-4o", "gpt-4o-mini")
    result = await client.chat_reasoning([{"role": "user", "content": "oi"}])
    assert result == expected.strip()


@pytest.mark.asyncio
async def test_chat_cheap_retorna_string():
    """chat_cheap retorna texto stripped da resposta do LLM."""
    import json
    payload = json.dumps({"intencao": "ambigua", "idioma": "pt", "confianca": "baixa"})
    fake_mod = _make_fake_openai_module(chat_content=payload)

    import sys
    sys.modules["openai"] = fake_mod
    sys.modules.pop("app.integrations.openai_client", None)

    from app.integrations.openai_client import OpenAIClient

    client = OpenAIClient("key", "gpt-4o", "gpt-4o-mini")
    result = await client.chat_cheap([{"role": "user", "content": "teste"}])

    parsed = json.loads(result)
    assert parsed["intencao"] == "ambigua"


@pytest.mark.asyncio
async def test_transcricao_whisper_modelo_correto():
    """Transcricao usa o modelo whisper-1."""
    chamadas: list[str] = []

    class FakeTranscriptions:
        async def create(self, model, file):
            chamadas.append(model)
            r = MagicMock()
            r.text = "ok"
            return r

    fake_mod = _make_fake_openai_module()

    class FakeAudioMod:
        transcriptions = FakeTranscriptions()

    # Substituir audio
    class FakeAsyncOpenAI2:
        def __init__(self, api_key=""):
            self.audio = FakeAudioMod()
            self.chat = MagicMock()

    fake_mod.AsyncOpenAI = FakeAsyncOpenAI2  # type: ignore

    import sys
    sys.modules["openai"] = fake_mod
    sys.modules.pop("app.integrations.openai_client", None)

    from app.integrations.openai_client import OpenAIClient

    client = OpenAIClient("key", "gpt-4o", "gpt-4o-mini")
    await client.transcribe_audio(b"\x00\x01")

    assert chamadas == ["whisper-1"]
