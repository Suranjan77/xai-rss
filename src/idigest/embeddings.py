"""CPU embeddings via fastembed (ONNX).

Deliberately *not* served through llama-server: the user can load only one model
at a time on the GPU, so embeddings run on the CPU and never compete with the
generation model. Small (~130 MB) and fast enough for daily batch use.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Sequence

from .config import load_config


@lru_cache(maxsize=1)
def _model() -> Any:
    from fastembed import TextEmbedding  # lazy: heavy import

    cfg = load_config()["embeddings"]
    return TextEmbedding(model_name=cfg["model"])


def embed(texts: Sequence[str]) -> list[list[float]]:
    """Embed a batch of texts -> list of float vectors."""
    if not texts:
        return []
    return [list(map(float, v)) for v in _model().embed(list(texts))]


def embed_one(text: str) -> list[float]:
    return embed([text])[0]
