from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Sequence
from typing import Any, Protocol

BGE_M3_MODEL_NAME = "BAAI/bge-m3"
BGE_M3_EMBEDDING_DIMENSION = 1024


class EmbeddingProviderError(RuntimeError):
    """Controlled embedding failure without runtime or cache details."""


class EmbeddingProvider(Protocol):
    model_name: str
    dimension: int

    async def embed(self, text: str) -> list[float]: ...


def validate_dense_embedding(
    values: Sequence[float],
    *,
    expected_dimension: int = BGE_M3_EMBEDDING_DIMENSION,
) -> list[float]:
    if len(values) != expected_dimension:
        raise EmbeddingProviderError("Embedding output has an invalid dimension")
    try:
        vector = [float(value) for value in values]
    except (TypeError, ValueError) as error:
        raise EmbeddingProviderError("Embedding output contains non-numeric values") from error
    if not all(math.isfinite(value) for value in vector):
        raise EmbeddingProviderError("Embedding output contains non-finite values")
    return vector


class BgeM3EmbeddingProvider:
    """Lazy sentence-transformers adapter for normalized BGE-M3 dense vectors."""

    model_name = BGE_M3_MODEL_NAME
    dimension = BGE_M3_EMBEDDING_DIMENSION

    def __init__(self, *, model_loader: Callable[[], Any] | None = None) -> None:
        self._model_loader = model_loader or self._load_sentence_transformer
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()

    @staticmethod
    def _load_sentence_transformer() -> Any:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as error:
            raise EmbeddingProviderError(
                "BGE-M3 runtime dependency is not installed; install the ai extra"
            ) from error
        try:
            return SentenceTransformer(BGE_M3_MODEL_NAME)
        except Exception as error:
            raise EmbeddingProviderError("BGE-M3 model initialization failed") from error

    async def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is None:
                try:
                    self._model = await asyncio.to_thread(self._model_loader)
                except EmbeddingProviderError:
                    raise
                except Exception as error:
                    raise EmbeddingProviderError("BGE-M3 model initialization failed") from error
        return self._model

    async def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise EmbeddingProviderError("Embedding input must not be empty")
        model = await self._get_model()

        def infer() -> Any:
            return model.encode(
                text,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )

        try:
            encoded = await asyncio.to_thread(infer)
            values = encoded.tolist() if hasattr(encoded, "tolist") else encoded
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                raise EmbeddingProviderError("Embedding output is not a dense vector")
            return validate_dense_embedding(values)
        except EmbeddingProviderError:
            raise
        except Exception as error:
            raise EmbeddingProviderError("BGE-M3 inference failed") from error
