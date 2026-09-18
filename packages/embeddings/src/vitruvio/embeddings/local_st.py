"""Sentence Transformers loaded once per process when a local runtime is selected."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from importlib import import_module
from typing import Any, cast

from vitruvio.embeddings.base import EmbedderUnavailableError, ImageInput, Modality, TextRole, Vector, _normalize
from vitruvio.embeddings.tag import UNPINNED, ModelTag
from vitruvio.kernel import EmbedderSpec


@lru_cache(maxsize=4)
def _load(model: str, device: str | None) -> Any:
    try:
        sentence_transformer = import_module("sentence_transformers").SentenceTransformer
    except ImportError as error:
        raise EmbedderUnavailableError("local-st needs sentence-transformers; install vitruvio[local]") from error
    return sentence_transformer(model, device=device)


class SentenceTransformerEmbedder:
    """A local text model whose weights remain in RAM across requests."""

    def __init__(self, spec: EmbedderSpec) -> None:
        self.spec = spec
        self.model = _load(spec.runtime_model or spec.model, spec.device)
        if not all(callable(getattr(self.model, method, None)) for method in ("encode_query", "encode_document")):
            raise EmbedderUnavailableError("local-st needs sentence-transformers>=5; upgrade vitruvio[local]")
        width = self.model.get_sentence_embedding_dimension()
        if width is None or (spec.dims is not None and spec.dims != width):
            raise EmbedderUnavailableError(f"local model width is {width}, configured dims is {spec.dims}")
        self._dimensions = cast(int, width)

    @property
    def tag(self) -> ModelTag:
        identity = self.spec.model_id or self.spec.model
        owner, separator, model = identity.partition("/")
        if not separator:
            owner, model = "sentence-transformers", identity
        return ModelTag(
            provider=owner,
            model=model,
            revision=self.spec.revision or UNPINNED,
            dimensions=self._dimensions,
            pooling="model",
            prompts="model",
            preprocess="cut24000",
        )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def modalities(self) -> frozenset[Modality]:
        return frozenset({Modality.TEXT})

    @property
    def available(self) -> bool:
        return True

    def embed_text(self, texts: Sequence[str], *, role: TextRole = TextRole.PASSAGE) -> list[Vector]:
        if not texts:
            return []
        encode = self.model.encode_query if role is TextRole.QUERY else self.model.encode_document
        vectors = encode([text[:24_000] for text in texts], batch_size=self.spec.batch or 32)
        return [_normalize([float(value) for value in vector]) for vector in vectors]

    def embed_images(self, images: Sequence[ImageInput]) -> list[Vector]:
        raise EmbedderUnavailableError("local-st embeds text only")
