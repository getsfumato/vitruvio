"""The optional local runtime is lazy and keeps one model resident."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from vitruvio.embeddings.base import TextRole
from vitruvio.embeddings.local_st import SentenceTransformerEmbedder, _load
from vitruvio.kernel import EmbedderSpec


def test_sentence_transformer_loads_once_and_uses_query_document_roles(monkeypatch) -> None:
    created: list[object] = []

    class Model:
        def __init__(self, name: str, *, device: str | None) -> None:
            created.append((name, device))

        def get_sentence_embedding_dimension(self) -> int:
            return 2

        def encode_query(self, texts, *, batch_size):
            return [[1.0, 0.0] for _ in texts]

        def encode_document(self, texts, *, batch_size):
            return [[0.0, 1.0] for _ in texts]

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Model))
    _load.cache_clear()
    spec = EmbedderSpec(provider="local-st", model="example/model", dims=2)
    first = SentenceTransformerEmbedder(spec)
    second = SentenceTransformerEmbedder(spec)
    assert len(created) == 1
    assert first.model is second.model
    assert first.embed_text(["hello"], role=TextRole.QUERY) == [(1.0, 0.0)]
    assert first.embed_text(["hello"], role=TextRole.PASSAGE) == [(0.0, 1.0)]
    _load.cache_clear()
