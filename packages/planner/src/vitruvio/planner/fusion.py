"""Combining heterogeneous rankings, and rendering the score the protocol asks for.

**Weighted reciprocal-rank fusion, K=60.** The scores being combined are not comparable in principle: BM25 is
corpus-dependent, cosine lives in ``[-1, 1]``, an exact match is a point mass at 1, and graph distance is ordinal
rather than metric. Min-max normalisation is also *unstable* exactly where it matters -- a generator that returns one
candidate normalises it to 1.0 and it wins everything. RRF is scale-free, assumes nothing about the distributions,
and has one parameter whose behaviour is understood.

**Absence contributes zero**, not ``1/(K + list_length)``. The alternative rewards a document for a generator merely
*having had* a list, and with heterogeneous list lengths it systematically favours whichever generator returned
fewest candidates.

**An exact hit short-circuits.** An identity match is not a relevance judgement, so letting it compete on rank
against a similarity score would be a category error. It is placed above everything and keeps its fused component
only as a tie-break among several exact hits.

**Two decimals collapse a lot of ranking**, so the order is decided on the full float *before* rendering, with an
explicit tie-break whose last component matches the SDK's own -- so vitruvio and the SDK's scan agree on order
wherever their scores agree.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal

# Imported rather than re-declared: a second definition of the wire format's precision is a second thing to keep in
# sync, and this one is the protocol's.
from boltzmann.query.scan import SCORE_PRECISION

RRF_K = 60
"""The constant in ``1 / (K + rank)``. The conventional value: large enough that the top few ranks are close
together, small enough that rank 100 contributes almost nothing."""

EXACT_FLOOR = 1.0
"""What an exact hit is offset by, so it outranks every fused candidate regardless of their scores."""


@dataclass
class Candidate:
    """
    One block accumulating evidence from several generators.

    Attributes:
        block_id (str): What matched.
        signals (dict[str, float]): Per-generator raw score, kept for EXPLAIN rather than for ranking.
        ranks (dict[str, int]): Per-generator rank, which is what fusion actually uses.
        depth (int): Graph hops from a directly-matched block. Zero means directly matched. **Owned by**
            :meth:`contribute` -- setting it at construction is overwritten by the first contribution, because a
            candidate's depth is a property of how it was reached rather than of the object.
        exact (bool): Whether an identity lookup produced it.
        origins (tuple[str, ...]): Which operators contributed. Used to decide whether an unresolvable block may be
            returned -- reached by identity or association, yes; by a content generator, no, because that can only
            mean a stale index.
    """

    block_id: str
    signals: dict[str, float] = field(default_factory=dict)
    ranks: dict[str, int] = field(default_factory=dict)
    depth: int = 0
    exact: bool = False
    origins: tuple[str, ...] = ()

    def contribute(self, generator: str, rank: int, score: float, *, depth: int = 0) -> None:
        """
        Record one generator's opinion.

        Args:
            generator (str): Which operator.
            rank (int): One-based rank in that generator's output.
            score (float): Its own score, on its own scale.
            depth (int): Graph hops, for an expansion contribution.
        """
        if generator not in self.ranks or rank < self.ranks[generator]:
            self.ranks[generator] = rank
            self.signals[generator] = score
        # The shallowest path wins: a block reached both directly and by expansion is a direct match. Read *before*
        # `origins` is extended -- doing it after made every first contribution look like a second one, which
        # discarded the hop count and let an expanded block outrank a direct match instead of competing with it.
        self.depth = min(self.depth, depth) if self.origins else depth
        if generator not in self.origins:
            self.origins = (*self.origins, generator)


def fuse(
    candidates: Mapping[str, Candidate],
    weights: Mapping[str, float],
    *,
    k: int = RRF_K,
    decay: float = 0.5,
) -> list[tuple[Candidate, float]]:
    """
    Combine every generator's ranking into one ordered list.

    Args:
        candidates (Mapping[str, Candidate]): Accumulated evidence, keyed by block identity.
        weights (Mapping[str, float]): Per-generator weight from the intent.
        k (int): The RRF constant.
        decay (float): Multiplier per graph hop, so a distant neighbour scores below a near one. Applied to the
            fused score rather than to a single signal, because expansion competes with direct matches -- which is
            what keeps ``expand_depth`` from destroying precision.

    Returns:
        list[tuple[Candidate, float]]: Candidates and fused scores, best first, deterministically ordered.
    """
    scored: list[tuple[Candidate, float]] = []
    for candidate in candidates.values():
        total = 0.0
        for generator, rank in candidate.ranks.items():
            weight = weights.get(generator, 0.5)
            total += weight / (k + rank)
        if candidate.depth:
            total *= decay**candidate.depth
        if candidate.exact:
            total += EXACT_FLOOR
        scored.append((candidate, total))

    # Ordered on the full float, before any rendering. The final component matches the SDK's scan tie-break, so the
    # two agree on order wherever their scores agree -- which is what makes a differential test meaningful.
    scored.sort(key=lambda pair: (-pair[1], pair[0].depth, pair[0].block_id))
    return scored


def normalize(scored: Sequence[tuple[Candidate, float]]) -> list[tuple[Candidate, float]]:
    """
    Rescale fused scores so the best match is 1.0.

    RRF values live on an arbitrary scale that depends on how many generators ran, which would make a score
    meaningless to a caller comparing two queries. Rescaling is rank-preserving and gives the top match ``1.00``,
    matching the convention the SDK's own scan uses for perfect coverage.

    Args:
        scored (Sequence[tuple[Candidate, float]]): Fused output.

    Returns:
        list[tuple[Candidate, float]]: The same order, rescaled into ``[0, 1]``.
    """
    if not scored:
        return []
    top = scored[0][1]
    if top <= 0:
        return [(candidate, 0.0) for candidate, _ in scored]
    return [(candidate, min(1.0, value / top)) for candidate, value in scored]


def render(value: float) -> str:
    """
    Render a score the way the protocol carries it: a decimal string.

    ``Decimal(repr(x))`` rather than ``Decimal(x)``, so the rendering depends on the shortest round-tripping
    representation instead of on the binary tail -- the same value arrived at by two arithmetic paths then renders
    identically. ``ROUND_HALF_EVEN`` avoids the systematic upward bias that half-up accumulates across a bundle.

    The clamp also removes ``"-0.00"``, which is a valid float and an absurd score.

    Args:
        value (float): The score.

    Returns:
        str: Exactly :data:`SCORE_PRECISION` decimal places.
    """
    clamped = min(1.0, max(0.0, value))
    quantized = Decimal(repr(clamped)).quantize(Decimal(1).scaleb(-SCORE_PRECISION), rounding=ROUND_HALF_EVEN)
    return f"{quantized:.{SCORE_PRECISION}f}"


def accumulate(
    candidates: dict[str, Candidate],
    generator: str,
    hits: Iterable[tuple[str, float]],
    *,
    depth: int = 0,
    exact: bool = False,
) -> None:
    """
    Fold one generator's output into the accumulator.

    Args:
        candidates (dict[str, Candidate]): The accumulator, mutated in place.
        generator (str): Which operator produced these.
        hits (Iterable[tuple[str, float]]): Block identity and score, best first.
        depth (int): Graph hops, for an expansion.
        exact (bool): Whether these are identity matches.
    """
    for rank, (block_id, score) in enumerate(hits, start=1):
        # Depth is passed to `contribute`, not to the constructor: the constructor value would be overwritten by the
        # first contribution anyway, and having two places to set it is a trap.
        candidate = candidates.setdefault(block_id, Candidate(block_id=block_id))
        candidate.contribute(generator, rank, score, depth=depth)
        if exact:
            candidate.exact = True
