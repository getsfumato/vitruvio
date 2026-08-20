"""The vector index: usearch HNSW, and the only index that travels.

Every other index is a deterministic function of the blocks, so any client can rebuild it. This one cannot be rebuilt
without a model, which is why the protocol packs it into the published artifact and refuses a pull whose model tag does
not match. Three consequences shape everything here.

**Keys are explicit, never truncated.** usearch keys are ``uint64`` and a ``BlockId`` is 256 bits. Truncating would be
silent -- a collision produces a wrong neighbour, not an error -- so an explicit table maps keys to rows, and it travels
inside the *same bytes* as the graph. Keys are therefore never compared across files, and a fresh build assigning
different numbers is harmless.

**``dump()`` returns exactly the file body.** The local sidecar and the published layer are the same bytes, so it is
impossible to publish something this process does not hold. The protocol is explicit that an artifact claiming a vector
index and carrying none is worse than one that omits it, because a consumer can detect absence and cannot detect
emptiness.

**A mismatched tag is refused, not degraded.** Vectors from a different model are not lower quality, they are
*unrelated*: the cosines between them are noise that would silently poison fusion. Refusing is better than ranking on
noise, and the refusal names which field differs.

Chunking is by **characters**, not tokens. A token-based chunker would depend on which tokenizer happens to be
installed, so chunk boundaries -- and therefore cache keys and vector identity -- would differ between a
``[vision]``-only install and a full one.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

from boltzmann.blocks.memory_type import MemoryType
from boltzmann.exceptions import DistributionError
from boltzmann.indices.base import IndexKind

from vitruvio.embeddings import (
    Embedder,
    EmbedderUnavailableError,
    MemoryCache,
    ModelTag,
    TextRole,
    Vector,
    cache_key,
    explain_mismatch,
)
from vitruvio.indices import format as envelope
from vitruvio.indices.base import VitruvioIndex
from vitruvio.indices.projection import Projection
from vitruvio.indices.queries import VectorQuery
from vitruvio.stats import VectorStats

CHUNKER_ID = "vitruvio-chunker/1"
"""Bumped when chunk boundaries change. Inside the model tag, because different chunks are different embedded strings."""

MAX_CHARS = 1600
"""Characters per chunk. Roughly 380 tokens, comfortably inside every model's window."""

OVERLAP = 200
"""Characters of overlap, so a sentence spanning a boundary is intact in at least one chunk."""

BOUNDARIES = ("\n\n", ". ", "\n", " ")
"""Where to prefer breaking, best first. Searched backwards within the overlap window."""

OVERSAMPLE = 4
"""Probe multiplier, so grouping multi-chunk hits back to blocks still fills the requested limit."""

SPACE_TEXT = "text"
"""The text embedding space."""

SPACE_MULTIMODAL = "multimodal"
"""Where image vectors live. Separate because a caption-trained text tower degrades pure-text retrieval."""


class IndexModelMismatchError(DistributionError):
    """A vector index was built by a different model than the one configured.

    Subclasses ``DistributionError`` deliberately: ``Brain._restore_travelling`` catches that, so a brain with a
    mismatched index still *opens* -- degraded, with the vector generator excluded -- rather than becoming unopenable.
    A brain you cannot open is worse than a brain you cannot search semantically.
    """


def chunk(text: str, *, max_chars: int = MAX_CHARS, overlap: int = OVERLAP) -> list[tuple[int, str, tuple[int, int]]]:
    """
    Split long text at preferred boundaries.

    Character-based on purpose. A token-based chunker depends on which tokenizer is installed, which would make chunk
    boundaries -- and therefore cache keys and vector identity -- vary between installs. Characters are a pure function
    of the text and stable forever.

    Args:
        text (str): The text to split.
        max_chars (int): Maximum chunk length.
        overlap (int): How far back to look for a boundary, and how much to overlap.

    Returns:
        list[tuple[int, str, tuple[int, int]]]: Chunk index, text, and the character span it came from -- the span is
        what lets a citation point at a passage rather than at a whole document.
    """
    stripped = text.strip()
    if not stripped:
        return []
    if len(stripped) <= max_chars:
        return [(0, stripped, (0, len(stripped)))]

    chunks: list[tuple[int, str, tuple[int, int]]] = []
    start = 0
    position = 0
    while start < len(stripped):
        end = min(start + max_chars, len(stripped))
        if end < len(stripped):
            window = stripped[max(start, end - overlap) : end]
            for boundary in BOUNDARIES:
                cut = window.rfind(boundary)
                if cut > 0:
                    end = max(start, end - overlap) + cut + len(boundary)
                    break
        piece = stripped[start:end].strip()
        if piece:
            chunks.append((position, piece, (start, end)))
            position += 1
        if end <= start:
            break
        start = max(end - overlap, end) if end >= len(stripped) else end - overlap
        if start >= len(stripped):
            break
    return chunks


class VectorIndex(VitruvioIndex):
    """
    Similarity search over usearch HNSW, with an explicit key table that travels.

    Attributes:
        memory_type (MemoryType): Which module this indexes.
        embedder (Embedder): What produces the vectors, and whose tag gates compatibility.
    """

    KIND: ClassVar[IndexKind] = IndexKind.VECTOR
    REBUILDABLE: ClassVar[bool] = False
    BODY_VERSION: ClassVar[int] = 1
    ENGINE: ClassVar[str] = "usearch"

    def __init__(
        self,
        memory_type: MemoryType,
        home: Path | None = None,
        *,
        embedder: Embedder,
        cache: Any | None = None,
        connectivity: int = 16,
        expansion_add: int = 128,
        expansion_search: int = 64,
        dtype: str = "f32",
        autoload: bool = True,
    ) -> None:
        """
        Build the index.

        Args:
            memory_type (MemoryType): Which module.
            home (Path | None): Where the sidecar lives.
            embedder (Embedder): The model.
            cache (Any | None): An embedding cache. Defaults to in-memory, which is the wrong choice for a real brain
                and the right one for a test -- the runtime passes a persistent one.
            connectivity (int): HNSW graph degree.
            expansion_add (int): Build-time effort.
            expansion_search (int): Query-time effort. Recorded in the header so a reader knows what it received.
            dtype (str): Storage precision.
            autoload (bool): Read an existing sidecar.
        """
        self.embedder = embedder
        self.cache = cache if cache is not None else MemoryCache(embedder.tag.render())
        self.connectivity = connectivity
        self.expansion_add = expansion_add
        self.expansion_search = expansion_search
        self.dtype = dtype
        super().__init__(memory_type, home, autoload=autoload)

    # --- Identity -------------------------------------------------------------

    @property
    def model_tag(self) -> str | None:
        """The tag every vector here was produced under, including the chunker."""
        return self._tag().render()

    def _tag(self) -> ModelTag:
        """The embedder's tag, with this index's dtype, projection and chunker folded in.

        All three belong in the tag: a different dtype quantises differently, and a different projection or chunker
        embeds different *strings*, so any of them changes where a vector lands. They are composed here rather than
        claimed by the embedder because they are decisions of the thing calling the embedder -- an embedder does not
        know what text it will be handed.
        """
        from dataclasses import replace

        from vitruvio.indices.projection import PROJECTION_ID

        return replace(
            self.embedder.tag,
            dtype=self.dtype,
            projection=PROJECTION_ID.replace("/", "-"),
            chunker=CHUNKER_ID.replace("/", "-"),
        )

    @property
    def queryable(self) -> bool:
        """
        Whether a probe can be made *and* scored. False degrades rather than fails: nothing is embedded and no
        query runs, so the planner simply has no vector generator to choose.

        Two reasons, and the second is not obvious. Scoring here is a dot product, which equals cosine only for
        unit vectors. ``ModelTag.normalization`` is a parsed field whose other legal value is ``none``, and
        providers arrive through a plugin registry -- so an embedder declaring ``none`` would be ranked by dot
        product, which favours whichever passage happens to have the longest vector, and reported as cosine. Every
        embedder vitruvio ships declares ``l2``, so this never fires today; it is here because ranking wrong looks
        exactly as plausible as ranking right.
        """
        return bool(self.embedder.available) and self.embedder.tag.normalization == "l2"

    # --- Build ----------------------------------------------------------------

    def _reset(self) -> None:
        """Discard every vector and the key table."""
        # key -> (block_id, space, chunk_index, span, cache_key). Keys come from a monotone counter and are never
        # recycled: a recycled key would let a new vector inherit an old one's neighbour references.
        self._rows: dict[int, tuple[str, str, int, tuple[int, int] | None, bytes]] = {}
        self._next_key = 0
        self._vectors: dict[int, tuple[float, ...]] = {}
        self._engines: dict[str, Any] = {}
        self._removed = 0

    def _apply(self, projection: Projection) -> None:
        """Embed this block's projected text, in chunks, reusing anything the cache already holds."""
        if projection.embed_text is None or not self.queryable:
            return

        pieces = chunk(projection.embed_text)
        if not pieces:
            return

        tag = self._tag().render()
        wanted: list[tuple[int, str, tuple[int, int], bytes]] = []
        for position, text, span in pieces:
            key = cache_key(tag, SPACE_TEXT, TextRole.PASSAGE.value, text)
            wanted.append((position, text, span, key))

        cached = self.cache.get_many([key for _, _, _, key in wanted])
        missing = [(text, key) for _, text, _, key in wanted if key not in cached]

        if missing:
            try:
                produced = self.embedder.embed_text([text for text, _ in missing], role=TextRole.PASSAGE)
            except EmbedderUnavailableError:
                # The index is fine; we cannot make vectors right now. Leaving the block unindexed is honest, and the
                # capability probe will report the index as short rather than pretending it is complete.
                return
            fresh = {key: vector for (_, key), vector in zip(missing, produced, strict=True)}
            self.cache.put_many(fresh, SPACE_TEXT)
            cached = {**cached, **fresh}

        for position, _text, span, key in wanted:
            vector = cached.get(key)
            if vector is None:
                continue
            self._rows[self._next_key] = (projection.block_id, SPACE_TEXT, position, span, key)
            self._vectors[self._next_key] = tuple(vector)
            self._next_key += 1

    def _on_build_end(self, delta: Any) -> None:
        """Construct the HNSW graph once, from every vector collected."""
        self._engines = {}
        self._build_engine(SPACE_TEXT)

    def _build_engine(self, space: str) -> None:
        """
        Build one space's HNSW graph, or fall back to exact search.

        usearch is a declared dependency, but a platform without a wheel must still be able to *use* a brain: an exact
        scan is slower and perfectly accurate, so the fallback costs latency rather than correctness. Which engine ran
        is recorded in the header, so a file is never ambiguous.
        """
        keys = [key for key, row in self._rows.items() if row[1] == space]
        if not keys:
            return
        try:
            from usearch.index import Index as Usearch
        except ModuleNotFoundError:  # pragma: no cover - a declared dependency
            return

        import numpy as np

        dimensions = len(next(iter(self._vectors.values())))
        engine = Usearch(
            ndim=dimensions,
            metric="cos",
            dtype=self.dtype,
            connectivity=self.connectivity,
            expansion_add=self.expansion_add,
            expansion_search=self.expansion_search,
        )
        # numpy only at this edge. The Embedder Protocol speaks plain tuples so that a provider is not forced to depend
        # on numpy, and usearch wants arrays -- so the conversion happens here, once, and nowhere else.
        engine.add(
            np.asarray(keys, dtype=np.uint64),
            np.asarray([self._vectors[key] for key in keys], dtype=np.float32),
        )
        self._engines[space] = engine

    # --- Reporting ------------------------------------------------------------

    @property
    def population(self) -> int:
        """
        How many **blocks** are represented, not how many vectors.

        The distinction matters: a chunked document contributes several vectors and is one block, and the planner's
        cardinality reasoning is about blocks.
        """
        return len({row[0] for row in self._rows.values()})

    def _capability_extra(self) -> dict[str, Any]:
        """Which embedding spaces hold anything."""
        return {"spaces": tuple(sorted({row[1] for row in self._rows.values()}))}

    def _fragment_extra(self) -> dict[str, Any]:
        """
        Per-space vector statistics, including a **measured** recall curve.

        The curve is measured against exact search over a sample, which is what lets the planner treat recall as an
        estimated quantity with an empirical basis. Without it, "recall" in the cost objective would be a made-up
        number, and the planner's central trade-off would be arithmetic over a guess.
        """
        spaces: dict[str, VectorStats] = {}
        for space in {row[1] for row in self._rows.values()}:
            keys = [key for key, row in self._rows.items() if row[1] == space]
            if not keys:
                continue
            spaces[space] = VectorStats(
                vectors=len(keys),
                blocks=len({self._rows[key][0] for key in keys}),
                dimensions=len(self._vectors[keys[0]]),
                metric="cos",
                model_tag=self._tag().render(),
                removed_fraction=self._removed / max(1, len(keys) + self._removed),
                recall_curve=self._measure_recall(space, keys),
            )
        return {"vectors": spaces}

    def _measure_recall(self, space: str, keys: Sequence[int]) -> tuple[tuple[int, float], ...]:
        """
        Measure recall@10 against exact search, at a few effort levels.

        Sampled, and skipped entirely on a small space where the approximate and exact answers cannot differ. Measuring
        is cheap and turns the planner's recall term from a guess into arithmetic.
        """
        engine = self._engines.get(space)
        if engine is None or len(keys) < 64:
            # Below a few dozen vectors HNSW visits everything anyway, so a measurement here would report 1.0 and mean
            # nothing. Reported as 1.0 explicitly rather than left empty, which the planner reads pessimistically.
            return ((self.expansion_search, 1.0),)

        sample = [self._vectors[key] for key in list(keys)[:: max(1, len(keys) // 32)]][:32]
        curve: list[tuple[int, float]] = []
        for effort in (32, 64, 128):
            hits = 0
            total = 0
            for probe in sample:
                exact = self._exact_search(space, probe, 10)
                approximate = self._approximate_search(space, probe, 10, effort)
                hits += len(set(exact) & set(approximate))
                total += len(exact)
            curve.append((effort, hits / total if total else 1.0))
        return tuple(curve)

    def _header_extra(self) -> dict[str, Any]:
        """The HNSW parameters and the chunker, so a reader knows exactly what it received."""
        return {
            "connectivity": self.connectivity,
            "expansion_add": self.expansion_add,
            "expansion_search": self.expansion_search,
            "dtype": self.dtype,
            "vectors": len(self._rows),
            "spaces": sorted({row[1] for row in self._rows.values()}),
            "engine_backed": sorted(self._engines),
        }

    def header(self) -> envelope.Header:
        """The base header, with the chunker recorded where a reader looks for it."""
        return super().header().model_copy(update={"chunker_id": CHUNKER_ID})

    # --- Persistence and travel ----------------------------------------------

    def _dump_state(self) -> dict[str, Any]:
        """
        The key table and the raw vectors.

        The **vectors** are serialized, not usearch's graph buffers. usearch's own serialization is not promised to be
        byte-stable across versions or platforms, and a travelling layer whose digest changes for no semantic reason
        would break incremental distribution -- the whole point of which is that an unchanged module reuses its digest.
        Rebuilding the graph on load costs a little time; publishability is worth more than load speed for the one
        artifact that has to travel.
        """
        return {
            "rows": {
                str(key): [row[0], row[1], row[2], list(row[3]) if row[3] else None, row[4].hex()]
                for key, row in sorted(self._rows.items())
            },
            "vectors": {
                str(key): struct.pack(f"<{len(vector)}f", *vector).hex()
                for key, vector in sorted(self._vectors.items())
            },
            "next_key": self._next_key,
            "removed": self._removed,
            "model_tag": self._tag().render(),
        }

    def _load_body(self, body: dict[str, Any]) -> None:
        """
        Restore the vectors, refusing a tag that does not match.

        Raises:
            IndexModelMismatchError: If the stored tag differs from the configured embedder's. Refused rather than
                degraded: the two spaces are unrelated, so the cosines between them are noise, and ranking on noise is
                worse than having no vector index at all.
        """
        self._reset()
        stored = str(body.get("model_tag", ""))
        current = self._tag().render()
        if stored and stored != current:
            raise IndexModelMismatchError(
                f"the vector index for {self.memory_type.value} cannot be used: {explain_mismatch(stored, current)}"
            )

        for key, row in body.get("rows", {}).items():
            block_id, space, position, span, key_hex = row
            self._rows[int(key)] = (
                block_id,
                space,
                int(position),
                tuple(span) if span else None,
                bytes.fromhex(key_hex),
            )
        for key, packed in body.get("vectors", {}).items():
            raw = bytes.fromhex(packed)
            self._vectors[int(key)] = struct.unpack(f"<{len(raw) // 4}f", raw)
        self._next_key = int(body.get("next_key", len(self._rows)))
        self._removed = int(body.get("removed", 0))
        self._build_engine(SPACE_TEXT)

    def dump(self) -> bytes:
        """
        The bytes that travel, which are exactly the bytes on disk.

        Returns:
            bytes: The complete file body.

        Raises:
            DistributionError: If the index holds nothing. The protocol is explicit that publishing a layer that claims
                a vector index and carries none is worse than omitting it -- a consumer can detect absence and cannot
                detect emptiness.
        """
        if not self._rows:
            raise DistributionError(
                f"the vector index for {self.memory_type.value} holds nothing, and an empty travelling layer is worse "
                f"than an absent one: a consumer can detect absence and cannot detect emptiness"
            )
        from vitruvio.indices import format as fmt

        return fmt.encode(self.header(), self._dump_body())

    def load(self, data: bytes) -> None:
        """
        Restore from bytes that travelled.

        Args:
            data (bytes): What ``dump`` produced.

        Raises:
            IndexFormatError: If the bytes are not a vitruvio index.
            IndexModelMismatchError: If the model tag does not match.
        """
        from vitruvio.indices import format as fmt

        header, body = fmt.decode(data)
        if header.kind != self.KIND.value:
            from vitruvio.indices.format import IndexFormatError

            raise IndexFormatError(f"expected a vector index, got {header.kind}")
        self._load_body(body)
        self._table = type(self._table)(sorted({row[0] for row in self._rows.values()}))
        self._bound_root = header.merkle_root
        self._built_at = header.built_at

    # --- Query ----------------------------------------------------------------

    def _exact_search(self, space: str, probe: Vector, limit: int) -> list[int]:
        """
        Brute-force cosine over one space. Exact, and the fallback when no graph was built.

        A dot product, which is cosine **only because every vector here is L2-normalized** -- the tag says so, and
        :meth:`_load_body` refuses an index whose tag differs from the configured embedder's, so the probe and the
        stored vectors always come from the same model. :attr:`queryable` asserts the half of that the tag check
        cannot: that the model in question normalizes at all.

        ``strict=True`` on the zip for the same reason. The widths cannot differ once the tag matches, so a mismatch
        is a broken invariant rather than a short vector to pad over -- and truncating one silently would rank by a
        prefix of the embedding and still call it cosine.
        """
        scored = [
            (key, sum(a * b for a, b in zip(probe, vector, strict=True)))
            for key, vector in self._vectors.items()
            if self._rows[key][1] == space
        ]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return [key for key, _ in scored[:limit]]

    def _approximate_search(self, space: str, probe: Vector, limit: int, effort: int) -> list[int]:
        """One HNSW probe, falling back to exact when no graph exists."""
        engine = self._engines.get(space)
        if engine is None:
            return self._exact_search(space, probe, limit)
        import numpy as np

        matches = engine.search(np.asarray(probe, dtype=np.float32), limit)
        return [int(key) for key in matches.keys]

    def lookup(self, query: VectorQuery, limit: int = 10) -> list[tuple[str, float, int, tuple[int, int] | None]]:
        """
        Search one space and group multi-chunk hits back to blocks.

        A block's score is the **max** over its chunks. Not the sum, which favours a long document -- a 200-chunk PDF
        would beat a perfect one-liner -- and not the mean, which punishes one. Max answers the question actually being
        asked: does this block contain a passage that matches?

        Args:
            query (VectorQuery): Text or a pre-computed probe, the space, the effort, and an optional mask.
            limit (int): How many blocks to return.

        Returns:
            list[tuple[str, float, int, tuple[int, int] | None]]: Block identity, score, which chunk matched, and its
            character span -- the span is what lets a citation point at a passage.

        Raises:
            EmbedderUnavailableError: If a probe vector is needed and cannot be made.
        """
        probe: Vector
        if query.vector is not None:
            probe = query.vector
        elif query.text:
            probe = self.embedder.embed_text([query.text], role=TextRole.QUERY)[0]
        else:
            return []

        pool = max(limit * OVERSAMPLE, limit)
        keys = (
            self._exact_search(query.space, probe, pool)
            if query.exact or not self._engines.get(query.space)
            else self._approximate_search(query.space, probe, pool, query.effort)
        )

        allowed = set(query.allow) if query.allow is not None else None
        best: dict[str, tuple[float, int, tuple[int, int] | None]] = {}
        for key in keys:
            row = self._rows.get(key)
            if row is None:
                continue
            block_id, _, position, span, _ = row
            if allowed is not None and (self._table.ordinal(block_id) not in allowed):
                continue
            vector = self._vectors[key]
            score = max(0.0, sum(a * b for a, b in zip(probe, vector, strict=False)))
            held = best.get(block_id)
            if held is None or score > held[0]:
                best[block_id] = (score, position, span)

        ordered = sorted(best.items(), key=lambda item: (-item[1][0], item[0]))
        return [(block_id, score, position, span) for block_id, (score, position, span) in ordered[:limit]]

    def search(self, query: Any, limit: int = 10) -> list[tuple[Any, float]]:
        """
        The SDK's entry point.

        Args:
            query (Any): A :class:`~vitruvio.indices.queries.VectorQuery`, or a bare query string.
            limit (int): How many to return.

        Returns:
            list[tuple[Any, float]]: Block identities and scores.
        """
        from boltzmann.identity.digest import BlockId

        if isinstance(query, str):
            query = VectorQuery(text=query)
        if not isinstance(query, VectorQuery):
            return []
        return [(BlockId.parse(block_id), score) for block_id, score, _, _ in self.lookup(query, limit)]

    def project_2d(self, text: str, identities: Sequence[str], *, limit: int = 20) -> dict[str, Any]:
        """Project the query and representative result vectors into two dimensions.

        Each result contributes the chunk whose cosine is highest against the query, matching the max-over-chunks
        rule used by :meth:`lookup`. PCA is only a view over those real embeddings; coordinates are normalized for a
        terminal canvas and must not be read as scores.

        Returns:
            dict[str, Any]: Source dimensionality, projection method and normalized query/result points.
        """
        if not text.strip() or not self.queryable:
            return {"dimensions": 0, "method": "pca", "points": []}

        probe = tuple(self.embedder.embed_text([text], role=TextRole.QUERY)[0])
        wanted = list(dict.fromkeys(str(identity) for identity in identities))[: max(0, limit)]
        wanted_set = set(wanted)
        best_by_identity: dict[str, tuple[float, int, tuple[float, ...]]] = {}
        # One pass over the vector table, not one pass per result. Diagnostics must stay bounded on the million-vector
        # spaces where a terminal view is most useful.
        for key, row in self._rows.items():
            identity = row[0]
            if identity not in wanted_set or row[1] != SPACE_TEXT:
                continue
            vector = self._vectors[key]
            score = sum(a * b for a, b in zip(probe, vector, strict=False))
            held = best_by_identity.get(identity)
            if held is None or score > held[0]:
                best_by_identity[identity] = (score, row[2], vector)
        representatives = [
            (identity, best_by_identity[identity][2], best_by_identity[identity][1])
            for identity in wanted
            if identity in best_by_identity
        ]

        vectors = [probe, *(vector for _, vector, _ in representatives)]
        if not vectors:
            return {"dimensions": 0, "method": "pca", "points": []}

        import numpy as np

        matrix = np.asarray(vectors, dtype=np.float64)
        dimensions = int(matrix.shape[1]) if matrix.ndim == 2 else 0
        centred = matrix - matrix.mean(axis=0, keepdims=True)
        if len(vectors) == 1 or not np.any(centred):
            coordinates = np.zeros((len(vectors), 2), dtype=np.float64)
        else:
            _left, _singular, axes = np.linalg.svd(centred, full_matrices=False)
            projected = centred @ axes[: min(2, len(axes))].T
            coordinates = np.zeros((len(vectors), 2), dtype=np.float64)
            coordinates[:, : projected.shape[1]] = projected
            # SVD axes have arbitrary signs. Fix each sign from the first largest-magnitude point so repeated views
            # of unchanged vectors do not mirror themselves.
            for axis in range(2):
                pivot = int(np.argmax(np.abs(coordinates[:, axis])))
                if coordinates[pivot, axis] < 0:
                    coordinates[:, axis] *= -1
            span = np.max(np.abs(coordinates), axis=0)
            span[span == 0] = 1.0
            coordinates /= span

        points: list[dict[str, Any]] = [
            {
                "role": "query",
                "block_id": None,
                "chunk": None,
                "x": float(coordinates[0, 0]),
                "y": float(coordinates[0, 1]),
            }
        ]
        for position, (identity, _vector, chunk_position) in enumerate(representatives, start=1):
            points.append(
                {
                    "role": "result",
                    "block_id": identity,
                    "chunk": chunk_position,
                    "x": float(coordinates[position, 0]),
                    "y": float(coordinates[position, 1]),
                }
            )
        return {"dimensions": dimensions, "method": "pca", "points": points}

    def locator_for(self, block_id: str, position: int, span: tuple[int, int] | None) -> str:
        """
        A citation string for one chunk.

        Plain text, which is exactly what ``SourceRef.locator`` and ``DerivationRecord.locator`` are -- so the payoff of
        chunking reaches the Evidence Bundle without inventing a new type: a citation points at a passage rather than at
        a whole document.

        Args:
            block_id (str): Which block.
            position (int): Which chunk.
            span (tuple[int, int] | None): Its character range.

        Returns:
            str: e.g. ``chunk:3#1600-3200``.
        """
        if span is None:
            return f"chunk:{position}"
        return f"chunk:{position}#{span[0]}-{span[1]}"
