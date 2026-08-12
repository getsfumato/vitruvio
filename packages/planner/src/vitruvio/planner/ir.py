"""The physical operator algebra, as an arena of nodes rather than a tree.

**Why an arena and not a nested tree.** A bitmap mask is legitimately consumed *twice* -- as a pre-filter for the
vector probe and as a post-filter for the term scan -- and a tree would have to duplicate that node, which means
evaluating the filter twice and costing it twice. Costing also wants memoisation per node, and ``EXPLAIN ANALYZE``
wants actuals per node; both are naturally an array indexed by node id. So: ``Plan(nodes, root)`` where each node
names its inputs by index, and the builder interns on ``(kind, params, inputs)`` so a shared subexpression *is* the
same node.

**Estimates live outside the operators.** A node's identity is a function of the query shape alone; its cost is a
function of ``(plan, statistics, calibration)``. Keeping them apart is what makes the plan cache honest -- the
structure is reusable across queries of the same shape, and the numbers are re-derived from whatever statistics are
current.

**Two output types, and the distinction is load-bearing.** A ``Mask`` is a set of ordinals; ``Rows`` are scored
candidates. Pushdown is only expressible because they are different things: a mask can be handed *into* a
generator, while a residual predicate has to consume rows and decode blocks -- which costs roughly 150 to 900 times
as much per row. That ratio is the entire reason the pushdown rules exist.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

NodeId = int
"""A node's position in the arena."""


class Output(StrEnum):
    """What an operator produces. Not interchangeable."""

    MASK = "mask"
    """A set of ordinals. Can be pushed into a generator."""
    ROWS = "rows"
    """Scored candidates. Must be consumed."""
    BUNDLE = "bundle"
    """The finished Evidence Bundle."""


class Op(StrEnum):
    """Every physical operator.

    Two of them are where the planner is actually making a decision worth explaining:

    ``BRUTE_VECTOR`` -- below a threshold of in-mask vectors, an exact scan replaces the approximate probe. Feeding
    a very selective mask into HNSW *loses* recall, because the graph walk visits a fixed number of nodes and only
    a fraction survive the filter, so the exact scan is often both cheaper and more accurate. The cost model
    derives that rather than hard-coding it.

    ``RESIDUAL`` -- a predicate no index could evaluate, applied by decoding blocks. Its cost per row is what makes
    pushdown a costed decision instead of folklore.
    """

    SEQ_SCAN = "SeqScan"
    EXACT_LOOKUP = "ExactLookup"
    RANGE_SCAN = "RangeScan"
    TERM_SCAN = "TermScan"
    VECTOR_SEARCH = "VectorSearch"
    BRUTE_VECTOR = "BruteVector"
    BITMAP_FILTER = "BitmapFilter"
    MASK_APPLY = "MaskApply"
    RESIDUAL = "Residual"
    GRAPH_EXPAND = "GraphExpand"
    FUSE = "Fuse"
    TOP_K = "TopK"
    MERGE_MODULES = "MergeModules"
    ACCESSIBILITY = "Accessibility"
    RESOLVE = "Resolve"
    VERIFY = "Verify"
    BUNDLE = "Bundle"
    EMPTY = "Empty"

    @property
    def is_generator(self) -> bool:
        """
        Whether this operator produces *scored* candidates on its own.

        The set matters: the validity rule counts generators, and a plan with fewer than two of them when two are
        available is inadmissible. A filter is not a generator -- it narrows a candidate set rather than proposing
        one -- so counting a ``BitmapFilter`` here would let a single-authority plan through.
        """
        return self in {
            Op.TERM_SCAN,
            Op.VECTOR_SEARCH,
            Op.BRUTE_VECTOR,
            Op.GRAPH_EXPAND,
            Op.SEQ_SCAN,
        }

    @property
    def output(self) -> Output:
        """What this operator produces."""
        if self is Op.BUNDLE:
            return Output.BUNDLE
        if self in {Op.BITMAP_FILTER, Op.RANGE_SCAN}:
            return Output.MASK
        return Output.ROWS


@dataclass(frozen=True, slots=True)
class Node:
    """
    One operator instance.

    Attributes:
        op (Op): Which operator.
        scope (str | None): Which memory type it runs over. ``None`` for the cross-scope tail.
        params (tuple[tuple[str, Any], ...]): Parameters, as sorted pairs so the node hashes stably. A tuple rather
            than a dict because identity requires hashability and a dict is not hashable.
        inputs (tuple[NodeId, ...]): Which nodes feed it.
        index (str | None): Which index kind it consults, for EXPLAIN. ``None`` for a non-index operator.
    """

    op: Op
    scope: str | None = None
    params: tuple[tuple[str, Any], ...] = ()
    inputs: tuple[NodeId, ...] = ()
    index: str | None = None

    @property
    def parameters(self) -> dict[str, Any]:
        """The parameters as a mapping, for reading rather than for hashing."""
        return dict(self.params)

    def signature(self) -> str:
        """A stable string for this node, ignoring its inputs' contents."""
        rendered = ",".join(f"{name}={value!r}" for name, value in self.params)
        return f"{self.op.value}[{self.scope or '*'}]({rendered})<-{list(self.inputs)}"


@dataclass(frozen=True, slots=True)
class Plan:
    """
    A physical plan: an arena of nodes and the one that produces the result.

    Attributes:
        nodes (tuple[Node, ...]): Every operator, indexed by :data:`NodeId`.
        root (NodeId): Which node produces the bundle.
    """

    nodes: tuple[Node, ...]
    root: NodeId

    def __len__(self) -> int:
        """How many operators."""
        return len(self.nodes)

    def __getitem__(self, node_id: NodeId) -> Node:
        """One node by id."""
        return self.nodes[node_id]

    @property
    def signature(self) -> str:
        """
        A stable structural hash.

        Golden snapshots key on this, and so does the plan cache. It covers structure and parameters and nothing
        else -- deliberately not the costs, which move whenever the calibration or the statistics do.
        """
        joined = "\n".join(node.signature() for node in self.nodes)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]

    def generators(self) -> tuple[NodeId, ...]:
        """Which nodes produce scored candidates."""
        return tuple(node_id for node_id, node in enumerate(self.nodes) if node.op.is_generator)

    def indices_consulted(self) -> dict[str, list[str]]:
        """Which index kinds this plan touches, per scope, for EXPLAIN."""
        consulted: dict[str, set[str]] = {}
        for node in self.nodes:
            if node.index and node.scope:
                consulted.setdefault(node.scope, set()).add(node.index)
        return {scope: sorted(kinds) for scope, kinds in sorted(consulted.items())}

    def dependents(self, node_id: NodeId) -> tuple[NodeId, ...]:
        """Which nodes consume this one. More than one means the arena is doing its job."""
        return tuple(other for other, node in enumerate(self.nodes) if node_id in node.inputs)

    def describe(self) -> list[str]:
        """A flat listing, for a quick look without the EXPLAIN machinery."""
        return [f"#{node_id} {node.signature()}" for node_id, node in enumerate(self.nodes)]


class PlanBuilder:
    """
    Builds a plan, interning identical nodes.

    Interning is not an optimisation for its own sake. A mask consumed by two generators must be *one* node, or it
    is evaluated twice and costed twice -- and the whole reason for an arena rather than a tree is to make that
    representable.
    """

    def __init__(self) -> None:
        """Start an empty arena."""
        self._nodes: list[Node] = []
        self._interned: dict[Node, NodeId] = {}

    def add(
        self,
        op: Op,
        *,
        scope: str | None = None,
        inputs: Iterable[NodeId] = (),
        index: str | None = None,
        **params: Any,
    ) -> NodeId:
        """
        Add a node, returning an existing id when an identical node is already present.

        Args:
            op (Op): Which operator.
            scope (str | None): Which memory type.
            inputs (Iterable[NodeId]): Feeding nodes.
            index (str | None): Which index kind it consults.
            **params: Operator parameters.

        Returns:
            NodeId: The node's id.
        """
        node = Node(
            op=op,
            scope=scope,
            params=tuple(sorted((name, value) for name, value in params.items())),
            inputs=tuple(inputs),
            index=index,
        )
        if node in self._interned:
            return self._interned[node]
        self._nodes.append(node)
        node_id = len(self._nodes) - 1
        self._interned[node] = node_id
        return node_id

    def finish(self, root: NodeId) -> Plan:
        """
        Freeze the arena.

        Args:
            root (NodeId): Which node produces the bundle.

        Returns:
            Plan: The finished plan.
        """
        return Plan(nodes=tuple(self._nodes), root=root)


@dataclass(frozen=True, slots=True)
class Estimates:
    """
    What a plan is expected to cost and to recall.

    Separate from the plan so that the same structure can be re-costed against new statistics without being
    rebuilt -- which is what lets the plan cache store shapes rather than numbers.

    Attributes:
        rows (tuple[int, ...]): Expected output cardinality per node.
        cost (tuple[float, ...]): Expected microseconds per node.
        recall (tuple[float | None, ...]): Expected recall per generator, ``None`` for a non-generator.
        notes (tuple[tuple[str, ...], ...]): Why each node was estimated as it was, for EXPLAIN.
    """

    rows: tuple[int, ...]
    cost: tuple[float, ...]
    recall: tuple[float | None, ...]
    notes: tuple[tuple[str, ...], ...] = ()

    @property
    def total_cost(self) -> float:
        """Estimated microseconds for the whole plan."""
        return sum(self.cost)


@dataclass(slots=True)
class Metrics:
    """
    What a plan actually did, recorded by the executor for ``EXPLAIN ANALYZE``.

    Attributes:
        rows (list[int | None]): Actual output cardinality per node.
        calls (list[int]): How many times each node ran.
        micros (list[float]): Wall time per node.
    """

    rows: list[int | None] = field(default_factory=list)
    calls: list[int] = field(default_factory=list)
    micros: list[float] = field(default_factory=list)

    @classmethod
    def over(cls, plan: Plan) -> Metrics:
        """Allocate one entry per node, aligned with the arena."""
        size = len(plan)
        return cls(rows=[None] * size, calls=[0] * size, micros=[0.0] * size)

    def record(self, node_id: NodeId, rows: int, micros: float) -> None:
        """
        Note what one node produced.

        Args:
            node_id (NodeId): Which node.
            rows (int): How many rows it output.
            micros (float): How long it took.
        """
        self.rows[node_id] = rows
        self.calls[node_id] += 1
        self.micros[node_id] += micros

    def error_against(self, estimates: Estimates) -> dict[int, float]:
        """
        Per-node estimation error, as ``log10(actual / estimated)``.

        A log ratio rather than a difference, because being wrong by 10 rows matters very differently at 10 rows
        and at 100 000. Anything beyond half an order of magnitude is worth a look, and this is what makes the
        cost model checkable instead of merely plausible.

        Args:
            estimates (Estimates): What was predicted.

        Returns:
            dict[int, float]: Node id to log-ratio, for the nodes that ran.
        """
        import math

        error: dict[int, float] = {}
        for node_id, actual in enumerate(self.rows):
            if actual is None:
                continue
            predicted = max(1, estimates.rows[node_id]) if node_id < len(estimates.rows) else 1
            error[node_id] = math.log10(max(1, actual) / predicted)
        return error


def rescope(node: Node, scope: str) -> Node:
    """
    The same operator over a different memory type.

    Used when a template is instantiated per scope: the shape is identical, only the module differs.

    Args:
        node (Node): The template node.
        scope (str): Which memory type.

    Returns:
        Node: A copy with the new scope.
    """
    return replace(node, scope=scope)


def parameters_of(plan: Plan, op: Op) -> Sequence[Mapping[str, Any]]:
    """
    Every instance of one operator's parameters, for asserting on a plan in a test.

    Args:
        plan (Plan): The plan.
        op (Op): Which operator.

    Returns:
        Sequence[Mapping[str, Any]]: The parameter mappings, in node order.
    """
    return [node.parameters for node in plan.nodes if node.op is op]
