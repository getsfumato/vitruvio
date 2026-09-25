"""Similarity inside SQL: ``about(id, 'text', min_score)`` and ``similarity(id, 'text')``.

The two functions answer a question structure cannot -- "how many facts are *about* ethics" -- and they are the only
place this package gives up exactness, so the rules are strict and the outcome says so every time:

- **A threshold, never a rank.** ``about`` admits every block whose score is at or above ``min_score``, over the whole
  population, scored exactly. Nothing is cut at a limit, so a count over it counts every block that clears the bar --
  the one thing a count over a top-k cannot do.
- **Literals only.** The text and the threshold must be written into the query. They are embedded once, before the
  database is sealed, and the scores loaded as a table; a text computed per row would mean one embedding per row and
  an answer that depended on evaluation order.
- **Approximate, and why.** Whether a block is "about" something is a judgement of the embedding model, so any query
  that uses either function comes back with ``exact: false`` and one ``approximate`` entry per text, naming the model
  that scored it and every module that could not be scored.

Scores come from somewhere this package does not reach: a :class:`Scorer` the caller hands in. The runtime implements
it over the brain's vector indices; a test implements it over a dictionary. What the engine needs from one is a score
per block for a text, and what it could not score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

ABOUT = "about"
SIMILARITY = "similarity"

SCORE_BLOCK = "__vitruvio_block"
"""The block column of a score table. Prefixed so no caller column can shadow it or be shadowed by it."""
SCORE_VALUE = "__vitruvio_score"
"""The score column of a score table."""


@dataclass(frozen=True, slots=True)
class Similarity:
    """
    Every block's similarity to one text, and what could not be scored.

    Attributes:
        scores (dict[str, float]): Block identity to a score in ``[0, 1]``, for every block that has a vector.
        models (dict[str, str]): Memory type to the model tag its scores came from.
        missing (dict[str, str]): Memory type to why its blocks have no score: no vector index, a stale one, an
            embedder that is unavailable or does not match the vectors.
    """

    scores: dict[str, float]
    models: dict[str, str] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)


class Scorer(Protocol):
    """What turns a text into a score for every block. The runtime's is backed by the brain's vector indices."""

    def similarity(self, text: str) -> Similarity:
        """Score every block against ``text``, exactly."""
        ...


def similarity_table(position: int) -> str:
    """The engine's table of scores for the ``position``-th distinct text a query names.

    Numbered rather than named for the text, which is caller data and has no business in an identifier.
    """
    return f"__vitruvio_similarity_{position}"


__all__ = ["ABOUT", "SCORE_BLOCK", "SCORE_VALUE", "SIMILARITY", "Scorer", "Similarity", "similarity_table"]
