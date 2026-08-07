"""The typed query each index kind answers.

``Index.search(query: Any, limit: int)`` leaves the query type to the implementation, which is the right call for
a protocol -- but ``Any`` inside vitruvio would mean the planner and the indices agreeing by convention. These
dataclasses are that agreement made checkable.

They are also where the planner's vocabulary and the engines' vocabulary meet, so each one is deliberately narrow:
a query shape that can express something no index can answer is a shape that will eventually be passed one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from vitruvio.indices.projection import EdgeKind, Facet, IdentityKey, OrderedKey


class Order(StrEnum):
    """Which end of an ordered scan to start from."""

    ASCENDING = "asc"
    DESCENDING = "desc"


class Combine(StrEnum):
    """How multiple values within one clause combine."""

    ANY = "any"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class IdQuery:
    """
    An exact lookup, for the hash-map index.

    Attributes:
        identities (tuple[str, ...]): Block identities to resolve directly.
        keys (tuple[tuple[IdentityKey, str], ...]): Key-and-value pairs, e.g. a label or a blob digest.
    """

    identities: tuple[str, ...] = ()
    keys: tuple[tuple[IdentityKey, str], ...] = ()

    @property
    def is_empty(self) -> bool:
        """Whether this asks for nothing, in which case the planner should not have built it."""
        return not self.identities and not self.keys


@dataclass(frozen=True, slots=True)
class RangeQuery:
    """
    An ordered scan, for the B-tree index.

    A block that does not carry the key is simply absent from the ordered array, which reproduces the SDK's rule
    that a block with no timestamp cannot satisfy a time window.

    Attributes:
        key (OrderedKey): Which ordered key to scan.
        low (str | int | None): Inclusive lower bound.
        high (str | int | None): Inclusive upper bound.
        prefix (str | None): Restrict to values starting with this, for a subject or a label.
        order (Order): Which direction to return them in.
        allow (frozenset[int] | None): Ordinals a prior filter already narrowed to.
    """

    key: OrderedKey
    low: str | int | None = None
    high: str | int | None = None
    prefix: str | None = None
    order: Order = Order.ASCENDING
    allow: frozenset[int] | None = None


@dataclass(frozen=True, slots=True)
class TermQuery:
    """
    A lexical query, for the inverted index.

    Attributes:
        terms (tuple[str, ...]): Raw query terms. The index analyses them with the *same* analyzer it indexed
            with -- one function over both sides, so drift between them is structurally impossible.
        groups (tuple[tuple[str, ...], ...]): The same terms, grouped by the token each came from. One token
            expands into several terms -- a stem per candidate language, plus the raw form -- and ``ALL`` means
            "every token appears", not "every expansion appears". Without the grouping, ``ALL`` asks for something
            no block can satisfy.
        combine (Combine): Whether every token must appear, or any.
        phrase (str | None): A phrase that must appear, evaluated positionally. A filter rather than a score
            bonus, so the ranking stays interpretable.
        language (str | None): Override the analyzer's language guess.
        allow (frozenset[int] | None): Ordinals a prior filter already narrowed to. Applied **before** scoring,
            which is what makes filter-then-score cheaper than score-then-filter.
    """

    terms: tuple[str, ...] = ()
    groups: tuple[tuple[str, ...], ...] = ()
    combine: Combine = Combine.ANY
    phrase: str | None = None
    language: str | None = None
    allow: frozenset[int] | None = None


@dataclass(frozen=True, slots=True)
class FacetClause:
    """
    One categorical predicate.

    Attributes:
        facet (Facet): Which dimension.
        values (tuple[str, ...]): Which values.
        combine (Combine): Whether the block must carry every value or any of them.
        negate (bool): Whether to exclude rather than require.
    """

    facet: Facet
    values: tuple[str, ...]
    combine: Combine = Combine.ANY
    negate: bool = False


@dataclass(frozen=True, slots=True)
class FacetQuery:
    """
    A conjunction of categorical predicates, for the bitmap index.

    Attributes:
        clauses (tuple[FacetClause, ...]): Every clause must hold.
        allow (frozenset[int] | None): Ordinals a prior filter already narrowed to.
    """

    clauses: tuple[FacetClause, ...] = ()
    allow: frozenset[int] | None = None

    @property
    def is_empty(self) -> bool:
        """Whether this constrains nothing."""
        return not self.clauses


@dataclass(frozen=True, slots=True)
class VectorQuery:
    """
    A similarity probe, for the vector index.

    Attributes:
        text (str | None): A query to embed. The index owns the embedder, so the planner never handles vectors.
        vector (tuple[float, ...] | None): A pre-computed probe, for tests and for reuse across modules.
        space (str): Which embedding space to search -- text, or the multimodal one where image vectors live.
        effort (int): HNSW ``ef_search``. Higher costs more and recalls more; the measured curve says how much.
        exact (bool): Force a brute-force scan. Below a few thousand in-mask vectors this is *both* cheaper and
            more accurate than a filtered graph walk, because a filtered walk visits a fixed number of nodes and
            only a fraction survive the filter.
        allow (frozenset[int] | None): Ordinals a prior filter already narrowed to.
    """

    text: str | None = None
    vector: tuple[float, ...] | None = None
    space: str = "text"
    effort: int = 64
    exact: bool = False
    allow: frozenset[int] | None = None


@dataclass(frozen=True, slots=True)
class TraversalQuery:
    """
    A graph expansion, for the graph index.

    Attributes:
        seeds (tuple[str, ...]): Block identities to expand from. Normally the *fused* top hits rather than one
            index's, so expansion follows consensus rather than whichever index was consulted.
        depth (int): How many hops.
        kinds (tuple[EdgeKind, ...]): Which edge kinds to follow. Empty means all.
        predicates (tuple[str, ...]): Restrict relation edges to these predicates.
        inbound (bool): Traverse the transpose -- "what cites this" rather than "what does this cite".
        decay (float): Multiplier per hop, so a distant neighbour scores below a near one.
        max_nodes (int): Ceiling, because a hub node can otherwise explode an inbound expansion.
    """

    seeds: tuple[str, ...] = ()
    depth: int = 1
    kinds: tuple[EdgeKind, ...] = ()
    predicates: tuple[str, ...] = ()
    inbound: bool = False
    decay: float = 0.5
    max_nodes: int = 512


@dataclass(frozen=True, slots=True)
class Hit:
    """
    One result from an index, before fusion.

    Attributes:
        block_id (str): What matched.
        score (float): The index's own score, on its own scale. Scales are not comparable across index kinds,
            which is why the planner fuses ranks rather than scores.
        note (str | None): Why it matched, for EXPLAIN.
    """

    block_id: str
    score: float
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Results:
    """
    What an index returns, with the one flag the planner cannot infer.

    Attributes:
        hits (tuple[Hit, ...]): Matches, best first.
        exhausted (bool): Whether the index enumerated its **whole** domain. A ``k``-limited probe did not, and
            the planner needs to know: a bundle whose candidates were cut by a generator's own limit is
            truncated even if it returned fewer matches than asked for.
        consulted (str = ""): Which index kind produced this.
    """

    hits: tuple[Hit, ...] = ()
    exhausted: bool = True
    consulted: str = ""

    def identities(self) -> tuple[str, ...]:
        """Just the block identities, in rank order."""
        return tuple(hit.block_id for hit in self.hits)


@dataclass
class BuildDelta:
    """
    What changed between what an index holds and what it was just handed.

    ``Index.build`` is a full rebuild on every commit *and* on every open, and the SDK passes the whole block set
    each time. Honouring that literally would re-index a hundred thousand blocks to add five. So each index
    diffs by identity and applies only the difference -- an internal optimisation behind an unchanged contract,
    which is exactly what the SDK's own docstring invites.

    Attributes:
        added (tuple[str, ...]): Identities present now and not before.
        removed (tuple[str, ...]): Identities held before and absent now.
        unchanged (int): How many were already indexed.
    """

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    unchanged: int = 0

    @property
    def is_noop(self) -> bool:
        """Whether nothing needs doing -- the common case on an open."""
        return not self.added and not self.removed

    @property
    def rebuilds_everything(self) -> bool:
        """
        Whether applying the delta is no cheaper than starting over.

        True when more than half the held blocks are being removed: at that point the bookkeeping of an
        incremental apply costs more than a clean build, and a clean build has no chance of leaving stale state
        behind.
        """
        held = self.unchanged + len(self.removed)
        return bool(self.removed) and len(self.removed) > held / 2

    @classmethod
    def between(cls, held: Sequence[str], incoming: Sequence[str]) -> BuildDelta:
        """
        Compute the delta between two identity sets.

        Args:
            held (Sequence[str]): What the index already has.
            incoming (Sequence[str]): What it was just handed.

        Returns:
            BuildDelta: The difference.
        """
        held_set, incoming_set = set(held), set(incoming)
        return cls(
            added=tuple(sorted(incoming_set - held_set)),
            removed=tuple(sorted(held_set - incoming_set)),
            unchanged=len(held_set & incoming_set),
        )


AnyQuery = IdQuery | RangeQuery | TermQuery | FacetQuery | VectorQuery | TraversalQuery
"""Every query shape an index may be handed."""


@dataclass(frozen=True, slots=True)
class Capability:
    """
    What one installed index can currently answer.

    The planner reads this rather than assuming: an index that is registered but empty, or stale, or whose model
    tag does not match the configured embedder, must be excluded from the plan space rather than consulted and
    believed.

    Attributes:
        kind (str): Which index kind.
        memory_type (str): Which module.
        state (str): ``ready``, ``empty``, ``stale``, ``model_mismatch`` or ``absent``.
        population (int): How many blocks it holds. Zero with ``ready`` is impossible by construction.
        detail (str | None): Why it is not ready.
        facets (tuple[str, ...]): Which facets it can filter on.
        keys (tuple[str, ...]): Which ordered keys it can scan.
        spaces (tuple[str, ...]): Which embedding spaces it holds.
    """

    kind: str
    memory_type: str
    state: str = "absent"
    population: int = 0
    detail: str | None = None
    facets: tuple[str, ...] = ()
    keys: tuple[str, ...] = ()
    spaces: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """
        Whether the planner may include this index in a plan.

        An **empty** index is treated as unusable rather than as one that matches nothing. That distinction is
        the whole reason ``population`` exists: an empty index does not announce itself, so a planner that
        consulted it would get no candidates and report a confident nothing.
        """
        return self.state == "ready" and self.population > 0
