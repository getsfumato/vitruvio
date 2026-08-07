"""The shared base every vitruvio index sits on.

Four things live here because all six kinds need them and none of them should be solved six times.

**An ordinal table.** Indices work on small integers, not on 32-byte digests: a bitmap is a set of ordinals, a
postings list is an array of ordinals, and a usearch key is a ``uint64``. The table is the one place a
``BlockId`` becomes an ordinal, and it is ordered canonically -- sorted by identity -- so the same block set
always yields the same ordinals and a serialized index is byte-reproducible.

**Persistence.** Every index survives a restart. Structural ones *could* be rebuilt, but recomputing them on
every process start is waste, and ``Brain.__init__`` calls ``rebuild_indices()`` on every open.

**Internal incrementality.** ``build`` is a full rebuild by contract and is called on every commit. Honouring it
literally would re-index the whole module to add one block. Each index therefore diffs by identity and applies
only the difference -- behind the unchanged contract, which the SDK's own docstring invites.

**A statistics fragment.** Computed during the pass ``build`` is already making. A second pass to gather
statistics would double the cost of a write.
"""

from __future__ import annotations

from abc import abstractmethod
from bisect import bisect_left
from typing import TYPE_CHECKING, Any, ClassVar

from boltzmann.identity.time import utc_timestamp
from boltzmann.indices.base import AbstractIndex, IndexKind

from vitruvio.indices import format as envelope
from vitruvio.indices.projection import PROJECTION_ID, Projection, project
from vitruvio.indices.queries import BuildDelta, Capability, Results
from vitruvio.stats import StatsFragment, leaf_fingerprint

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from boltzmann.blocks.base import Block
    from boltzmann.blocks.memory_type import MemoryType
    from boltzmann.indices.base import ContentReader


class OrdinalTable:
    """
    The mapping from block identity to a dense small integer, and back.

    Ordinals are assigned in sorted-identity order and reassigned on every rebuild. That is deliberate: a stable
    ordinal across rebuilds would need a free-list and would leave holes, while a canonical order makes the
    serialized form depend on the *set* of blocks and nothing else -- so two machines indexing the same
    composition produce the same bytes.

    The vector index is the one that cannot follow this rule, because reassigning would invalidate an HNSW graph
    it did not rebuild. It keeps its own key table for that reason, documented where it does so.
    """

    __slots__ = ("_identities", "_ordinals")

    def __init__(self, identities: Iterable[str] = ()) -> None:
        """
        Build a table over a set of identities.

        Args:
            identities (Iterable[str]): The identities to number.
        """
        self._identities: list[str] = sorted(set(identities))
        self._ordinals: dict[str, int] = {identity: position for position, identity in enumerate(self._identities)}

    def __len__(self) -> int:
        """How many identities are numbered."""
        return len(self._identities)

    def __contains__(self, identity: str) -> bool:
        """Whether an identity has an ordinal."""
        return identity in self._ordinals

    @property
    def identities(self) -> tuple[str, ...]:
        """Every identity, in ordinal order."""
        return tuple(self._identities)

    def ordinal(self, identity: str) -> int | None:
        """The ordinal for an identity, or ``None`` if it is not held."""
        return self._ordinals.get(identity)

    def identity(self, ordinal: int) -> str | None:
        """The identity for an ordinal, or ``None`` if it is out of range."""
        if 0 <= ordinal < len(self._identities):
            return self._identities[ordinal]
        return None

    def ordinals(self, identities: Iterable[str]) -> frozenset[int]:
        """Ordinals for a set of identities, skipping any not held."""
        return frozenset(
            ordinal for ordinal in (self._ordinals.get(identity) for identity in identities) if ordinal is not None
        )

    def resolve(self, ordinals: Iterable[int]) -> tuple[str, ...]:
        """Identities for a set of ordinals, in ordinal order, skipping any out of range."""
        return tuple(
            identity for identity in (self.identity(ordinal) for ordinal in sorted(ordinals)) if identity is not None
        )

    def rank(self, identity: str) -> int:
        """Where an identity would sort, whether or not it is held. For a bisect over the identity array."""
        return bisect_left(self._identities, identity)


class VitruvioIndex(AbstractIndex):
    """
    The base every vitruvio index extends.

    Subclasses implement three things: ``_apply`` to fold a projection in, ``_reset`` to start clean, and
    ``search``. Everything else -- persistence, the delta, the header, the statistics fragment -- happens here.

    Attributes:
        memory_type (MemoryType): Which module this indexes.
        home (Path | None): Where to persist. ``None`` keeps the index in memory, which is what the tests use and
            what a caller who does not want a sidecar gets.
    """

    KIND: ClassVar[IndexKind]
    REBUILDABLE: ClassVar[bool] = True
    BODY_VERSION: ClassVar[int] = 1
    ENGINE: ClassVar[str] = "python"

    def __init__(self, memory_type: MemoryType, home: Path | None = None, *, autoload: bool = True) -> None:
        """
        Build an index over one module.

        Args:
            memory_type (MemoryType): Which module.
            home (Path | None): Directory for the sidecar file.
            autoload (bool): Read an existing sidecar, so a restart does not pay for a rebuild.
        """
        self.memory_type = memory_type
        self.home = home
        self._table = OrdinalTable()
        self._built_at = ""
        self._bound_root: str | None = None
        self._loaded_from: Path | None = None
        self._reset()
        if autoload and home is not None:
            self._load_if_present()

    # --- Identity and reporting ----------------------------------------------

    @property
    def path(self) -> Path | None:
        """Where this index persists, if anywhere."""
        return None if self.home is None else self.home / f"{self.memory_type.value}.{self.KIND.value}{envelope.SUFFIX}"

    @property
    def population(self) -> int:
        """
        How many blocks this index represents.

        A hard requirement rather than a nicety. An empty index does not announce itself: a planner consulting
        one gets no candidates and reports a confident nothing, which is worse than an error because it looks
        like an answer.
        """
        return len(self._table)

    @property
    def bound_root(self) -> str | None:
        """The module root this index was last built against, when it is known."""
        return self._bound_root

    @property
    def fingerprint(self) -> str:
        """Fingerprint of the identities held, which detects a redaction the root cannot."""
        return leaf_fingerprint(self._table.identities)

    def ordinals_for(self, identities: Iterable[str]) -> frozenset[int]:
        """
        Translate block identities into this index's own ordinals.

        Public because a caller holding a mask from one index needs to hand it to another, and ordinals are internal
        numbering: they agree across indices only because the ordering is canonical, and relying on that across an
        index boundary is coupling that will break the first time one of them changes.

        Args:
            identities (Iterable[str]): Block identities.

        Returns:
            frozenset[int]: The ordinals this index holds for them, skipping any it does not.
        """
        return self._table.ordinals(identities)

    def identities_for(self, ordinals: Iterable[int]) -> tuple[str, ...]:
        """
        Translate this index's ordinals back into block identities.

        Args:
            ordinals (Iterable[int]): Ordinals.

        Returns:
            tuple[str, ...]: Identities, in ordinal order.
        """
        return self._table.resolve(ordinals)

    def capability(self, *, root: str | None = None, model_tag: str | None = None) -> Capability:
        """
        What this index can currently answer.

        Args:
            root (str | None): The module's current root, to check the binding against.
            model_tag (str | None): The configured embedder's tag, for a vector index.

        Returns:
            Capability: Ready only when the index holds something and its binding matches.
        """
        state, detail = "ready", None
        if self.population == 0:
            state, detail = "empty", "the index holds no blocks"
        elif root is not None and self._bound_root is not None and self._bound_root != root:
            state, detail = "stale", "built against a different composition"
        elif model_tag is not None and self.model_tag is not None and model_tag != self.model_tag:
            state, detail = (
                "model_mismatch",
                f"built with {self.model_tag}, configured embedder is {model_tag}",
            )
        return Capability(
            kind=self.KIND.value,
            memory_type=self.memory_type.value,
            state=state,
            population=self.population,
            detail=detail,
            **self._capability_extra(),
        )

    def _capability_extra(self) -> dict[str, Any]:
        """Per-kind capability detail: which facets, keys or spaces this index offers."""
        return {}

    # --- Build ----------------------------------------------------------------

    def build(self, blocks: Iterable[Block], content: ContentReader) -> None:
        """
        Index a block set, applying only what changed.

        The SDK calls this on every commit and on every open, with the whole resolvable composition each time. A
        literal rebuild would make opening a large brain cost a full re-index before a single query runs, so the
        incoming set is diffed against what is held and only the difference is applied.

        Args:
            blocks (Iterable[Block]): The module's resolvable blocks, decoded.
            content (ContentReader): For reading blobs a block names, such as a normalized view.
        """
        materialized = list(blocks)
        incoming = [str(block.block_id) for block in materialized]
        delta = BuildDelta.between(self._table.identities, incoming)

        if delta.is_noop and self.population == len(incoming):
            return

        # Ordinals are canonical over the whole set, so any change to membership renumbers. That makes a
        # "partial apply" meaningless for the ordinal-based indices, and the honest thing is to say so: the win
        # is skipping the *expensive* per-block work, which subclasses do by consulting the delta.
        self._reset()
        self._table = OrdinalTable(incoming)
        self._on_build_start(delta)

        for block in materialized:
            self._apply(project(block, content))

        self._built_at = utc_timestamp()
        self._on_build_end(delta)
        if self.home is not None:
            self.flush()

    def _on_build_start(self, delta: BuildDelta) -> None:
        """Hook for a subclass that wants to know what changed before the pass begins."""

    def _on_build_end(self, delta: BuildDelta) -> None:
        """Hook for a subclass that finalises after the pass, e.g. computing an average."""

    def bind(self, root: str | None) -> None:
        """
        Record which module root this index describes.

        Called by the caller that holds the module, because ``build`` is not given one. Persisted in the header,
        so a later open can tell a current index from a stale one without re-reading every block.

        Args:
            root (str | None): The module's Merkle root.
        """
        self._bound_root = root
        if self.home is not None and self.population:
            self.flush()

    @abstractmethod
    def _reset(self) -> None:
        """Discard all state. Called before every build, and by the constructor."""

    @abstractmethod
    def _apply(self, projection: Projection) -> None:
        """Fold one block's projection into the index."""

    @abstractmethod
    def search(self, query: Any, limit: int = 10) -> list[tuple[Any, float]]:
        """Answer a query. The concrete query type is this index's own, from ``vitruvio.indices.queries``."""

    # --- Statistics -----------------------------------------------------------

    def fragment(self) -> StatsFragment:
        """
        What this index measured, for the planner to cost against.

        Returns:
            StatsFragment: The measurements, gathered during the build rather than in a second pass.
        """
        return StatsFragment(
            kind=self.KIND.value,
            memory_type=self.memory_type.value,
            indexed=self.population,
            fingerprint=self.fingerprint,
            built_at=self._built_at,
            model_tag=self.model_tag,
            **self._fragment_extra(),
        )

    def _fragment_extra(self) -> dict[str, Any]:
        """Per-kind statistics: columns, terms, graph or vectors."""
        return {}

    # --- Persistence ----------------------------------------------------------

    def header(self) -> envelope.Header:
        """The header to write beside the body."""
        return envelope.Header(
            kind=self.KIND.value,
            memory_type=self.memory_type.value,
            body_version=self.BODY_VERSION,
            merkle_root=self._bound_root,
            leaf_fingerprint=self.fingerprint,
            population=self.population,
            engine=self.ENGINE,
            projection_id=PROJECTION_ID,
            model_tag=self.model_tag,
            built_at=self._built_at,
            extra=self._header_extra(),
        )

    def _header_extra(self) -> dict[str, Any]:
        """Per-kind parameters worth recording, e.g. HNSW connectivity or the analyzer's identity."""
        return {}

    def flush(self) -> Path | None:
        """
        Persist the index, atomically -- unless it holds nothing.

        An empty index is **not** written, and an existing file is removed when the index becomes empty. This is
        the same rule the protocol states for a travelling vector layer: an artifact that claims to carry an index
        and carries none is worse than one that omits it, because a consumer can detect the absence and cannot
        detect the emptiness. A module with no blocks yet is the ordinary reason to be here.

        Returns:
            Path | None: Where it was written, or ``None`` when there was nothing to write.
        """
        target = self.path
        if target is None:
            return None
        if self.population == 0:
            target.unlink(missing_ok=True)
            return None
        return envelope.write(target, self.header(), self._dump_body())

    def _load_if_present(self) -> None:
        """
        Read an existing sidecar, tolerating one that cannot be read.

        A damaged file is *not* treated as an empty index -- that would be the silent-wrong-answer failure this
        whole design guards against. It is discarded, leaving population zero, which the capability probe reports
        as ``empty`` and the planner excludes.
        """
        target = self.path
        if target is None:
            return
        try:
            found = envelope.read(target)
        except envelope.IndexFormatError:
            return
        if found is None:
            return

        header, body = found
        if header.kind != self.KIND.value or header.memory_type != self.memory_type.value:
            return
        if header.body_version != self.BODY_VERSION:
            # A structural index is cheap to rebuild, so an older body is dropped rather than migrated.
            return
        try:
            self._load_body(body)
        except Exception:
            self._reset()
            self._table = OrdinalTable()
            return

        self._table = OrdinalTable(body.get("identities", []))
        self._bound_root = header.merkle_root
        self._built_at = header.built_at
        self._loaded_from = target

    def _dump_body(self) -> dict[str, Any]:
        """The serializable body, with the ordinal table's identities so ordinals can be restored."""
        return {"identities": list(self._table.identities), **self._dump_state()}

    @abstractmethod
    def _dump_state(self) -> dict[str, Any]:
        """The subclass's own serializable state."""

    @abstractmethod
    def _load_body(self, body: dict[str, Any]) -> None:
        """Restore the subclass's own state from a body."""

    # --- Helpers for subclasses ----------------------------------------------

    def _results(self, scored: Sequence[tuple[int, float]], limit: int, *, exhausted: bool = True) -> Results:
        """
        Turn scored ordinals into results, sorted deterministically.

        The tie-break is ``(-score, identity)``, matching the SDK's own scan. Agreement wherever the scores agree
        is worth having: it is what makes the differential test against the scan meaningful rather than noisy.

        Args:
            scored (Sequence[tuple[int, float]]): Ordinal and score pairs.
            limit (int): How many to return.
            exhausted (bool): Whether the whole domain was enumerated.

        Returns:
            Results: The hits.
        """
        from vitruvio.indices.queries import Hit

        resolved = [
            (identity, score)
            for identity, score in ((self._table.identity(ordinal), score) for ordinal, score in scored)
            if identity is not None
        ]
        resolved.sort(key=lambda pair: (-pair[1], pair[0]))
        truncated = limit > 0 and len(resolved) > limit
        chosen = resolved[:limit] if limit > 0 else resolved
        return Results(
            hits=tuple(Hit(block_id=identity, score=score) for identity, score in chosen),
            exhausted=exhausted and not truncated,
            consulted=self.KIND.value,
        )
