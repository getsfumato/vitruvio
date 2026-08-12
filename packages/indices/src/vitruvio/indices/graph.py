"""The graph index: typed-edge traversal over CSR adjacency arrays.

**Why CSR and not networkx.** What is needed here is typed-edge breadth-first search with a decay, and nothing else.
networkx would bring a dict-of-dicts, a dependency, and a pickle format nobody wants -- and every one of its algorithms
would have to be constrained anyway (a node ceiling, per-edge-kind filtering). Two parallel integer arrays are smaller,
faster, and byte-reproducible, because the node order is the canonical sorted-identity order.

**Two things this index knows that no other does.** ``derived_from`` and ``supersedes`` exist only inside a
``ProvenanceBlock``, so a semantic module's index never sees them -- and a *derived* module's index cannot answer "what
was this derived from". The answer is not to share one index across modules, which would make its root binding
meaningless, but to register one per module and union them at query time: :class:`FederatedGraphView`.

**External targets are information, not errors.** A citation pointing at a block outside this install is exactly what a
selective install produces, and reporting it is more useful than dropping it: "the evidence you asked about is not
installed" is an answer.

**Scoring is a max over paths with a per-hop decay.** Every edge weight is at most 1 and the decay is below 1, so path
score is monotonically non-increasing -- which makes a best-first search with a visited-max map exact, and means no
Dijkstra is needed. It is also a strict improvement on the SDK's own scan, which assigns 0.0 to everything reached by
association and therefore cannot rank an expansion at all.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from typing import Any, ClassVar

from boltzmann.indices.base import IndexKind

from vitruvio.indices.base import VitruvioIndex
from vitruvio.indices.projection import EdgeKind, Projection
from vitruvio.indices.queries import TraversalQuery
from vitruvio.stats import GraphStats

SEED_SCORE = 1.0
"""What a seed scores. It was already found; the expansion is about what it leads to."""

REACH_SAMPLE = 128
"""How many nodes to BFS from when measuring frontier growth.

Measured rather than derived from mean degree, because ``degree ** depth`` is wrong by orders of magnitude in a real
knowledge graph: frontiers overlap heavily, since concepts cite the same evidence. Measuring is cheap and turns graph
costing from fiction into arithmetic.
"""

MAX_MEASURED_DEPTH = 4
"""How deep to measure. Beyond this the planner labels its estimate a guess rather than an interpolation."""


class GraphIndex(VitruvioIndex):
    """
    Typed traversal over one module's edges, with the transpose for inbound queries.

    Attributes:
        memory_type (MemoryType): Which module this indexes.
    """

    KIND: ClassVar[IndexKind] = IndexKind.GRAPH
    REBUILDABLE: ClassVar[bool] = True
    BODY_VERSION: ClassVar[int] = 1
    ENGINE: ClassVar[str] = "csr"

    def _reset(self) -> None:
        """Discard the edge set and the adjacency arrays."""
        # Held as an edge list during the build and compiled into CSR at the end: a counting sort over the whole set is
        # cheaper than inserting into adjacency arrays one edge at a time.
        self._edges: list[tuple[str, str, str, str | None, float]] = []
        self._nodes: list[str] = []
        self._position: dict[str, int] = {}
        self._out: dict[int, list[int]] = {}
        self._into: dict[int, list[int]] = {}
        self._kinds: dict[tuple[int, int], tuple[str, str | None, float]] = {}
        self._external: set[int] = set()

    def _apply(self, projection: Projection) -> None:
        """Collect this block's outgoing edges."""
        for edge in projection.edges:
            self._edges.append((projection.block_id, edge.target, edge.kind.value, edge.predicate, edge.weight))

    def _on_build_end(self, delta: Any) -> None:
        """Compile the edge list into adjacency arrays, in canonical node order."""
        members = set(self._table.identities)
        mentioned = {source for source, _, _, _, _ in self._edges} | {target for _, target, _, _, _ in self._edges}
        # Sorted, so the node numbering -- and therefore the serialized form -- depends only on the set of identities.
        self._nodes = sorted(members | mentioned)
        self._position = {identity: position for position, identity in enumerate(self._nodes)}
        self._external = {self._position[identity] for identity in self._nodes if identity not in members}

        for source, target, kind, predicate, weight in self._edges:
            tail, head = self._position[source], self._position[target]
            self._out.setdefault(tail, []).append(head)
            self._into.setdefault(head, []).append(tail)
            self._kinds[(tail, head)] = (kind, predicate, weight)

        for adjacency in (self._out, self._into):
            for node in adjacency:
                adjacency[node] = sorted(set(adjacency[node]))

    # --- Reporting ------------------------------------------------------------

    @property
    def population(self) -> int:
        """
        How many blocks of *this module* the index covers.

        Not the node count: the node set includes external targets, which are mentioned rather than held. Reporting them
        as population would make a module of three blocks with fifty citations look like a module of fifty-three.
        """
        return len(self._table)

    def _capability_extra(self) -> dict[str, Any]:
        """Which edge kinds and predicates are present, so the planner can tell a traversal is answerable."""
        return {
            "keys": tuple(sorted({kind for kind, _, _ in self._kinds.values()})),
        }

    def _fragment_extra(self) -> dict[str, Any]:
        """The degree distribution and the **measured** frontier growth."""
        out_degrees = [len(self._out.get(node, ())) for node in range(len(self._nodes))]
        predicates: dict[str, int] = {}
        per_kind: dict[str, int] = {}
        for kind, predicate, _ in self._kinds.values():
            per_kind[kind] = per_kind.get(kind, 0) + 1
            if predicate:
                predicates[predicate] = predicates.get(predicate, 0) + 1

        return {
            "graph": GraphStats(
                nodes=len(self._nodes),
                edges=len(self._kinds),
                external_nodes=len(self._external),
                out_degree_mean=(sum(out_degrees) / len(out_degrees)) if out_degrees else 0.0,
                out_degree_max=max(out_degrees, default=0),
                predicates={**predicates, **per_kind},
                reach_by_depth=self._measure_reach(),
            )
        }

    def _measure_reach(self) -> tuple[float, ...]:
        """
        Mean frontier size at each depth, measured by BFS from a sample.

        This is what makes ``GraphExpand`` costable. ``mean_degree ** depth`` over-estimates by orders of magnitude,
        because a real knowledge graph's frontiers overlap -- several concepts cite the same evidence -- and the
        overlap is exactly what a sample measures and a formula cannot.
        """
        if not self._nodes or not self._kinds:
            return ()
        step = max(1, len(self._nodes) // REACH_SAMPLE)
        sample = list(range(0, len(self._nodes), step))[:REACH_SAMPLE]

        totals = [0] * MAX_MEASURED_DEPTH
        for start in sample:
            seen = {start}
            frontier = [start]
            for depth in range(MAX_MEASURED_DEPTH):
                nextfrontier: list[int] = []
                for node in frontier:
                    for neighbour in self._out.get(node, ()):
                        if neighbour not in seen:
                            seen.add(neighbour)
                            nextfrontier.append(neighbour)
                totals[depth] += len(seen) - 1
                frontier = nextfrontier
                if not frontier:
                    # The remaining depths reach nothing new, so the cumulative count carries forward -- which is the
                    # honest answer rather than zero.
                    for remaining in range(depth + 1, MAX_MEASURED_DEPTH):
                        totals[remaining] += len(seen) - 1
                    break
        return tuple(total / len(sample) for total in totals)

    def _header_extra(self) -> dict[str, Any]:
        """Node and edge counts, and how many targets point outside this module."""
        return {
            "nodes": len(self._nodes),
            "edges": len(self._kinds),
            "external": len(self._external),
            "kinds": sorted({kind for kind, _, _ in self._kinds.values()}),
        }

    def _dump_state(self) -> dict[str, Any]:
        """The node list and the edge set, both in canonical order so the bytes depend only on the graph."""
        return {
            "nodes": list(self._nodes),
            "edges": sorted(
                [source, target, kind, predicate, round(weight * 1000)]
                for source, target, kind, predicate, weight in self._edges
            ),
        }

    def _load_body(self, body: dict[str, Any]) -> None:
        """Restore the edges and recompile the adjacency."""
        self._reset()
        self._edges = [
            (source, target, kind, predicate, weight / 1000)
            for source, target, kind, predicate, weight in body.get("edges", [])
        ]
        self._table = type(self._table)(body.get("identities", []))
        self._on_build_end(None)

    # --- Query ----------------------------------------------------------------

    def expand(self, query: TraversalQuery) -> list[tuple[str, float, int]]:
        """
        Walk outward from the seeds and score what is reached.

        Best-first with a visited-max map, which is *exact* here rather than approximate: every edge weight is at most 1
        and the decay is below 1, so a path's score is monotonically non-increasing and the first time a node is reached
        by the best path is the last time its score improves. No priority queue is needed.

        Args:
            query (TraversalQuery): Seeds, depth, which edge kinds and predicates to follow, direction, decay, and a
                node ceiling.

        Returns:
            list[tuple[str, float, int]]: Block identity, score, and hop count, best first. Seeds are excluded: they were
            already found, and returning them would double-count them in fusion.
        """
        adjacency = self._into if query.inbound else self._out
        kinds = {kind.value for kind in query.kinds} if query.kinds else None
        predicates = set(query.predicates) if query.predicates else None

        best: dict[int, tuple[float, int]] = {}
        seeds = [self._position[identity] for identity in query.seeds if identity in self._position]
        pending: deque[tuple[int, float, int]] = deque((node, SEED_SCORE, 0) for node in seeds)
        origin = set(seeds)

        while pending and len(best) < query.max_nodes:
            node, score, depth = pending.popleft()
            if depth >= query.depth:
                continue
            # Sorted, so the expansion order -- and therefore any tie-break downstream -- is deterministic.
            for neighbour in adjacency.get(node, ()):
                edge = self._kinds.get((neighbour, node) if query.inbound else (node, neighbour))
                if edge is None:
                    continue
                kind, predicate, weight = edge
                if kinds is not None and kind not in kinds:
                    continue
                if predicates is not None and (predicate is None or predicate not in predicates):
                    continue

                reached = score * weight * query.decay
                held = best.get(neighbour)
                if held is not None and held[0] >= reached:
                    continue
                best[neighbour] = (reached, depth + 1)
                pending.append((neighbour, reached, depth + 1))

        results = [(self._nodes[node], score, depth) for node, (score, depth) in best.items() if node not in origin]
        results.sort(key=lambda entry: (-entry[1], entry[0]))
        return results

    def search(self, query: Any, limit: int = 10) -> list[tuple[Any, float]]:
        """
        The SDK's entry point.

        Args:
            query (Any): A :class:`~vitruvio.indices.queries.TraversalQuery`.
            limit (int): How many to return.

        Returns:
            list[tuple[Any, float]]: Block identities and scores. Identities outside this module are skipped here,
            because the SDK's contract is block identities and a caller cannot resolve one that is not installed --
            :meth:`expand` still reports them, which is where a "not installed" answer comes from.
        """
        from boltzmann.identity.digest import BlockId

        if not isinstance(query, TraversalQuery):
            return []
        reached = self.expand(query)
        return [(BlockId.parse(identity), score) for identity, score, _ in reached[:limit] if identity in self._table]

    def neighbours(self, identity: str, *, inbound: bool = False) -> list[tuple[str, str, str | None]]:
        """
        One block's immediate edges.

        Args:
            identity (str): Which block.
            inbound (bool): Traverse the transpose -- "what cites this" rather than "what does this cite".

        Returns:
            list[tuple[str, str, str | None]]: Target identity, edge kind, and predicate.
        """
        node = self._position.get(identity)
        if node is None:
            return []
        adjacency = self._into if inbound else self._out
        found: list[tuple[str, str, str | None]] = []
        for neighbour in adjacency.get(node, ()):
            edge = self._kinds.get((neighbour, node) if inbound else (node, neighbour))
            if edge is not None:
                found.append((self._nodes[neighbour], edge[0], edge[1]))
        return found

    def external(self) -> list[str]:
        """
        Targets this module cites but does not hold.

        Information rather than an error: a selective install produces exactly this, and "the evidence you asked about
        is not installed here" is an answer worth giving.

        Returns:
            list[str]: Identities mentioned but not held.
        """
        return sorted(self._nodes[node] for node in self._external)


class FederatedGraphView:
    """
    Several modules' graphs, unioned at query time.

    The reason this exists rather than one shared index: ``derived_from`` and ``supersedes`` live **only** in
    ``ProvenanceBlock``, which a semantic module's index never sees. Sharing one index across memory types would leave
    its root binding meaningless -- bound to which module? -- so each stays root-bound and independently persistable, and
    the union happens here.

    Attributes:
        graphs (Mapping[str, GraphIndex]): Memory type to its graph index.
    """

    def __init__(self, graphs: Mapping[str, GraphIndex]) -> None:
        """
        Build a view over several graphs.

        Args:
            graphs (Mapping[str, GraphIndex]): The graphs to union.
        """
        self.graphs = dict(graphs)

    def expand(self, query: TraversalQuery) -> list[tuple[str, float, int]]:
        """
        Expand across every graph and merge, keeping the best score per block.

        Args:
            query (TraversalQuery): The traversal.

        Returns:
            list[tuple[str, float, int]]: Block identity, best score, and shallowest depth, best first.
        """
        best: dict[str, tuple[float, int]] = {}
        for graph in self.graphs.values():
            for identity, score, depth in graph.expand(query):
                held = best.get(identity)
                if held is None or score > held[0]:
                    best[identity] = (score, min(depth, held[1]) if held else depth)

        merged = [(identity, score, depth) for identity, (score, depth) in best.items()]
        merged.sort(key=lambda entry: (-entry[1], entry[0]))
        return merged

    def predicates(self) -> dict[str, int]:
        """Every predicate across every graph, so intent classification can recognise one in a query."""
        combined: dict[str, int] = {}
        for graph in self.graphs.values():
            stats = graph.fragment().graph
            if stats is None:
                continue
            for predicate, count in stats.predicates.items():
                combined[predicate] = combined.get(predicate, 0) + count
        return combined

    def provenance_of(self, identity: str) -> list[tuple[str, str, str | None]]:
        """
        What one block was derived from or supersedes, wherever that edge is recorded.

        The federation earning its place: this question cannot be answered from the derived module's own index, because
        the edge lives in provenance.

        Args:
            identity (str): Which block.

        Returns:
            list[tuple[str, str, str | None]]: Target, edge kind, and predicate.
        """
        wanted = {EdgeKind.DERIVED_FROM.value, EdgeKind.SUPERSEDES.value}
        found: list[tuple[str, str, str | None]] = []
        for graph in self.graphs.values():
            found.extend(edge for edge in graph.neighbours(identity) if edge[1] in wanted)
        return found
