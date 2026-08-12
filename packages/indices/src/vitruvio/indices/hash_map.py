"""The hash-map index: exact resolution, and the module-level statistics.

A ``BlockId`` is already a 256-bit uniform hash, so a Python dict over it is O(1) and beats anything more
elaborate in-process. The interesting decision is not the data structure but **which keys deserve a table**, and
the rule is: identity-shaped keys only.

``subject`` and ``tags`` are *facets* -- many blocks share one -- and they belong to the bitmap index, which can
intersect them cheaply. ``label`` earns a table because "get the concept called Fourier series" is an operation
people actually perform, and a label is close enough to an identity that a duplicate is worth reporting.
``record_subject`` earns one because it is the only way a provenance block is addressable at all: those blocks are
looked up by *what they talk about*, never by themselves.

This index also owns the **module-level** statistics fragment -- block count, average size, the leaf fingerprint --
because it necessarily visits every block and so gets them for free. It is registered first for that reason, but
the merge is order-independent anyway, so the ordering is an optimisation rather than a requirement.
"""

from __future__ import annotations

from typing import Any, ClassVar

from boltzmann.indices.base import IndexKind

from vitruvio.indices.base import VitruvioIndex
from vitruvio.indices.projection import IdentityKey, Projection, fold
from vitruvio.indices.queries import IdQuery, Results

EXACT_SCORE = 1.0
"""What an identity match scores.

No gradation, deliberately, and it mirrors the SDK's own exact path. An identity match is not a relevance
judgement -- there is no such thing as a partially correct digest -- so giving it a graded score would invite a
caller to compare it against a similarity score, which is a category error.
"""


class HashMapIndex(VitruvioIndex):
    """
    Exact lookup by identity, blob digest, label, alias, or provenance subject.

    Attributes:
        memory_type (MemoryType): Which module this indexes.
    """

    KIND: ClassVar[IndexKind] = IndexKind.HASH_MAP
    REBUILDABLE: ClassVar[bool] = True
    BODY_VERSION: ClassVar[int] = 1

    def _reset(self) -> None:
        """Discard every table."""
        self._tables: dict[str, dict[str, list[int]]] = {key.value: {} for key in IdentityKey}
        self._total_bytes = 0
        self._sized = 0

    def _apply(self, projection: Projection) -> None:
        """Record this block's identity keys, and accumulate the module-level numbers."""
        ordinal = self._table.ordinal(projection.block_id)
        if ordinal is None:
            return

        for key, values in projection.identities.items():
            table = self._tables[key.value]
            for value in values:
                if not value:
                    continue
                table.setdefault(value, []).append(ordinal)

        if projection.size:
            self._total_bytes += projection.size
            self._sized += 1

    def _capability_extra(self) -> dict[str, Any]:
        """Which identity tables actually hold anything."""
        return {"keys": tuple(sorted(name for name, table in self._tables.items() if table))}

    def _fragment_extra(self) -> dict[str, Any]:
        """
        The module-level numbers, which belong to no single index.

        ``resolvable_count`` equals the population because the SDK filters to the resolvable subset before calling
        ``build`` -- an index is never handed a block it could not read.
        """
        return {
            "module_level": True,
            "cardinality": self.population,
            "resolvable_count": self.population,
            "average_block_bytes": (self._total_bytes / self._sized) if self._sized else 0.0,
        }

    def _header_extra(self) -> dict[str, Any]:
        """Report the per-table sizes, which is what makes a duplicate label visible in ``index status``."""
        return {"tables": {name: len(table) for name, table in sorted(self._tables.items()) if table}}

    def _dump_state(self) -> dict[str, Any]:
        """Tables with sorted keys and sorted postings, so the bytes depend only on the block set."""
        return {
            "tables": {
                name: {value: sorted(ordinals) for value, ordinals in sorted(table.items())}
                for name, table in sorted(self._tables.items())
            },
            "total_bytes": self._total_bytes,
            "sized": self._sized,
        }

    def _load_body(self, body: dict[str, Any]) -> None:
        """Restore the tables."""
        self._reset()
        for name, table in body.get("tables", {}).items():
            if name in self._tables:
                self._tables[name] = {value: list(ordinals) for value, ordinals in table.items()}
        self._total_bytes = int(body.get("total_bytes", 0))
        self._sized = int(body.get("sized", 0))

    # --- Query ----------------------------------------------------------------

    def lookup(self, query: IdQuery) -> Results:
        """
        Resolve identities and identity-shaped keys.

        Args:
            query (IdQuery): What to resolve.

        Returns:
            Results: Every match, at :data:`EXACT_SCORE`. Always exhausted: an exact lookup either finds the key
            or it does not, so there is never "more" to find.
        """
        ordinals: set[int] = set()

        for identity in query.identities:
            ordinal = self._table.ordinal(identity)
            if ordinal is not None:
                ordinals.add(ordinal)

        for key, value in query.keys:
            table = self._tables.get(key.value, {})
            ordinals.update(table.get(fold(value), ()))

        return self._results([(ordinal, EXACT_SCORE) for ordinal in ordinals], limit=0)

    def search(self, query: Any, limit: int = 10) -> list[tuple[Any, float]]:
        """
        The SDK's entry point.

        Accepts an :class:`~vitruvio.indices.queries.IdQuery`, or a bare string, which is read as an identity when
        it looks like a digest and as a label otherwise. The bare-string form is what makes
        ``open_index(SEMANTIC, HASH_MAP).search("Fourier series")`` work for a caller who is not the planner.

        Args:
            query (Any): The query.
            limit (int): How many to return. Zero means all.

        Returns:
            list[tuple[Any, float]]: Block identities and scores, as the ``Index`` Protocol requires.
        """
        from boltzmann.identity.digest import BlockId

        if isinstance(query, str):
            text = query.strip()
            if text.startswith("sha256:"):
                query = IdQuery(identities=(text,))
            else:
                query = IdQuery(keys=((IdentityKey.LABEL, text), (IdentityKey.ALIAS, text)))

        if not isinstance(query, IdQuery):
            return []

        results = self.lookup(query)
        hits = results.hits[:limit] if limit > 0 else results.hits
        return [(BlockId.parse(hit.block_id), hit.score) for hit in hits]

    def duplicates(self, key: IdentityKey) -> dict[str, int]:
        """
        Keys that resolve to more than one block.

        A label shared by two concepts is not an error -- both are returned, correctly -- but it *is* the
        planner's cue that a label probe is not a unique lookup and cannot be treated as one.

        Args:
            key (IdentityKey): Which table.

        Returns:
            dict[str, int]: The colliding values and how many blocks each names.
        """
        table = self._tables.get(key.value, {})
        return {value: len(ordinals) for value, ordinals in table.items() if len(ordinals) > 1}
