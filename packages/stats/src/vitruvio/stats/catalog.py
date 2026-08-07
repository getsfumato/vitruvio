"""The statistics catalogue: what the indices measure and what the planner costs against.

Its own distribution to break a cycle. Indices *produce* fragments during the pass they are already making over
the blocks; the planner *consumes* them to estimate selectivity, cardinality and recall. Neither should import
the other, so the vocabulary they share lives here.

## Freshness is two-level, and both levels are necessary

``Index.build`` receives the blocks and *not* the module's ``MerkleRoot``, so a fragment cannot stamp itself with
the version it describes. The planner can, because it holds the module -- so the root is stamped at assembly
time. That leaves one case the root cannot catch:

**A redaction destroys a block's bytes without changing the composition.** The root is unchanged, membership
proofs still verify, and the block is still a member -- but ``Brain._build`` now hands the index one fewer block,
because it filters to the resolvable subset. Only a fingerprint over the leaves *actually indexed* sees that.

So :class:`StatsVersion` carries both, and a mismatch on either means stale.

## Estimates are honest about being estimates

Every estimate carries a ``confidence`` and an ``exact`` flag. A bitmap index can compute a real intersection
cardinality; a range histogram interpolates. A planner that could not tell those apart would trade a measured
number against a guessed one as though they were the same, which is how a cost model quietly stops working.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CATALOG_SCHEMA = 1
"""Bumped when a statistic's meaning changes. A fragment from an older schema is stale, not wrong."""

HISTOGRAM_BUCKETS = 32
"""Equi-depth buckets per ordered key.

Equi-depth rather than equi-width because ``occurred_at`` and ``size`` are both heavily skewed: equi-width gives
unbounded relative error on exactly the ranges people ask about.
"""

TOP_VALUES = 32
"""How many most-frequent values to keep per column, for point-selectivity estimation."""


class Freshness(BaseModel):
    """
    Whether a statistics fragment still describes the module.

    Attributes:
        state (str): ``fresh``, ``stale`` or ``absent``.
        reason (str | None): Which check failed, so that a diagnosis does not require re-deriving it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: str = "absent"
    reason: str | None = None

    @property
    def is_fresh(self) -> bool:
        """Whether the fragment can be costed against."""
        return self.state == "fresh"

    @classmethod
    def fresh(cls) -> Freshness:
        """A fragment that matches the module on both root and leaf fingerprint."""
        return cls(state="fresh")

    @classmethod
    def stale(cls, reason: str) -> Freshness:
        """A fragment that describes a different composition than the one installed."""
        return cls(state="stale", reason=reason)

    @classmethod
    def absent(cls) -> Freshness:
        """No fragment at all."""
        return cls(state="absent", reason="no statistics have been built for this module")


class StatsVersion(BaseModel):
    """
    What composition a fragment describes.

    Attributes:
        root (str | None): The module's Merkle root, stamped at assembly time because ``build`` does not
            receive one.
        leaf_fingerprint (str): ``sha256`` over the sorted identities actually indexed. Catches the redaction
            case the root cannot: bytes destroyed, composition unchanged, one fewer block handed to the index.
        catalog_schema (int): Which schema the numbers follow.
        built_at (str): RFC3339, for reporting rather than for comparison.
        index_kinds (tuple[str, ...]): Which indices contributed.
        model_tag (str | None): The embedding model, when a vector index contributed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    root: str | None = None
    leaf_fingerprint: str = ""
    catalog_schema: int = CATALOG_SCHEMA
    built_at: str = ""
    index_kinds: tuple[str, ...] = ()
    model_tag: str | None = None

    def freshness_against(self, root: str, fingerprint: str) -> Freshness:
        """
        Compare this version against a module's current identity.

        Args:
            root (str): The module's Merkle root.
            fingerprint (str): The fingerprint of the module's resolvable leaves.

        Returns:
            Freshness: Fresh only when the schema, the root and the fingerprint all agree.
        """
        if self.catalog_schema != CATALOG_SCHEMA:
            return Freshness.stale(f"built under catalog schema {self.catalog_schema}, current is {CATALOG_SCHEMA}")
        if not self.leaf_fingerprint:
            return Freshness.absent()
        if self.root is not None and self.root != root:
            return Freshness.stale("the module's composition changed")
        if self.leaf_fingerprint != fingerprint:
            # The root can match while this does not: a redaction destroys bytes without changing membership.
            return Freshness.stale("the set of resolvable blocks changed, though the composition did not")
        return Freshness.fresh()


class Estimate(BaseModel):
    """
    A cardinality estimate, honest about how it was obtained.

    Attributes:
        rows (int): Expected number of blocks.
        exact (bool): Whether this was computed rather than interpolated. A bitmap intersection is exact; a
            histogram range is not, and a planner that treated them alike would trade a measured number against
            a guessed one.
        confidence (float): How much to trust an inexact estimate, in ``[0, 1]``.
        note (str | None): How it was derived, for EXPLAIN.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: int = Field(ge=0)
    exact: bool = False
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    note: str | None = None

    @classmethod
    def measured(cls, rows: int, note: str | None = None) -> Estimate:
        """An estimate that is a count."""
        return cls(rows=rows, exact=True, confidence=1.0, note=note)


class ColumnStats(BaseModel):
    """
    One indexed field's distribution.

    Attributes:
        distinct (int): Distinct values seen.
        null_count (int): Blocks where the field is absent or empty.
        total_values (int): Sum of list lengths, for multi-valued fields like tags.
        top (tuple[tuple[str, int], ...]): The most frequent values and their counts.
        truncated (bool): Whether ``distinct`` exceeded the cap, so ``top`` covers less of the domain.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    distinct: int = 0
    null_count: int = 0
    total_values: int = 0
    top: tuple[tuple[str, int], ...] = ()
    truncated: bool = False

    @property
    def average_values(self) -> float:
        """Mean values per block that has any. One for a scalar field, more for tags."""
        populated = max(1, self.total_values and self.distinct and (self.total_values // max(1, self.distinct)))
        return self.total_values / max(1, populated)

    def selectivity(self, value: str, cardinality: int) -> Estimate:
        """
        How many blocks carry one value.

        Exact for a value in ``top``; otherwise the remaining mass spread uniformly over the unseen tail, which
        is the standard assumption and the one that fails on a skewed tail. When the value is not in ``top`` and
        the column was *not* truncated, the answer is exact and it is zero -- which is worth having, because it
        lets the planner prune a whole subplan before running a generator.

        Args:
            value (str): The value being filtered on.
            cardinality (int): Blocks in the module.

        Returns:
            Estimate: The expected match count.
        """
        for candidate, count in self.top:
            if candidate == value:
                return Estimate.measured(count, note=f"most-frequent value {value!r}")
        if not self.truncated and self.distinct <= len(self.top):
            return Estimate.measured(0, note=f"{value!r} does not occur in this module")

        covered = sum(count for _, count in self.top)
        remaining = max(0, self.total_values - covered)
        unseen = max(1, self.distinct - len(self.top))
        return Estimate(
            rows=min(cardinality, max(1, remaining // unseen)),
            confidence=0.4,
            note="uniform over the unseen tail",
        )


class TimeStats(BaseModel):
    """
    The distribution of an ordered timestamp key.

    Attributes:
        minimum (str): Earliest value seen.
        maximum (str): Latest value seen.
        boundaries (tuple[str, ...]): Equi-depth bucket edges.
        counts (tuple[int, ...]): Blocks per bucket.
        timed_count (int): Blocks that carry the key **at all**.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    minimum: str = ""
    maximum: str = ""
    boundaries: tuple[str, ...] = ()
    counts: tuple[int, ...] = ()
    timed_count: int = 0

    def range_selectivity(self, low: str | None, high: str | None, cardinality: int) -> Estimate:
        """
        How many blocks fall in a time window.

        Two things this must get right. Timestamps are fixed-width RFC3339 in UTC, so lexicographic order *is*
        chronological order and no parsing is needed. And a block with no timestamp **cannot satisfy a window**
        -- that is the SDK's own filter rule -- so the estimate is scaled by the fraction of blocks that carry
        one. Omitting that factor over-estimates by exactly the untimed fraction, which on a mixed module is
        large.

        Args:
            low (str | None): Inclusive lower bound.
            high (str | None): Inclusive upper bound.
            cardinality (int): Blocks in the module.

        Returns:
            Estimate: The expected match count.
        """
        if not self.timed_count or not self.counts:
            return Estimate(rows=0, exact=True, confidence=1.0, note="no block in this module carries a timestamp")
        if low and self.maximum and low > self.maximum:
            return Estimate.measured(0, note="the window starts after the last timestamp")
        if high and self.minimum and high < self.minimum:
            return Estimate.measured(0, note="the window ends before the first timestamp")

        matched = 0
        for index, count in enumerate(self.counts):
            start = self.boundaries[index] if index < len(self.boundaries) else self.minimum
            end = self.boundaries[index + 1] if index + 1 < len(self.boundaries) else self.maximum
            if (high and start > high) or (low and end < low):
                continue
            fully_inside = (not low or start >= low) and (not high or end <= high)
            matched += count if fully_inside else count // 2

        return Estimate(
            rows=min(cardinality, matched),
            confidence=0.8,
            note=f"equi-depth interpolation over {len(self.counts)} buckets, scaled by timed blocks",
        )


class TermStats(BaseModel):
    """
    The lexical index's view of the vocabulary.

    Attributes:
        doc_count (int): Blocks with any indexed text.
        vocabulary (int): Distinct terms.
        average_length (float): Mean terms per block, for BM25's length normalisation.
        document_frequency (dict[str, int]): How many blocks contain each term, for the head of the
            distribution.
        postings (int): Total postings, for costing a scan.
        tail_max_frequency (int): The largest ``df`` among terms *not* listed, so an unlisted term can be
            bounded rather than guessed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_count: int = 0
    vocabulary: int = 0
    average_length: float = 0.0
    document_frequency: dict[str, int] = Field(default_factory=dict)
    postings: int = 0
    tail_max_frequency: int = 0

    def frequency(self, term: str) -> int:
        """
        How many blocks contain a term.

        Returns ``0`` for a term the index has never seen, which is the single most useful signal the planner
        has: a query whose terms have zero document frequency cannot be answered lexically, and that is a fact
        about *this brain* rather than a guess about language.

        Args:
            term (str): The analysed term.

        Returns:
            int: The document frequency, or a bound for an unlisted term.
        """
        if term in self.document_frequency:
            return self.document_frequency[term]
        if self.vocabulary > len(self.document_frequency):
            # Listed exactly for the head; bounded for the tail. A bound is honest and still useful.
            return self.tail_max_frequency
        return 0

    def out_of_vocabulary_ratio(self, terms: tuple[str, ...]) -> float:
        """
        The fraction of query terms this index has never seen.

        The best intent feature available, and free -- it falls straight out of ``document_frequency``.

        Args:
            terms (tuple[str, ...]): The analysed query terms.

        Returns:
            float: In ``[0, 1]``. Zero when there are no terms.
        """
        if not terms:
            return 0.0
        unseen = sum(1 for term in terms if self.frequency(term) == 0)
        return unseen / len(terms)


class GraphStats(BaseModel):
    """
    The shape of the relation graph, which is what makes expansion costable.

    Attributes:
        nodes (int): Distinct block identities mentioned, including targets outside the module.
        edges (int): Directed edges.
        external_nodes (int): Targets that are not members of this module. A citation pointing outside an
            install is information, not an error.
        out_degree_mean (float): Mean out-degree.
        out_degree_max (int): Largest out-degree, so a hub is visible before an expansion hits it.
        predicates (dict[str, int]): Edge counts per predicate.
        reach_by_depth (tuple[float, ...]): **Measured** mean frontier size at depths 1..n.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    nodes: int = 0
    edges: int = 0
    external_nodes: int = 0
    out_degree_mean: float = 0.0
    out_degree_max: int = 0
    predicates: dict[str, int] = Field(default_factory=dict)
    reach_by_depth: tuple[float, ...] = ()

    @property
    def density(self) -> float:
        """Edges per node. Derived rather than stored, so there is one source for it."""
        return self.edges / max(1, self.nodes)

    def expansion(self, seeds: int, depth: int, cardinality: int) -> Estimate:
        """
        How many blocks an expansion would reach.

        Uses the **measured** frontier growth rather than ``mean_degree ** depth``. That formula is wrong by
        orders of magnitude in a real knowledge graph, because frontiers overlap heavily -- concepts cite the
        same evidence -- and measuring the overlap at build time is cheap.

        Args:
            seeds (int): Blocks the expansion starts from.
            depth (int): How many hops.
            cardinality (int): Blocks in the module, as a ceiling.

        Returns:
            Estimate: The expected reached count.
        """
        if depth <= 0 or not seeds or not self.edges:
            return Estimate.measured(0, note="no expansion")
        if depth <= len(self.reach_by_depth):
            reach = self.reach_by_depth[depth - 1]
            return Estimate(
                rows=min(cardinality, int(seeds * reach)),
                confidence=0.75,
                note=f"measured mean reach {reach:.1f} at depth {depth}",
            )
        # Beyond what was measured, fall back to the branching product and say so: an unmeasured depth is a
        # guess, and it should be labelled as one rather than dressed as an interpolation.
        estimated = seeds * max(1.0, self.out_degree_mean) ** depth
        return Estimate(
            rows=min(cardinality, int(estimated)),
            confidence=0.25,
            note=f"branching product beyond measured depth {len(self.reach_by_depth)}; frontiers overlap, so this over-estimates",
        )


class VectorStats(BaseModel):
    """
    The vector index's shape and its measured recall behaviour.

    Attributes:
        vectors (int): Vectors held, which exceeds block count when documents are chunked.
        blocks (int): Distinct blocks represented.
        dimensions (int): Vector width.
        metric (str): Distance metric.
        model_tag (str): The composite tag of the model that produced them.
        removed_fraction (float): Share of slots vacated by removals, for deciding when to compact.
        recall_curve (tuple[tuple[int, float], ...]): ``(effort, measured recall@10)`` pairs, measured at build
            time against exact search. Without this, "recall" in a cost objective is a made-up number.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    vectors: int = 0
    blocks: int = 0
    dimensions: int = 0
    metric: str = "cos"
    model_tag: str = ""
    removed_fraction: float = 0.0
    recall_curve: tuple[tuple[int, float], ...] = ()

    def recall_at(self, effort: int) -> float:
        """
        Expected recall at a given search effort, interpolated from the measured curve.

        Args:
            effort (int): The HNSW ``ef_search`` the plan would use.

        Returns:
            float: Expected recall in ``[0, 1]``. Pessimistic (``0.8``) when nothing was measured, because
            assuming perfect recall from an approximate index is how a planner talks itself into a bad plan.
        """
        if not self.recall_curve:
            return 0.8
        points = sorted(self.recall_curve)
        if effort <= points[0][0]:
            return points[0][1]
        if effort >= points[-1][0]:
            return points[-1][1]
        for (low_effort, low_recall), (high_effort, high_recall) in pairwise(points):
            if low_effort <= effort <= high_effort:
                span = high_effort - low_effort
                weight = 0.0 if span == 0 else (effort - low_effort) / span
                return low_recall + weight * (high_recall - low_recall)
        return points[-1][1]


class ModuleStats(BaseModel):
    """
    Everything the planner knows about one module.

    Attributes:
        memory_type (str): Which module.
        version (StatsVersion): What composition this describes.
        freshness (Freshness): Whether it still does.
        cardinality (int): Blocks in the composition.
        resolvable_count (int): Blocks whose bytes can actually be read.
        average_block_bytes (float): Mean serialized size, for costing a resolve.
        columns (dict[str, ColumnStats]): Per-field distributions.
        time (dict[str, TimeStats]): Per-ordered-key distributions.
        terms (TermStats): The lexical view.
        graph (GraphStats): The relation view.
        vectors (dict[str, VectorStats]): Per-space vector views.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_type: str
    version: StatsVersion = StatsVersion()
    freshness: Freshness = Freshness.absent()
    cardinality: int = 0
    resolvable_count: int = 0
    average_block_bytes: float = 0.0
    columns: dict[str, ColumnStats] = Field(default_factory=dict)
    time: dict[str, TimeStats] = Field(default_factory=dict)
    terms: TermStats = TermStats()
    graph: GraphStats = GraphStats()
    vectors: dict[str, VectorStats] = Field(default_factory=dict)

    def column(self, field: str) -> ColumnStats:
        """The distribution of one field, empty when it was never indexed."""
        return self.columns.get(field, ColumnStats())

    def summary(self) -> dict[str, Any]:
        """
        A compact rendering for ``index stats`` and for EXPLAIN.

        Returns:
            dict[str, Any]: The numbers a human reads first.
        """
        return {
            "memory_type": self.memory_type,
            "freshness": self.freshness.state,
            "reason": self.freshness.reason,
            "cardinality": self.cardinality,
            "resolvable": self.resolvable_count,
            "root": self.version.root,
            "built_at": self.version.built_at or None,
            "indices": list(self.version.index_kinds),
            "vocabulary": self.terms.vocabulary,
            "postings": self.terms.postings,
            "graph_edges": self.graph.edges,
            "vectors": {space: view.vectors for space, view in self.vectors.items()},
            "columns": {name: column.distinct for name, column in sorted(self.columns.items())},
        }
