from __future__ import annotations

import builtins
import math
from typing import Any

import pytest

from app.ai.vector_embedding import (
    BGE_M3_EMBEDDING_DIMENSION,
    BGE_M3_MODEL_NAME,
    BgeM3EmbeddingProvider,
    EmbeddingProviderError,
    validate_dense_embedding,
)


class ArrayLike:
    def __init__(self, values: list[float]) -> None:
        self.values = values

    def tolist(self) -> list[float]:
        return self.values


class RecordingModel:
    def __init__(self, values: list[float]) -> None:
        self.values = values
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def encode(self, text: str, **kwargs: Any) -> ArrayLike:
        self.calls.append((text, kwargs))
        return ArrayLike(self.values)


@pytest.mark.asyncio
async def test_bge_provider_is_lazy_and_returns_normalized_dense_vector() -> None:
    model = RecordingModel([0.25] * BGE_M3_EMBEDDING_DIMENSION)
    load_count = 0

    def load_model() -> RecordingModel:
        nonlocal load_count
        load_count += 1
        return model

    provider = BgeM3EmbeddingProvider(model_loader=load_model)
    assert provider.model_name == BGE_M3_MODEL_NAME
    assert provider.dimension == BGE_M3_EMBEDDING_DIMENSION
    assert load_count == 0

    first = await provider.embed("normalized resume text")
    second = await provider.embed("second resume")

    assert load_count == 1
    assert first == [0.25] * BGE_M3_EMBEDDING_DIMENSION
    assert second == first
    assert model.calls[0] == (
        "normalized resume text",
        {
            "normalize_embeddings": True,
            "convert_to_numpy": True,
            "show_progress_bar": False,
        },
    )


@pytest.mark.asyncio
async def test_bge_provider_rejects_empty_input_before_loading() -> None:
    loaded = False

    def load_model() -> RecordingModel:
        nonlocal loaded
        loaded = True
        return RecordingModel([0.0] * BGE_M3_EMBEDDING_DIMENSION)

    provider = BgeM3EmbeddingProvider(model_loader=load_model)
    with pytest.raises(EmbeddingProviderError, match="must not be empty"):
        await provider.embed("  \n")
    assert loaded is False


@pytest.mark.parametrize(
    "values",
    [
        [0.0] * (BGE_M3_EMBEDDING_DIMENSION - 1),
        [0.0] * (BGE_M3_EMBEDDING_DIMENSION - 1) + [math.inf],
        [0.0] * (BGE_M3_EMBEDDING_DIMENSION - 1) + [math.nan],
        [0.0] * (BGE_M3_EMBEDDING_DIMENSION - 1) + ["not-numeric"],
    ],
    ids=["wrong-dimension", "infinite", "nan", "non-numeric"],
)
def test_dense_embedding_validation_rejects_invalid_output(values: list[Any]) -> None:
    with pytest.raises(EmbeddingProviderError):
        validate_dense_embedding(values)


@pytest.mark.asyncio
async def test_bge_provider_wraps_inference_failure_without_runtime_details() -> None:
    class FailingModel:
        def encode(self, text: str, **kwargs: Any) -> list[float]:
            del text, kwargs
            raise RuntimeError("C:/secret/model-cache/private-path")

    provider = BgeM3EmbeddingProvider(model_loader=FailingModel)
    with pytest.raises(EmbeddingProviderError) as raised:
        await provider.embed("resume")
    assert str(raised.value) == "BGE-M3 inference failed"


@pytest.mark.asyncio
async def test_missing_optional_ai_runtime_has_clear_controlled_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def reject_sentence_transformers(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "sentence_transformers":
            raise ImportError
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_sentence_transformers)
    provider = BgeM3EmbeddingProvider()
    with pytest.raises(EmbeddingProviderError, match="install the ai extra"):
        await provider.embed("resume")
