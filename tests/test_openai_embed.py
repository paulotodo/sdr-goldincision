"""
Testes de `OpenAIClient.embed()` (task 2.1.2, Onda 3 — RAG hibrido).

Cenarios:
- lista de textos -> lista de vetores na mesma ordem/quantidade
- shape/dimensao do retorno (1536, text-embedding-3-small)
- lista vazia -> retorno vazio, sem chamar a API (nao-op)
- modelo de embedding correto e enviado na chamada
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

_EMBED_DIMS = 1536


def _make_fake_openai_module(dims: int = _EMBED_DIMS) -> tuple[ModuleType, list[str]]:
    """Cria um modulo `openai` falso com `embeddings.create` (SDK oficial)."""
    fake_mod = ModuleType("openai")
    chamadas_modelo: list[str] = []

    class FakeEmbeddingItem:
        def __init__(self, vetor: list[float]):
            self.embedding = vetor

    class FakeEmbeddingsResponse:
        def __init__(self, data: list["FakeEmbeddingItem"]):
            self.data = data

    class FakeEmbeddings:
        async def create(self, model: str, input: list[str]):
            chamadas_modelo.append(model)
            data = [
                FakeEmbeddingItem([float(i)] * dims) for i, _ in enumerate(input)
            ]
            return FakeEmbeddingsResponse(data)

    class FakeChat:
        completions = MagicMock()

    class FakeAsyncOpenAI:
        def __init__(self, api_key: str = ""):
            self.embeddings = FakeEmbeddings()
            self.chat = FakeChat()

    fake_mod.AsyncOpenAI = FakeAsyncOpenAI  # type: ignore
    return fake_mod, chamadas_modelo


@pytest.fixture(autouse=True)
def _clean_module_cache():
    yield
    sys.modules.pop("app.integrations.openai_client", None)


@pytest.mark.asyncio
async def test_embed_retorna_vetores_na_mesma_ordem_e_quantidade():
    """embed() com N textos retorna N vetores, na mesma ordem."""
    fake_mod, _ = _make_fake_openai_module()
    sys.modules["openai"] = fake_mod
    sys.modules.pop("app.integrations.openai_client", None)

    from app.integrations.openai_client import OpenAIClient

    client = OpenAIClient(
        api_key="test-key",
        model_reasoning="gpt-4o",
        model_cheap="gpt-4o-mini",
        model_embedding="text-embedding-3-small",
    )

    textos = ["objecao 1", "objecao 2", "faq 3"]
    vetores = await client.embed(textos)

    assert len(vetores) == len(textos)
    # ordem preservada: o item i foi gerado como [float(i)] * dims pelo fake
    assert vetores[0][0] == 0.0
    assert vetores[1][0] == 1.0
    assert vetores[2][0] == 2.0


@pytest.mark.asyncio
async def test_embed_shape_dimensao_1536():
    """Cada vetor retornado tem 1536 dimensoes (text-embedding-3-small)."""
    fake_mod, _ = _make_fake_openai_module(dims=1536)
    sys.modules["openai"] = fake_mod
    sys.modules.pop("app.integrations.openai_client", None)

    from app.integrations.openai_client import OpenAIClient

    client = OpenAIClient("key", "gpt-4o", "gpt-4o-mini")
    vetores = await client.embed(["um texto qualquer"])

    assert len(vetores) == 1
    assert len(vetores[0]) == 1536
    assert all(isinstance(x, float) for x in vetores[0])


@pytest.mark.asyncio
async def test_embed_lista_vazia_retorna_vazio_sem_chamar_api():
    """embed([]) e nao-op: retorna [] sem invocar a API (evita chamada supérflua)."""
    fake_mod, chamadas = _make_fake_openai_module()
    sys.modules["openai"] = fake_mod
    sys.modules.pop("app.integrations.openai_client", None)

    from app.integrations.openai_client import OpenAIClient

    client = OpenAIClient("key", "gpt-4o", "gpt-4o-mini")
    vetores = await client.embed([])

    assert vetores == []
    assert chamadas == []


@pytest.mark.asyncio
async def test_embed_usa_model_embedding_configurado():
    """embed() envia o RAG_EMBEDDING_MODEL configurado (default text-embedding-3-small)."""
    fake_mod, chamadas = _make_fake_openai_module()
    sys.modules["openai"] = fake_mod
    sys.modules.pop("app.integrations.openai_client", None)

    from app.integrations.openai_client import OpenAIClient

    client = OpenAIClient(
        api_key="key",
        model_reasoning="gpt-4o",
        model_cheap="gpt-4o-mini",
        model_embedding="text-embedding-3-small",
    )
    await client.embed(["x"])

    assert chamadas == ["text-embedding-3-small"]


@pytest.mark.asyncio
async def test_embed_default_model_embedding_quando_nao_especificado():
    """Sem model_embedding explicito, usa default text-embedding-3-small (nao quebra call-sites existentes)."""
    fake_mod, chamadas = _make_fake_openai_module()
    sys.modules["openai"] = fake_mod
    sys.modules.pop("app.integrations.openai_client", None)

    from app.integrations.openai_client import OpenAIClient

    client = OpenAIClient(api_key="key", model_reasoning="gpt-4o", model_cheap="gpt-4o-mini")
    await client.embed(["y"])

    assert chamadas == ["text-embedding-3-small"]
