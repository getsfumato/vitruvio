"""The ordered index: range and prefix scans over sorted arrays.

Named for the role the paper gives it -- a B+ tree -- and implemented as **sorted parallel arrays with
``bisect``**, because a B+ tree exists to make *page-level* updates cheap on disk and that reason never fires here:
a module holds 10^3 to 10^6 blocks, and the SDK re-hands the whole set on every build, so there are no incremental
page splits to amortise. In CPython a bisect over a flat list beats a hand-written tree by a wide margin, it
serializes as two sorted arrays with no pointer fixups, and it is byte-reproducible.

SQLite was rejected here, and everywhere structural, for three reasons: its file bytes are not reproducible, it
would fork the source of truth across six index kinds, and a queryable ``.sqlite`` sitting next to a brain invites
someone to query it directly and treat a derived view as the record.

One detail carries more weight than it looks. ``Timestamp`` in this protocol is fixed-width RFC3339 in UTC, so
**lexicographic order is chronological order** -- no parsing on the hot path, and a range scan is two bisects. And
a block that does not carry the key is simply absent from the array, which reproduces the SDK's rule that a block
with no timestamp cannot satisfy a time window.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Any, ClassVar

from boltzmann.indices.base import IndexKind

from vitruvio.indices.base import VitruvioIndex
from vitruvio.indices.projection import OrderedKey, Projection
from vitruvio.indices.queries import Order, RangeQuery
from vitruvio.stats import HISTOGRAM_BUCKETS, Estimate, TimeStats

RANGE_SCORE = 1.0
"""What an in-range hit scores. A range predicate is boolean; ranking belongs to the planner."""

PREFIX_CEILING = "￿"
"""Appended to turn a prefix into an upper bound. Above every assigned character, so nothing sorts past it."""

TIME_KEYS = (OrderedKey.OCCURRED_AT, OrderedKey.ENDED_AT, OrderedKey.RECORDED_AT)
"""Keys whose distribution is worth a histogram, because they are what people write range predicates over."""


class BTreeIndex(VitruvioIndex):
    """
    Range, prefix and equality scans over ordered keys.

    Attributes:
        memory_type (MemoryType): Which module this indexes.
    """

    KIND: ClassVar[IndexKind] = IndexKind.BTREE
    REBUILDABLE: ClassVar[bool] = True
    BODY_VERSION: ClassVar[int] = 1
    ENGINE: ClassVar[str] = "sorted-array"

    def _reset(self) -> None:
        """Discard every key's arrays."""
        # Per key: the sorted values, and the ordinal each belongs to. Two parallel lists rather than a list of
        # pairs, so a bisect runs over the values alone without a key function.
        self._values: dict[str, list[str]] = {}
        self._owners: dict[str, list[int]] = {}
        self._pending: dict[str, list[tuple[str, int]]] = {}

    @staticmethod
    def _encode(value: str | int) -> str:
        """
        Render a key so that string order is value order.

        Integers are zero-padded to a fixed width, because ``"10" < "9"`` lexicographically and a size range would
        otherwise return nonsense. Twenty digits covers any plausible block size and keeps the width uniform.
        """
        if isinstance(value, int):
            return f"{value:020d}"
        return value

    def _apply(self, projection: Projection) -> None:
        """Collect this block's ordered keys; sorting happens once, at the end of the build."""
        ordinal = self._table.ordinal(projection.block_id)
        if ordinal is None:
            return
        for key, value in projection.keys.items():
            self._pending.setdefault(key.value, []).append((self._encode(value), ordinal))

    def _on_build_end(self, delta: Any) -> None:
        """Sort each key's collected pairs into the parallel arrays.

        One ``sorted()`` per key at the end rather than an insertion per block: the same O(n log n) total, with a
        C-level sort instead of n Python-level list insertions.
        """
        for key, pairs in self._pending.items():
            pairs.sort()
            self._values[key] = [value for value, _ in pairs]
            self._owners[key] = [ordinal for _, ordinal in pairs]
        self._pending.clear()

    def _capability_extra(self) -> dict[str, Any]:
        """Which ordered keys can be scanned."""
        return {"keys": tuple(sorted(key for key, values in self._values.items() if values))}

    def _fragment_extra(self) -> dict[str, Any]:
        """
        Equi-depth histograms for the time-shaped keys.

        Equi-depth rather than equi-width because ``occurred_at`` is heavily skewed -- a course's episodes cluster
        in term time -- and equi-width gives unbounded relative error on exactly the windows people ask about.

        ``timed_count`` is carried separately from the module cardinality, because scaling a range estimate by the
        fraction of blocks that carry a timestamp at all is what keeps it honest on a mixed module.
        """
        histograms: dict[str, TimeStats] = {}
        for key in TIME_KEYS:
            values = self._values.get(key.value)
            if not values:
                continue
            count = len(values)
            per_bucket = max(1, count // HISTOGRAM_BUCKETS)
            boundaries: list[str] = []
            counts: list[int] = []
            for start in range(0, count, per_bucket):
                chunk = values[start : start + per_bucket]
                boundaries.append(chunk[0])
                counts.append(len(chunk))
            boundaries.append(values[-1])
            histograms[key.value] = TimeStats(
                minimum=values[0],
                maximum=values[-1],
                boundaries=tuple(boundaries),
                counts=tuple(counts),
                timed_count=count,
            )
        return {"time": histograms}

    def _header_extra(self) -> dict[str, Any]:
        """How many entries each key holds."""
        return {"keys": {key: len(values) for key, values in sorted(self._values.items()) if values}}

    def _dump_state(self) -> dict[str, Any]:
        """The parallel arrays, already sorted."""
        return {
            "values": {key: list(values) for key, values in sorted(self._values.items())},
            "owners": {key: list(owners) for key, owners in sorted(self._owners.items())},
        }

    def _load_body(self, body: dict[str, Any]) -> None:
        """Restore the arrays."""
        self._reset()
        self._values = {key: list(values) for key, values in body.get("values", {}).items()}
        self._owners = {key: list(owners) for key, owners in body.get("owners", {}).items()}

    # --- Query ----------------------------------------------------------------

    def scan(self, query: RangeQuery) -> list[int]:
        """
        The ordinals whose key falls in a range.

        Args:
            query (RangeQuery): The bounds.

        Returns:
            list[int]: Matching ordinals, in the requested order. Empty when the key was never indexed, which is
            correct: a time range over semantic memory matches nothing, because a concept has no timestamp.
        """
        values = self._values.get(query.key.value)
        owners = self._owners.get(query.key.value)
        if not values or not owners:
            return []

        low: str | None = None if query.low is None else self._encode(query.low)
        high: str | None = None if query.high is None else self._encode(query.high)
        if query.prefix:
            low = query.prefix if low is None else max(low, query.prefix)
            ceiling = query.prefix + PREFIX_CEILING
            high = ceiling if high is None else min(high, ceiling)

        start = 0 if low is None else bisect_left(values, low)
        end = len(values) if high is None else bisect_right(values, high)
        if start >= end:
            return []

        selected = owners[start:end]
        if query.allow is not None:
            allowed = set(query.allow)
            selected = [ordinal for ordinal in selected if ordinal in allowed]
        if query.order is Order.DESCENDING:
            selected = list(reversed(selected))
        return selected

    def estimate(self, query: RangeQuery) -> Estimate:
        """
        How many blocks a range would match.

        Exact, because the arrays are held in memory and two bisects cost microseconds -- there is no reason to
        interpolate something that can be counted. The histogram in the statistics fragment exists for the
        planner's *offline* costing, where the index may not be loaded.

        Args:
            query (RangeQuery): The bounds.

        Returns:
            Estimate: A measured count.
        """
        return Estimate.measured(len(self.scan(query)), note=f"bisect over {query.key.value}")

    def search(self, query: Any, limit: int = 10) -> list[tuple[Any, float]]:
        """
        The SDK's entry point.

        Args:
            query (Any): A :class:`~vitruvio.indices.queries.RangeQuery`.
            limit (int): How many to return. Zero means all.

        Returns:
            list[tuple[Any, float]]: Block identities and scores.

        Note:
            Order is preserved rather than re-sorted by score. Every in-range hit scores the same, so re-sorting
            would discard the one thing a range scan knows -- which is the order.
        """
        from boltzmann.identity.digest import BlockId

        if not isinstance(query, RangeQuery):
            return []
        ordinals = self.scan(query)
        chosen = ordinals[:limit] if limit > 0 else ordinals
        return [
            (BlockId.parse(identity), RANGE_SCORE)
            for identity in (self._table.identity(ordinal) for ordinal in chosen)
            if identity is not None
        ]

    def extremes(self, key: OrderedKey) -> tuple[str, str] | None:
        """
        The smallest and largest value of one key.

        Args:
            key (OrderedKey): Which key.

        Returns:
            tuple[str, str] | None: The bounds, or ``None`` when the key was never indexed.
        """
        values = self._values.get(key.value)
        if not values:
            return None
        return values[0], values[-1]
