"""The embedder seam, and the two providers that need no model.

The protocol is model-agnostic and vitruvio has to be too: the vector index is the one derived structure a consumer
cannot rebuild, so *which* model produced it is data that travels, and the runtime must not assume a particular one.

Two providers ship without any optional dependency, and both are load-bearing rather than filler:

* :class:`HashingEmbedder` -- signed feature hashing. It is the **default**, so a bare ``pip install vitruvio`` can
  build a vector index, publish it, pull it and query it without downloading 2.5 GB of torch. Its model tag says
  ``hashing`` so that nothing mistakes the result for semantics, and ``ModelTag.is_semantic`` is what tools read to
  say so out loud.
* :class:`FakeEmbedder` -- derived from sha256, so the same text produces bit-identical vectors on every machine. That
  is what makes a vector-index test assert on results rather than on tolerances, and what lets a planner test control
  a neighbourhood exactly instead of hoping a real model agrees.

Real models arrive through the same seam, behind extras. Nothing above this layer knows the difference.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from vitruvio.embeddings.folding import FOLDING_VERSION, tokenize
from vitruvio.embeddings.tag import ModelTag

if TYPE_CHECKING:
    from collections.abc import Sequence

Vector = tuple[float, ...]
"""One embedding.

A tuple rather than a numpy array at the seam, so the Protocol does not force numpy on a provider that has no use for
it, and so a vector is hashable and comparable in a test. The vector index converts once, at its own edge.
"""


class Modality(StrEnum):
    """What an embedder can consume."""

    TEXT = "text"
    IMAGE = "image"


class TextRole(StrEnum):
    """Which side of a retrieval pair a string is.

    Some models -- e5 among them -- prefix a query differently from a passage, and using the wrong prefix costs real
    recall. Making the role explicit means the caller cannot forget, and the prefix identity lands in the model tag so
    a mismatch is detectable rather than mysterious.
    """

    QUERY = "query"
    PASSAGE = "passage"


@dataclass(frozen=True, slots=True)
class ImageInput:
    """
    One image to embed.

    Attributes:
        data (bytes): The encoded image.
        media_type (str): What it is.
        digest (str | None): Its content digest, when known. Used as a cache key, so the same image is never embedded
            twice across a rebuild.
    """

    data: bytes
    media_type: str
    digest: str | None = None


@runtime_checkable
class Embedder(Protocol):
    """
    Turns text, and sometimes images, into vectors.

    The contract, and each clause is something a caller depends on:

    * Output is **positionally aligned** with input.
    * Vectors are **L2-normalized** when the tag says ``l2``, so a dot product is a cosine.
    * **Batching must not change results.** A provider that batches internally must produce the same vector for a
      string whether it was alone or in a batch of a thousand -- otherwise an index's contents depend on how the build
      happened to chunk its work.
    * An over-long input is **truncated**, never silently dropped, and the truncation strategy is named in the tag.
    """

    @property
    def tag(self) -> ModelTag:
        """Everything that determines where its vectors land."""
        ...

    @property
    def dimensions(self) -> int:
        """Vector width."""
        ...

    @property
    def modalities(self) -> frozenset[Modality]:
        """What it can consume."""
        ...

    @property
    def available(self) -> bool:
        """Whether it can actually run -- extras installed, credentials present, weights cached."""
        ...

    def embed_text(self, texts: Sequence[str], *, role: TextRole = TextRole.PASSAGE) -> list[Vector]:
        """Embed strings, positionally aligned with the input."""
        ...

    def embed_images(self, images: Sequence[ImageInput]) -> list[Vector]:
        """Embed images, positionally aligned with the input."""
        ...


class EmbedderUnavailableError(Exception):
    """An embedder cannot run: an extra is missing, credentials are absent, or a download failed.

    Raised rather than returning zeros. A zero vector is a *valid* vector that ranks arbitrarily, so silently
    substituting one would produce a searchable index full of meaningless neighbours -- the same class of failure as an
    empty index reporting a confident nothing.
    """


def _normalize(values: list[float]) -> Vector:
    """Scale a vector to unit length, leaving an all-zero vector alone rather than dividing by zero."""
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0.0:
        return tuple(values)
    return tuple(value / norm for value in values)


class HashingEmbedder:
    """
    Signed feature hashing over analysed tokens. No weights, no network, no torch.

    The default, and deliberately so: a bare install must be able to build a vector index, publish it, pull it and
    query it, or the travelling-index path is untested until someone downloads a model. What it produces is a
    deterministic projection of a bag of tokens -- it will match a plural to its singular through the shared analyzer
    and will not match a synonym, because there is no semantics in it at all.

    Its tag says ``hashing``, and ``ModelTag.is_semantic`` is false, which is what ``index list`` and
    ``inspect doctor`` read in order to say so plainly.

    Attributes:
        dimensions (int): Vector width. Higher reduces collisions between unrelated tokens.
    """

    PROVIDER = "hashing"

    def __init__(self, dimensions: int = 256) -> None:
        """
        Build the embedder.

        Args:
            dimensions (int): Vector width.
        """
        self._dimensions = dimensions

    @property
    def tag(self) -> ModelTag:
        """The tag, which names this as hashing rather than as a model."""
        return ModelTag(
            provider=self.PROVIDER,
            model="bow",
            # Folding is the revision: it decides what a token is, so a change to it moves every vector, exactly as a
            # model revision would. Note what is *not* in here -- the projection and the chunker, which belong to
            # whoever feeds this embedder rather than to the embedder. The vector index composes them in
            # (`VectorIndex.model_tag`), which is also why they are `none` here rather than a guess.
            revision=f"fold{FOLDING_VERSION}",
            dimensions=self._dimensions,
            dtype="f32",
            normalization="l2",
            projection="none",
            chunker="none",
        )

    @property
    def dimensions(self) -> int:
        """Vector width."""
        return self._dimensions

    @property
    def modalities(self) -> frozenset[Modality]:
        """Text only. Hashing an image's bytes would produce a vector with no relationship to its content."""
        return frozenset({Modality.TEXT})

    @property
    def available(self) -> bool:
        """Always. That is the point of it."""
        return True

    def embed_text(self, texts: Sequence[str], *, role: TextRole = TextRole.PASSAGE) -> list[Vector]:
        """
        Project each string onto a fixed number of hashed, signed features.

        The role is ignored: there is no query/passage asymmetry to model without a trained encoder, and pretending
        otherwise would put a field in the tag that changes nothing.

        Args:
            texts (Sequence[str]): Strings to embed.
            role (TextRole): Ignored.

        Returns:
            list[Vector]: Unit vectors, positionally aligned with the input.
        """
        vectors: list[Vector] = []
        for text in texts:
            values = [0.0] * self._dimensions
            for token in tokenize(text):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                bucket = int.from_bytes(digest[:4], "big") % self._dimensions
                # A sign bit from a different part of the digest, so two tokens landing in one bucket can cancel
                # instead of always reinforcing -- which is what keeps a collision from inventing similarity.
                sign = 1.0 if digest[4] & 1 else -1.0
                values[bucket] += sign
            vectors.append(_normalize(values))
        return vectors

    def embed_images(self, images: Sequence[ImageInput]) -> list[Vector]:
        """
        Not supported.

        Raises:
            EmbedderUnavailableError: Always. Hashing an image's bytes gives a vector unrelated to its content, and a
                meaningless vector that ranks is worse than an error.
        """
        raise EmbedderUnavailableError(
            "the hashing embedder cannot embed images: install vitruvio[vision] for a model that can"
        )


class FakeEmbedder:
    """
    Deterministic vectors derived from sha256. For tests, and it ships.

    Bit-identical on every machine and every Python version, which is what lets a vector-index test assert on exact
    results rather than on tolerances, and lets a planner test control a neighbourhood precisely instead of hoping a
    real model agrees about what is similar.

    Attributes:
        dimensions (int): Vector width.
        neighbourhoods (dict[str, str]): Optional grouping. Any two strings mapped to the same group get the *same*
            vector, which is how a test states "these are synonyms" as a fact instead of a hope.
    """

    PROVIDER = "fake"

    def __init__(self, dimensions: int = 32, neighbourhoods: dict[str, str] | None = None) -> None:
        """
        Build the embedder.

        Args:
            dimensions (int): Vector width.
            neighbourhoods (dict[str, str] | None): Text to group name, for scripting similarity.
        """
        self._dimensions = dimensions
        self.neighbourhoods = neighbourhoods or {}

    @property
    def tag(self) -> ModelTag:
        """The tag, naming this as fake so a real result can never be confused with a scripted one."""
        return ModelTag(
            provider=self.PROVIDER,
            model="deterministic",
            revision="1",
            dimensions=self._dimensions,
            dtype="f32",
            normalization="l2",
        )

    @property
    def dimensions(self) -> int:
        """Vector width."""
        return self._dimensions

    @property
    def modalities(self) -> frozenset[Modality]:
        """Both, so the vision path is testable without a model."""
        return frozenset({Modality.TEXT, Modality.IMAGE})

    @property
    def available(self) -> bool:
        """Always."""
        return True

    def _vector(self, seed: str) -> Vector:
        """A unit vector determined entirely by a string."""
        values: list[float] = []
        counter = 0
        while len(values) < self._dimensions:
            digest = hashlib.sha256(f"{seed}#{counter}".encode()).digest()
            # Unpacked as signed 16-bit integers and scaled: integer-derived floats are exactly representable, so two
            # machines produce identical bytes rather than identical-to-within-epsilon ones.
            for value in struct.unpack(">16h", digest[:32]):
                values.append(value / 32768.0)
                if len(values) == self._dimensions:
                    break
            counter += 1
        return _normalize(values)

    def embed_text(self, texts: Sequence[str], *, role: TextRole = TextRole.PASSAGE) -> list[Vector]:
        """
        Embed strings deterministically, collapsing any scripted neighbourhood.

        Args:
            texts (Sequence[str]): Strings to embed.
            role (TextRole): Ignored, so a query and a passage of the same text land in the same place.

        Returns:
            list[Vector]: Unit vectors.
        """
        return [self._vector(self.neighbourhoods.get(text, text)) for text in texts]

    def embed_images(self, images: Sequence[ImageInput]) -> list[Vector]:
        """
        Embed images from their bytes, deterministically.

        Args:
            images (Sequence[ImageInput]): Images to embed.

        Returns:
            list[Vector]: Unit vectors.
        """
        return [self._vector(image.digest or hashlib.sha256(image.data).hexdigest()) for image in images]
