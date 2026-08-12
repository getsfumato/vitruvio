"""Classifying what a query is asking for, deterministically.

The protocol says a query is declarative: it names no index, and choosing one is the implementation's job. That
choice starts here -- with what *shape* the query has -- and the classification must be a pure function, because a
planner whose plans depend on a model's mood is a planner nobody can reason about.

The single best feature is **out-of-vocabulary ratio**: the fraction of query terms the inverted index has never
seen. It is free -- it falls straight out of the document frequencies the index already computed -- and it is a fact
about *this brain* rather than a guess about language. A query whose terms have zero document frequency cannot be
answered lexically, whatever it looks like.

A ``RetrievalMode`` hint **restricts the admissible plan space and never selects a plan**. In particular ``SEMANTIC``
requires the vector generator if one is available but still admits the lexical one: a hint may not be used to
violate "no single index is authoritative".
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from boltzmann.query.request import RetrievalMode

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
"""What an exact identity looks like. Anchored, so a query that merely mentions a digest is not one."""

INTERROGATIVES = frozenset(
    {"what", "which", "who", "when", "where", "why", "how", "que", "cual", "quien", "cuando", "donde", "como", "por"}
)
"""Question words in both languages. Their presence is weak evidence of natural language rather than keywords."""

NATURAL_LANGUAGE_TOKENS = 4
"""At or above this many tokens, a query reads as a sentence rather than as a keyword list."""

OOV_THRESHOLD = 0.5
"""Above this fraction of unseen terms, a lexical plan cannot answer the query whatever else it looks like."""


class IntentKind(StrEnum):
    """What shape a query has."""

    EXACT = "exact"
    """A digest, or an identity-shaped key. One answer, no ranking."""
    NAVIGATIONAL = "navigational"
    """Filters and no text. "The episodes from May" is a filter evaluation, not a relevance ranking."""
    LEXICAL = "lexical"
    """Terms the index knows. A quoted phrase, or a short keyword list."""
    SEMANTIC = "semantic"
    """Natural language, or terms the index has never seen."""
    ASSOCIATIVE = "associative"
    """A traversal: what relates to this, what cites this."""
    HYBRID = "hybrid"
    """No clear signal. Every generator gets equal weight."""


# Per-intent generator weights for fusion, and per-generator authority priors for the recall model. Authority is
# always strictly below 1.0, for every generator, in every intent -- which is precisely the claim that no single
# index can be trusted alone, written as a number the objective consumes.
PROFILES: dict[IntentKind, tuple[dict[str, float], dict[str, float], float]] = {
    IntentKind.EXACT: ({"ExactLookup": 1.0}, {"ExactLookup": 1.0}, 1.0),
    IntentKind.NAVIGATIONAL: (
        {"RangeScan": 1.0, "BitmapFilter": 1.0, "SeqScan": 1.0},
        {"SeqScan": 1.0},
        1.0,
    ),
    IntentKind.LEXICAL: (
        {"TermScan": 0.85, "VectorSearch": 0.35, "BruteVector": 0.35, "GraphExpand": 0.20},
        {"TermScan": 0.85, "VectorSearch": 0.35, "BruteVector": 0.40, "GraphExpand": 0.20, "SeqScan": 1.0},
        0.95,
    ),
    IntentKind.SEMANTIC: (
        {"VectorSearch": 0.75, "BruteVector": 0.75, "TermScan": 0.50, "GraphExpand": 0.25},
        {"VectorSearch": 0.75, "BruteVector": 0.80, "TermScan": 0.50, "GraphExpand": 0.25, "SeqScan": 1.0},
        0.85,
    ),
    IntentKind.ASSOCIATIVE: (
        {"GraphExpand": 0.60, "TermScan": 0.50, "VectorSearch": 0.50, "BruteVector": 0.50},
        {"GraphExpand": 0.60, "TermScan": 0.50, "VectorSearch": 0.50, "BruteVector": 0.55, "SeqScan": 1.0},
        0.80,
    ),
    IntentKind.HYBRID: (
        {"TermScan": 0.60, "VectorSearch": 0.60, "BruteVector": 0.60, "GraphExpand": 0.30},
        {"TermScan": 0.60, "VectorSearch": 0.60, "BruteVector": 0.65, "GraphExpand": 0.30, "SeqScan": 1.0},
        0.90,
    ),
}


@dataclass(frozen=True, slots=True)
class Intent:
    """
    What the planner decided a query is, and what that implies.

    Attributes:
        kind (IntentKind): The shape.
        weights (Mapping[str, float]): Per-generator fusion weights.
        authority (Mapping[str, float]): Per-generator coverage priors, all strictly below 1.0 except for an
            exhaustive scan and an exact lookup, which genuinely do see everything.
        recall_floor (float): The minimum expected recall an admissible plan must reach.
        features (tuple[str, ...]): Which rules fired, so a classification can be explained rather than trusted.
        out_of_vocabulary (float): Fraction of query terms no index has seen.
    """

    kind: IntentKind
    weights: Mapping[str, float] = field(default_factory=dict)
    authority: Mapping[str, float] = field(default_factory=dict)
    recall_floor: float = 0.9
    features: tuple[str, ...] = ()
    out_of_vocabulary: float = 0.0

    @property
    def is_exact(self) -> bool:
        """Whether this is an identity lookup, which is exempt from the two-generator rule."""
        return self.kind is IntentKind.EXACT


def classify(
    text: str,
    *,
    mode: RetrievalMode = RetrievalMode.AUTO,
    has_filters: bool = False,
    expand_depth: int = 0,
    out_of_vocabulary: float = 0.0,
    known_predicates: frozenset[str] = frozenset(),
) -> Intent:
    """
    Decide what a query is asking for.

    Deterministic: no model, no network, no library whose behaviour changes between releases. Every branch is a
    property of the query text, the filters, or statistics the indices already computed.

    Args:
        text (str): The query.
        mode (RetrievalMode): The caller's hint. A mode other than ``AUTO`` pins the kind, because the caller has
            told us the shape -- but it still only *restricts* which plans are admissible, never selects one.
        has_filters (bool): Whether any filter was given.
        expand_depth (int): Requested graph expansion.
        out_of_vocabulary (float): Fraction of terms the inverted index has never seen.
        known_predicates (frozenset[str]): Relation predicates present in the graph, so a query naming one reads as
            a traversal.

    Returns:
        Intent: The classification, with the weights, priors and floor it implies.
    """
    stripped = text.strip()
    tokens = stripped.lower().split()
    features: list[str] = []

    kind = _kind_for_mode(mode)
    if kind is None:
        if DIGEST.match(stripped):
            kind, features = IntentKind.EXACT, ["text parses as a block identity"]
        elif not stripped and has_filters:
            kind, features = IntentKind.NAVIGATIONAL, ["filters with no text: a filter evaluation, not a ranking"]
        elif expand_depth > 0:
            kind, features = IntentKind.ASSOCIATIVE, [f"expand_depth={expand_depth}"]
        elif any(token in known_predicates for token in tokens):
            kind, features = IntentKind.ASSOCIATIVE, ["a token names a relation predicate this graph holds"]
        elif out_of_vocabulary > OOV_THRESHOLD:
            kind = IntentKind.SEMANTIC
            features = [f"out-of-vocabulary ratio {out_of_vocabulary:.2f}: not answerable lexically"]
        elif len(tokens) >= NATURAL_LANGUAGE_TOKENS or any(token in INTERROGATIVES for token in tokens):
            kind = IntentKind.SEMANTIC
            features = ["reads as a sentence rather than a keyword list"]
        elif tokens:
            kind, features = IntentKind.LEXICAL, ["a short keyword list whose terms the index knows"]
        else:
            kind, features = IntentKind.HYBRID, ["no text and no filters"]
    else:
        features = [f"mode={mode.value} was requested"]

    weights, authority, floor = PROFILES[kind]
    return Intent(
        kind=kind,
        weights=weights,
        authority=authority,
        recall_floor=floor,
        features=tuple(features),
        out_of_vocabulary=out_of_vocabulary,
    )


def _kind_for_mode(mode: RetrievalMode) -> IntentKind | None:
    """The intent a non-auto mode pins, or ``None`` for ``AUTO``."""
    return {
        RetrievalMode.EXACT: IntentKind.EXACT,
        RetrievalMode.LEXICAL: IntentKind.LEXICAL,
        RetrievalMode.SEMANTIC: IntentKind.SEMANTIC,
        RetrievalMode.ASSOCIATIVE: IntentKind.ASSOCIATIVE,
    }.get(mode)


def admissible_generators(mode: RetrievalMode) -> frozenset[str] | None:
    """
    Which generators a mode permits, or ``None`` for no restriction.

    Note what ``SEMANTIC`` does **not** do: it does not exclude the lexical generator. Requiring the vector index is
    a legitimate hint; forbidding a second opinion would let a hint override the protocol's own invariant, and a
    hint does not get to do that.

    Args:
        mode (RetrievalMode): The caller's hint.

    Returns:
        frozenset[str] | None: Permitted operator names, or ``None``.
    """
    if mode is RetrievalMode.EXACT:
        return frozenset({"ExactLookup"})
    if mode is RetrievalMode.LEXICAL:
        # The vector generator is excluded here, which is the one case a hint may narrow the space: the caller has
        # said "match words", and a semantic neighbour is not what was asked for.
        return frozenset({"TermScan", "SeqScan", "GraphExpand"})
    return None


def requires(mode: RetrievalMode) -> frozenset[str]:
    """
    Which operators a mode makes mandatory when available.

    Args:
        mode (RetrievalMode): The caller's hint.

    Returns:
        frozenset[str]: Operator names that must appear.
    """
    if mode is RetrievalMode.SEMANTIC:
        return frozenset({"VectorSearch", "BruteVector"})
    if mode is RetrievalMode.ASSOCIATIVE:
        return frozenset({"GraphExpand"})
    return frozenset()
