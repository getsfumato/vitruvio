"""Text and vision embedders behind one Protocol, with a composite model tag.

The vector index is the one derived structure a client cannot rebuild on its own, because rebuilding it needs a model.
That is why it travels inside the published artifact, and why every vector carries the tag of the model that produced
it: two vectors from different models live in unrelated spaces, so the cosine between them is noise.

torch is an optional extra. A bare install still embeds -- with feature hashing, tagged as such -- so that every code
path is exercised without a 2.5 GB download, and so the travelling-index path is *tested* rather than assumed.
"""

from __future__ import annotations

from vitruvio.embeddings.base import (
    Embedder,
    EmbedderUnavailableError,
    FakeEmbedder,
    HashingEmbedder,
    ImageInput,
    Modality,
    TextRole,
    Vector,
)
from vitruvio.embeddings.cache import EmbeddingCache, MemoryCache, cache_key
from vitruvio.embeddings.registry import available, register, resolve
from vitruvio.embeddings.tag import ModelTag, explain_mismatch

__all__ = [
    "Embedder",
    "EmbedderUnavailableError",
    "EmbeddingCache",
    "FakeEmbedder",
    "HashingEmbedder",
    "ImageInput",
    "MemoryCache",
    "Modality",
    "ModelTag",
    "TextRole",
    "Vector",
    "available",
    "cache_key",
    "explain_mismatch",
    "register",
    "resolve",
]
