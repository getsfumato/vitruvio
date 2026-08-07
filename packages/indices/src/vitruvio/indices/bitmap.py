"""The bitmap index: categorical filtering, and the best selectivity oracle in the system.

Roaring bitmaps via ``pyroaring`` when it is installed, plain ``frozenset`` behind the same façade when it is not.
The fallback is not a placeholder: at the sizes a knowledge module reaches, a Python set intersection is perfectly
serviceable, and Roaring's win is compression and cross-language portability rather than raw speed. Which engine
was used is written into the header, so a file is never ambiguous about how to read it.

The property that makes this index matter to the planner is not speed. It is that a facet intersection is
**exact**: the index can compute the real cardinality of ``semantic AND subject=signals AND tag=lecture`` before
any generator runs. Every other estimate in the cost model is interpolated. Being able to say "this is 96 blocks,
measured" rather than "this is about 400, probably" is what lets the planner choose a filter-first plan with
confidence -- and what lets EXPLAIN show real numbers instead of guesses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from boltzmann.indices.base import IndexKind

from vitruvio.indices.base import VitruvioIndex
from vitruvio.indices.projection import Facet, fold
from vitruvio.indices.queries import Combine, FacetClause, FacetQuery, Results
from vitruvio.stats import TOP_VALUES, ColumnStats, Estimate

if TYPE_CHECKING:
    from collections.abc import Iterable

    from vitruvio.indices.projection import Projection

FILTER_SCORE = 1.0
"""What a filter match scores. A filter has no relevance -- ranking belongs to whatever generated the candidates."""

MAX_DISTINCT_PER_FACET = 4096
"""Cap on distinct values per facet.

A facet with one distinct value per block is not a facet, it is an identity, and bitmapping it costs memory
proportional to the module with no filtering benefit. Exceeding the cap marks the facet unindexed and *says so*,
so the planner stops planning on it rather than silently receiving an empty bitmap -- which it would read as "no
blocks match".
"""


def _roaring() -> Any | None:
    """The Roaring implementation, or ``None`` when the wheel is absent."""
    try:
        from pyroaring import BitMap
    except ModuleNotFoundError:  # pragma: no cover - depends on the platform's wheels
        return None
    return BitMap


class BitmapIndex(VitruvioIndex):
    """
    Conjunctive filtering over categorical facets.

    Attributes:
        memory_type (MemoryType): Which module this indexes.
    """

    KIND: ClassVar[IndexKind] = IndexKind.BITMAP
    REBUILDABLE: ClassVar[bool] = True
    BODY_VERSION: ClassVar[int] = 1

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Build the index, choosing an engine once."""
        self._bitmap_type = _roaring()
        super().__init__(*args, **kwargs)

    @property
    def ENGINE(self) -> str:  # type: ignore[override]  # noqa: N802 - matches the ClassVar it overrides
        """Which implementation is backing the sets, recorded in the header."""
        return "pyroaring" if self._bitmap_type is not None else "python-set"

    def _reset(self) -> None:
        """Discard every facet."""
        self._facets: dict[str, dict[str, set[int]]] = {}
        self._unindexed: set[str] = set()
        self._null_counts: dict[str, int] = {}

    def _apply(self, projection: Projection) -> None:
        """Record this block's facet values."""
        ordinal = self._table.ordinal(projection.block_id)
        if ordinal is None:
            return

        for facet in Facet:
            values = projection.facets.get(facet, ())
            table = self._facets.setdefault(facet.value, {})
            if not values:
                # Counted rather than ignored: "how many blocks have no subject" is what makes a subject filter's
                # selectivity estimate correct rather than optimistic.
                self._null_counts[facet.value] = self._null_counts.get(facet.value, 0) + 1
                continue
            for value in values:
                key = fold(value)
                if key not in table and len(table) >= MAX_DISTINCT_PER_FACET:
                    self._unindexed.add(facet.value)
                    continue
                table.setdefault(key, set()).add(ordinal)

    def _capability_extra(self) -> dict[str, Any]:
        """Which facets can actually be filtered on -- excluding any that blew the distinct cap."""
        return {
            "facets": tuple(
                sorted(name for name, table in self._facets.items() if table and name not in self._unindexed)
            )
        }

    def _fragment_extra(self) -> dict[str, Any]:
        """
        Per-facet distributions, with **exact** counts for the most frequent values.

        The top list is capped, but the total and the distinct count are not, so the planner can tell the
        difference between "this value does not occur" and "this value is in the unmeasured tail" -- and the
        former lets it prune a whole subplan before running anything.
        """
        columns: dict[str, ColumnStats] = {}
        for name, table in self._facets.items():
            if not table:
                continue
            counted = sorted(((value, len(ordinals)) for value, ordinals in table.items()), key=lambda p: (-p[1], p[0]))
            columns[name] = ColumnStats(
                distinct=len(table),
                null_count=self._null_counts.get(name, 0),
                total_values=sum(count for _, count in counted),
                top=tuple(counted[:TOP_VALUES]),
                truncated=len(table) > TOP_VALUES or name in self._unindexed,
            )
        return {"columns": columns}

    def _header_extra(self) -> dict[str, Any]:
        """Facet sizes, and any facet that exceeded the distinct cap."""
        return {
            "facets": {name: len(table) for name, table in sorted(self._facets.items()) if table},
            "unindexed": sorted(self._unindexed),
        }

    def _dump_state(self) -> dict[str, Any]:
        """
        Sorted keys and sorted ordinal lists.

        Plain lists rather than Roaring's serialized form, so a file written with the fallback engine is readable
        by a build that has ``pyroaring`` and the other way round. That portability is worth more than the space:
        the alternative is an index that has to be rebuilt when a wheel appears.
        """
        return {
            "facets": {
                name: {value: sorted(ordinals) for value, ordinals in sorted(table.items())}
                for name, table in sorted(self._facets.items())
            },
            "unindexed": sorted(self._unindexed),
            "null_counts": dict(sorted(self._null_counts.items())),
        }

    def _load_body(self, body: dict[str, Any]) -> None:
        """Restore the facets."""
        self._reset()
        for name, table in body.get("facets", {}).items():
            self._facets[name] = {value: set(ordinals) for value, ordinals in table.items()}
        self._unindexed = set(body.get("unindexed", []))
        self._null_counts = dict(body.get("null_counts", {}))

    # --- Query ----------------------------------------------------------------

    def _clause_set(self, clause: FacetClause) -> set[int] | None:
        """
        The ordinals one clause admits, or ``None`` when the facet cannot be answered.

        ``None`` and "the empty set" are deliberately different. An unindexed facet cannot be evaluated and the
        planner must fall back to a residual filter; an indexed facet with no matching value genuinely matches
        nothing. Conflating them is how a filter silently starts excluding everything.
        """
        if clause.facet.value in self._unindexed:
            return None
        table = self._facets.get(clause.facet.value)
        if table is None:
            return None

        sets = [table.get(fold(value), set()) for value in clause.values]
        if not sets:
            return None
        if clause.combine is Combine.ALL:
            matched = set(sets[0])
            for other in sets[1:]:
                matched &= other
        else:
            matched = set()
            for other in sets:
                matched |= other

        if clause.negate:
            matched = set(range(len(self._table))) - matched
        return matched

    def filter(self, query: FacetQuery) -> frozenset[int] | None:
        """
        Intersect every clause and return the surviving ordinals.

        Clauses are evaluated smallest-first, so the intermediate set only ever shrinks. That is the whole trick
        of conjunctive bitmap evaluation, and it is why the ordering is not incidental.

        Args:
            query (FacetQuery): The clauses.

        Returns:
            frozenset[int] | None: The surviving ordinals, or ``None`` when some clause could not be evaluated and
            the caller must post-filter instead.
        """
        if query.is_empty:
            return query.allow

        evaluated: list[set[int]] = []
        for clause in query.clauses:
            matched = self._clause_set(clause)
            if matched is None:
                return None
            evaluated.append(matched)

        evaluated.sort(key=len)
        surviving = set(evaluated[0])
        for other in evaluated[1:]:
            surviving &= other
            if not surviving:
                break

        if query.allow is not None:
            surviving &= set(query.allow)
        return frozenset(surviving)

    def matching(self, query: FacetQuery) -> tuple[str, ...] | None:
        """
        The block identities a facet filter admits.

        The form a caller outside this index should use: ordinals are internal numbering, and translating them across
        an index boundary couples two indices' layouts together.

        Args:
            query (FacetQuery): The clauses.

        Returns:
            tuple[str, ...] | None: Identities, or ``None`` when a clause could not be evaluated and the caller must
            post-filter instead. ``None`` and the empty tuple are different answers.
        """
        surviving = self.filter(query)
        if surviving is None:
            return None
        return self.identities_for(surviving)

    def estimate(self, query: FacetQuery) -> Estimate:
        """
        The **exact** cardinality of a facet intersection.

        Not an estimate at all when every clause is indexed, which is what makes this index the planner's most
        trustworthy input -- and what lets EXPLAIN print measured cardinalities beside interpolated ones.

        Args:
            query (FacetQuery): The clauses.

        Returns:
            Estimate: Exact when every clause could be evaluated; a pessimistic pass-through when one could not.
        """
        surviving = self.filter(query)
        if surviving is None:
            return Estimate(
                rows=self.population,
                confidence=0.2,
                note="a clause names an unindexed facet, so it must be evaluated as a residual filter",
            )
        return Estimate.measured(len(surviving), note="exact bitmap intersection")

    def search(self, query: Any, limit: int = 10) -> list[tuple[Any, float]]:
        """
        The SDK's entry point.

        A bitmap match scores :data:`FILTER_SCORE` for everything: a filter expresses membership, not relevance.
        The planner should normally consume this as a *prefilter* handed to a generator rather than as a candidate
        source of its own.

        Args:
            query (Any): A :class:`~vitruvio.indices.queries.FacetQuery`.
            limit (int): How many to return. Zero means all.

        Returns:
            list[tuple[Any, float]]: Block identities and scores.
        """
        from boltzmann.identity.digest import BlockId

        if not isinstance(query, FacetQuery):
            return []
        surviving = self.filter(query)
        if surviving is None:
            return []
        results = self._results([(ordinal, FILTER_SCORE) for ordinal in surviving], limit=limit)
        return [(BlockId.parse(hit.block_id), hit.score) for hit in results.hits]

    def values(self, facet: Facet) -> dict[str, int]:
        """
        Every value of one facet and how many blocks carry it.

        Args:
            facet (Facet): Which dimension.

        Returns:
            dict[str, int]: Value counts, for ``index stats`` and for a caller building a filter interactively.
        """
        return {value: len(ordinals) for value, ordinals in sorted(self._facets.get(facet.value, {}).items())}

    def as_results(self, ordinals: Iterable[int], limit: int = 0) -> Results:
        """Wrap ordinals as results, for a planner that already holds a mask."""
        return self._results([(ordinal, FILTER_SCORE) for ordinal in ordinals], limit=limit)
