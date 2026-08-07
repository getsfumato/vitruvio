"""The model tag: one string that says exactly where a vector came from.

The protocol requires a vector index to record the embedding model that produced it, and refuses a pull whose tag
does not match. So the tag is the mechanism by which a consumer avoids reading vectors from an unrelated space -- and
that makes what goes *into* it a correctness question rather than a labelling convention.

Everything that changes **where a vector lands in the space** belongs in the tag. That includes the obvious things
(provider, model, revision, dimensions) and two that are easy to leave out and must not be:

* **The projection.** The same model over different text lands in a different place. If vitruvio changes which fields
  it embeds, or how it weights them, existing vectors describe different strings than new ones would.
* **The chunker.** Different chunk boundaries mean different embedded strings, for exactly the same reason.

A consumer must refuse a mismatched tag as firmly as a different model, because the failure is identical: cosines
between the two are noise. Comparison is exact string equality -- that is what the SDK checks -- and
:meth:`ModelTag.parse` exists only so that a tool can say *which field* differs, which is the difference between a
usable error and "the tags do not match".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SEPARATOR = "|"
"""Joins the tags of several spaces in one index. A composite index carries one tag per space."""

PATTERN = re.compile(
    r"^(?P<provider>[^/]+)/(?P<model>[^@]+)@(?P<revision>[^#]*)#"
    r"d(?P<dimensions>\d+),(?P<dtype>[^,]+),(?P<normalization>[^,]+),"
    r"(?P<pooling>[^,]+),(?P<prompts>[^,]+),(?P<preprocess>[^,]+),"
    r"(?P<projection>[^,]+),(?P<chunker>[^,]+)$"
)
"""The rendered form. Structured so a mismatch can be attributed to a field rather than to the whole string."""

UNPINNED = "unpinned"
"""Revision for a provider that will not name one.

An API model whose vendor publishes no version string gets this plus the date the configuration pinned it. It is
recorded honestly rather than left blank, because "we do not know what produced these vectors" is exactly the thing a
consumer needs to be told.
"""


@dataclass(frozen=True, slots=True)
class ModelTag:
    """
    Everything that determines where a vector lands.

    Attributes:
        provider (str): Registry key, e.g. ``local-st``, ``hashing``, ``openai``.
        model (str): The model within that provider. Slashes are escaped on render, since the format uses them.
        revision (str): A commit sha, a vendor version, or :data:`UNPINNED`.
        dimensions (int): Vector width.
        dtype (str): Storage precision -- ``f32``, ``f16``, ``i8``.
        normalization (str): ``l2`` or ``none``.
        pooling (str): ``mean``, ``cls``, ``attn``, or ``none`` for a model without pooling.
        prompts (str): Prompt-template identity, e.g. ``e5-qp`` for query/passage prefixes. Changing the prefix moves
            every vector, so it belongs here.
        preprocess (str): Image preprocessing identity, or ``none``.
        projection (str): Which field-extraction policy produced the embedded text.
        chunker (str): Which chunking policy produced the embedded spans.
    """

    provider: str
    model: str
    revision: str = UNPINNED
    dimensions: int = 0
    dtype: str = "f32"
    normalization: str = "l2"
    pooling: str = "none"
    prompts: str = "none"
    preprocess: str = "none"
    projection: str = "none"
    chunker: str = "none"

    def render(self) -> str:
        """
        The single string the protocol carries.

        Returns:
            str: e.g. ``local-st/intfloat--multilingual-e5-base@a1b2c3d4#d768,f16,l2,mean,e5-qp,none,proj1,chunk1``.
        """
        model = self.model.replace("/", "--")
        return (
            f"{self.provider}/{model}@{self.revision}#"
            f"d{self.dimensions},{self.dtype},{self.normalization},"
            f"{self.pooling},{self.prompts},{self.preprocess},"
            f"{self.projection},{self.chunker}"
        )

    def __str__(self) -> str:
        """The rendered form, so a tag can be used wherever a string is expected."""
        return self.render()

    @classmethod
    def parse(cls, value: str) -> ModelTag | None:
        """
        Read a rendered tag back.

        Not used for comparison -- that is exact string equality, which is what the SDK checks. This exists so a tool
        can report *which field* differs, turning "the tags do not match" into "you have f16 vectors from revision
        a1b2c3 and the configured embedder is f32 from revision d4e5f6".

        Args:
            value (str): A rendered tag.

        Returns:
            ModelTag | None: The parsed tag, or ``None`` when it was not produced by :meth:`render` -- an older
            vitruvio, or another implementation entirely.
        """
        match = PATTERN.match(value)
        if match is None:
            return None
        fields = match.groupdict()
        return cls(
            provider=fields["provider"],
            model=fields["model"].replace("--", "/"),
            revision=fields["revision"] or UNPINNED,
            dimensions=int(fields["dimensions"]),
            dtype=fields["dtype"],
            normalization=fields["normalization"],
            pooling=fields["pooling"],
            prompts=fields["prompts"],
            preprocess=fields["preprocess"],
            projection=fields["projection"],
            chunker=fields["chunker"],
        )

    def differences(self, other: ModelTag) -> dict[str, tuple[str, str]]:
        """
        Which fields differ, for an error message a person can act on.

        Args:
            other (ModelTag): The tag to compare against.

        Returns:
            dict[str, tuple[str, str]]: Field name to this value and the other's.
        """
        differing: dict[str, tuple[str, str]] = {}
        for name in self.__slots__:
            mine, theirs = getattr(self, name), getattr(other, name)
            if mine != theirs:
                differing[name] = (str(mine), str(theirs))
        return differing

    @property
    def is_semantic(self) -> bool:
        """
        Whether these vectors carry meaning.

        ``hashing`` does not: it is a deterministic bag-of-features projection that lets a bare install exercise every
        code path without a model. Reporting that plainly is the point -- a result ranked by hashed features must not
        be mistaken for a semantic one, and this is what ``inspect doctor`` and ``index list`` read.
        """
        return self.provider not in {"hashing", "fake"}


def explain_mismatch(held: str, configured: str) -> str:
    """
    Describe why two tags disagree.

    Args:
        held (str): The tag the index was built with.
        configured (str): The tag the configured embedder produces.

    Returns:
        str: A message naming the differing fields where both tags parse, and the raw strings otherwise.
    """
    mine, theirs = ModelTag.parse(held), ModelTag.parse(configured)
    if mine is None or theirs is None:
        return f"the index holds {held!r} and the configured embedder produces {configured!r}"

    differing = mine.differences(theirs)
    if not differing:
        return "the tags differ textually but every field matches, which should be impossible"
    rendered = "; ".join(
        f"{name}: {held_value} vs {wanted}" for name, (held_value, wanted) in sorted(differing.items())
    )
    return f"the index and the configured embedder differ in {rendered}"
