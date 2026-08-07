"""The cost model: microseconds, selectivity, and recall as part of the objective.

**Cost is estimated microseconds of wall time**, not an abstract page-fetch unit. The operators here span a dict
probe, a posting-list walk, a blob read and a neural forward pass; any synthetic unit would need conversion factors
anyway, and being honest about what is predicted is what lets ``EXPLAIN ANALYZE`` *validate* the model against
actuals instead of merely displaying it.

The most consequential constant is :attr:`Calibration.embed_text`. Embedding one query costs roughly 4.5 ms
locally, so **on a module of a couple of hundred blocks, embedding the query costs more than reading every block in
it.** "Natural language means use the vector index" is simply wrong there, and only a cost model notices. That
single number is the argument for costing plans rather than routing them by heuristic.

**Recall is part of the objective, not a hope.** A cheap plan that misses the answer is wrong. So expected recall
is estimated per plan, floored per intent as a hard constraint, and traded against latency explicitly -- and the
"no single index is authoritative" invariant enters as a *validity rule* that pruning cannot reach around rather
than as a preference the optimiser could sell.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from vitruvio.planner.ir import Estimates, Op

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from vitruvio.planner.ir import Plan
    from vitruvio.stats import ModuleStats


@dataclass(frozen=True, slots=True)
class Calibration:
    """
    Per-unit costs in microseconds, measurable and overridable.

    Defaults are measured on a laptop SSD with CPython 3.11. They are configuration rather than constants because a
    brain on a network filesystem and a brain on local NVMe do not agree about what resolving a block costs, and a
    cost model calibrated for the wrong machine chooses the wrong plans confidently.

    Attributes:
        dict_probe (float): One hash-map lookup.
        btree_seek (float): One ordered-array descent.
        btree_step (float): One sequential step within a range.
        term_seek (float): Opening one postings list.
        posting (float): Decoding one posting.
        bitmap_seek (float): Opening one bitmap.
        bitmap_word (float): Scanning one 64-bit word.
        distance_per_dim (float): One distance computation, per dimension.
        vector_setup (float): Fixed overhead of a vector-index call.
        embed_text (float): One query embedding. The number that reshapes plans on small modules.
        get_block (float): Reading a block from the store and validating it. Two orders of magnitude above a dict
            probe, which is why a residual filter is expensive and pushdown is worth costing.
        proof (float): Assembling one inclusion proof.
        hash_node (float): One 64-byte hash, for verifying a proof path.
        graph_edge (float): One adjacency traversal. No decode: the graph index holds the edges.
        fuse (float): One fusion contribution.
        compare (float): One heap comparison.
        substring (float): One term tested against one block's text, for a sequential scan.
        ledger (float): Building the provenance view, per provenance block. Cached per root, and reported in
            EXPLAIN, because otherwise the planner gets blamed for latency it did not cause.
    """

    dict_probe: float = 0.3
    btree_seek: float = 2.0
    btree_step: float = 0.15
    term_seek: float = 1.0
    posting: float = 0.05
    bitmap_seek: float = 0.5
    bitmap_word: float = 0.002
    distance_per_dim: float = 2.1e-4
    vector_setup: float = 30.0
    embed_text: float = 4500.0
    get_block: float = 45.0
    proof: float = 9.0
    hash_node: float = 0.6
    graph_edge: float = 0.4
    fuse: float = 0.15
    compare: float = 0.02
    substring: float = 0.9
    ledger: float = 45.0

    def digest(self) -> str:
        """A short hash of these constants, so a plan cache entry is invalidated when they change."""
        import hashlib

        rendered = ",".join(f"{name}={getattr(self, name)}" for name in sorted(self.__slots__))
        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:12]


DEFAULT_CALIBRATION = Calibration()
"""The measured defaults."""

OVERLAP = 0.4
"""Assumed overlap between two generators' candidate sets.

Generators overlap heavily on a *good* query -- that is the point of fusing them -- so treating their outputs as
independent badly over-estimates the union. Refit from ``EXPLAIN ANALYZE`` samples.
"""

BRUTE_THRESHOLD = 2048
"""Below this many in-mask vectors, prefer an exact scan over an approximate probe. See :func:`vector_recall`."""


def damped_conjunction(selectivities: Sequence[float]) -> float:
    """
    Combine conjunctive selectivities with exponential backoff.

    Independence under-estimates cardinality for correlated predicates, and the failure mode that hurts here is
    **recall**, not runtime: an under-estimated candidate pool makes the planner choose a ``k`` too small to contain
    the answer. So the most selective predicate counts fully, the next at its square root, the next at its fourth
    root -- biased toward over-estimating, which is the safe direction, because over-estimating costs latency while
    under-estimating costs the answer.

    Args:
        selectivities (Sequence[float]): Per-predicate selectivities in ``[0, 1]``.

    Returns:
        float: The combined selectivity.
    """
    if not selectivities:
        return 1.0
    ordered = sorted(selectivities)
    combined = 1.0
    for position, value in enumerate(ordered):
        combined *= max(value, 1e-9) ** (1 / (2**position))
    return min(1.0, combined)


def damped_union(cardinalities: Sequence[int], overlap: float = OVERLAP) -> int:
    """
    Combine candidate-set sizes for a fusion node.

    Damped in the *opposite* direction from a conjunction: generators overlap, so an independent union
    over-estimates. The largest set counts fully and the others contribute what is assumed not to overlap it.

    Args:
        cardinalities (Sequence[int]): Per-generator output sizes.
        overlap (float): Assumed overlap fraction.

    Returns:
        int: The expected union size.
    """
    if not cardinalities:
        return 0
    largest = max(cardinalities)
    remainder = sum(cardinalities) - largest
    return int(largest + remainder * (1 - overlap))


def vector_recall(stats: ModuleStats, space: str, effort: int, mask_selectivity: float) -> float:
    """
    Expected recall from an approximate vector probe, given a pre-filter.

    The subtle part, and the one that produces a real planning decision: feeding a mask into HNSW does **not**
    preserve recall. The graph walk visits a fixed number of nodes -- ``ef`` of them -- and only about
    ``mask_selectivity`` of those survive the filter, so the effective search effort collapses as the mask tightens.
    Modelled as ``ef_eff = ef * selectivity``.

    The consequence is that as a filter gets more selective, a filtered approximate probe gets *worse*, until an
    exact scan of the masked vectors is both cheaper and perfectly accurate. The planner switches on that
    crossover, and it switches because the model says so rather than because a threshold was hard-coded.

    Args:
        stats (ModuleStats): Carries the measured recall curve.
        space (str): Which embedding space.
        effort (int): The requested ``ef_search``.
        mask_selectivity (float): Fraction of the module the mask admits.

    Returns:
        float: Expected recall in ``[0, 1]``.
    """
    view = stats.vectors.get(space)
    if view is None:
        return 0.0
    effective = max(1, int(effort * max(mask_selectivity, 1e-6)))
    return view.recall_at(effective)


@dataclass(frozen=True, slots=True)
class Objective:
    """
    How a plan's cost and recall combine into one number to minimise.

    Three layers, and the layering is what lets pruning be aggressive without ever reaching a plan that violates
    the protocol:

    1. **A validity rule** that is never costed and never traded -- see :meth:`admissible`. Plans that treat a
       single index as authoritative are structurally excluded, so no amount of pruning can arrive at one.
    2. **A recall floor** per intent, as a feasibility constraint. Quality is a question of whether a plan is
       allowed at all, not a price the optimiser can pay.
    3. **The weighted sum**, to choose among plans that are already correct.

    Attributes:
        weight (float): ``lambda`` on the recall term. Zero optimises for latency alone, which is useful mainly for
            demonstrating what that costs.
        miss_cost (float): What a missed answer is declared to be worth, in microseconds.
    """

    weight: float = 1.0
    miss_cost: float = 250_000.0

    def score(self, cost: float, recall: float) -> float:
        """
        ``J = cost + lambda * (1 - recall) * miss_cost``.

        Args:
            cost (float): Estimated microseconds.
            recall (float): Estimated recall in ``[0, 1]``.

        Returns:
            float: The objective, lower being better.
        """
        return cost + self.weight * (1.0 - recall) * self.miss_cost

    @staticmethod
    def admissible(plan: Plan, available_generators: int, *, exact_intent: bool) -> tuple[bool, str | None]:
        """
        Whether a plan may be considered at all.

        The rule: **no plan with fewer than two scored generators is admissible when two or more are available and
        the intent is not an exact lookup.** This is the protocol's "no single index may be treated as
        authoritative", expressed structurally. It is checked rather than weighted, because a weight is something an
        optimiser can sell for enough latency.

        An exact identity lookup is exempt: a digest has one answer, and there is nothing for a second opinion to
        add.

        Args:
            plan (Plan): The candidate.
            available_generators (int): How many distinct generators the installed indices could supply.
            exact_intent (bool): Whether the query is an exact identity lookup.

        Returns:
            tuple[bool, str | None]: Whether it is admissible, and why not.
        """
        if exact_intent:
            return True, None
        kinds = {plan[node_id].op for node_id in plan.generators()}
        if kinds == {Op.SEQ_SCAN}:
            # An exhaustive scan reads every block, so it treats no index as authoritative -- it treats none as
            # anything. Rejecting it broke monotonicity: installing a second index made the *chosen* plan worse,
            # because the cheap perfect-recall plan became inadmissible. Whether to scan or to probe is a cost
            # question, and it belongs to the objective.
            return True, None
        if available_generators >= 2 and len(kinds) < 2:
            named = ", ".join(sorted(kind.value for kind in kinds)) or "none"
            return False, (
                f"only {len(kinds)} scored generator ({named}) with {available_generators} available: "
                "no single index may be treated as authoritative"
            )
        return True, None


def estimate(
    plan: Plan,
    statistics: Mapping[str, ModuleStats],
    calibration: Calibration = DEFAULT_CALIBRATION,
) -> Estimates:
    """
    Cost and size every node in a plan.

    Nodes are visited in arena order, which is construction order, so a node's inputs are always already estimated:
    the builder cannot produce a node before the nodes it consumes.

    Args:
        plan (Plan): The plan.
        statistics (Mapping[str, ModuleStats]): Per-scope statistics.
        calibration (Calibration): Per-unit costs.

    Returns:
        Estimates: Rows, cost and recall per node.
    """
    rows: list[int] = []
    cost: list[float] = []
    recall: list[float | None] = []
    notes: list[tuple[str, ...]] = []
    embedded = False

    for node in plan.nodes:
        stats = statistics.get(node.scope or "", None)
        cardinality = stats.cardinality if stats else 0
        parameters = node.parameters
        inputs = [rows[node_id] for node_id in node.inputs]
        node_notes: list[str] = []
        node_recall: float | None = None

        if node.op is Op.EMPTY:
            produced, spent = 0, 0.0
            node_notes.append("pruned: a predicate proved unsatisfiable")

        elif node.op is Op.SEQ_SCAN:
            terms = int(parameters.get("terms", 0))
            produced = int(cardinality * float(parameters.get("selectivity", 1.0)))
            spent = cardinality * calibration.get_block + cardinality * terms * calibration.substring
            node_recall = 1.0
            node_notes.append("exhaustive: recall is 1.0 by construction")

        elif node.op is Op.EXACT_LOOKUP:
            count = int(parameters.get("count", 1))
            produced, spent = count, count * calibration.dict_probe
            node_recall = 1.0

        elif node.op is Op.RANGE_SCAN:
            produced = int(parameters.get("rows", cardinality))
            spent = calibration.btree_seek + produced * calibration.btree_step

        elif node.op is Op.BITMAP_FILTER:
            clauses = int(parameters.get("clauses", 1))
            produced = int(parameters.get("rows", cardinality))
            spent = clauses * (calibration.bitmap_seek + math.ceil(max(1, cardinality) / 64) * calibration.bitmap_word)
            if parameters.get("exact"):
                node_notes.append("measured, not interpolated: a bitmap intersection is a count")

        elif node.op is Op.TERM_SCAN:
            postings = int(parameters.get("postings", cardinality))
            terms = int(parameters.get("terms", 1))
            limit = int(parameters.get("k", 10))
            produced = min(limit, int(parameters.get("rows", cardinality)))
            spent = (
                terms * calibration.term_seek
                + postings * calibration.posting
                + postings * math.log2(max(2, limit)) * calibration.compare
            )
            node_recall = float(parameters.get("recall", 0.95))

        elif node.op in {Op.VECTOR_SEARCH, Op.BRUTE_VECTOR}:
            dimensions = int(parameters.get("dimensions", 384))
            available = int(parameters.get("vectors", cardinality))
            limit = int(parameters.get("k", 10))
            probe = 0.0 if embedded else calibration.embed_text
            embedded = True
            if node.op is Op.VECTOR_SEARCH:
                effort = int(parameters.get("effort", 64))
                spent = (
                    probe
                    + calibration.vector_setup
                    + calibration.distance_per_dim * dimensions * effort * math.log2(max(2, available))
                )
                node_recall = float(parameters.get("recall", 0.9))
            else:
                spent = probe + available * calibration.distance_per_dim * dimensions
                node_recall = 1.0
                node_notes.append(f"exact scan of {available} masked vectors: recall 1.0")
            produced = min(limit, available)

        elif node.op is Op.MASK_APPLY:
            produced = int(inputs[0] * float(parameters.get("selectivity", 1.0))) if inputs else 0
            spent = (inputs[0] if inputs else 0) * calibration.dict_probe

        elif node.op is Op.RESIDUAL:
            incoming = inputs[0] if inputs else 0
            produced = int(incoming * float(parameters.get("selectivity", 1.0)))
            per_row = calibration.get_block if parameters.get("decode") else calibration.dict_probe
            spent = incoming * per_row
            if parameters.get("decode"):
                node_notes.append(
                    f"decodes {incoming} blocks: {calibration.get_block / calibration.bitmap_word:.0f}x a bitmap word"
                )

        elif node.op is Op.GRAPH_EXPAND:
            seeds = inputs[0] if inputs else 0
            depth = int(parameters.get("depth", 1))
            reach = float(parameters.get("reach", 1.0))
            degree = float(parameters.get("degree", 1.0))
            produced = min(cardinality or seeds, int(seeds * reach))
            spent = seeds * degree * depth * calibration.graph_edge
            node_recall = float(parameters.get("recall", 0.7))

        elif node.op is Op.FUSE:
            produced = damped_union(inputs)
            spent = sum(inputs) * calibration.fuse

        elif node.op is Op.TOP_K:
            incoming = inputs[0] if inputs else 0
            keep = int(parameters.get("k", 10)) + int(parameters.get("reserve", 0))
            produced = min(keep, incoming)
            spent = incoming * math.log2(max(2, keep)) * calibration.compare

        elif node.op is Op.MERGE_MODULES:
            produced = sum(inputs)
            spent = produced * calibration.fuse

        elif node.op is Op.ACCESSIBILITY:
            incoming = inputs[0] if inputs else 0
            produced = int(incoming * (1 - float(parameters.get("hidden", 0.0))))
            # A dict probe per row, and nothing for the provenance view: that is built once and cached per provenance
            # root, and it is reported separately as `prelude_us`. Charging it here inflated every candidate equally --
            # which left the ranking correct and made EXPLAIN claim a 136ms operator that does not exist.
            spent = incoming * calibration.dict_probe

        elif node.op is Op.RESOLVE:
            produced = inputs[0] if inputs else 0
            spent = produced * calibration.get_block

        elif node.op is Op.VERIFY:
            incoming = inputs[0] if inputs else 0
            produced = incoming
            spent = incoming * (calibration.proof + math.log2(max(2, cardinality or 2)) * calibration.hash_node)

        else:  # Op.BUNDLE
            produced = inputs[0] if inputs else 0
            spent = 0.0

        rows.append(max(0, produced))
        cost.append(max(0.0, spent))
        recall.append(node_recall)
        notes.append(tuple(node_notes))

    return Estimates(rows=tuple(rows), cost=tuple(cost), recall=tuple(recall), notes=tuple(notes))


def plan_recall(plan: Plan, estimates: Estimates, authority: Mapping[str, float]) -> float:
    """
    Expected recall for a whole plan.

    Composed so that adding a generator can only help:

    ``miss = product over generators of (1 - authority_g * coverage_g)``, and ``recall = 1 - miss``.

    With every ``authority_g`` strictly below 1, a multi-generator plan is *strictly* better in expected recall than
    any single-generator plan. That is the point: it puts "no single index is authoritative" into the objective
    itself rather than bolting it on, so the optimiser's own arithmetic prefers a plan that consults more than one
    index.

    Args:
        plan (Plan): The plan.
        estimates (Estimates): Per-node recall.
        authority (Mapping[str, float]): Per-operator prior on how much of the answer set that generator can see.

    Returns:
        float: Expected recall in ``[0, 1]``.
    """
    miss = 1.0
    found = False
    for node_id in plan.generators():
        coverage = estimates.recall[node_id]
        if coverage is None:
            continue
        found = True
        weight = authority.get(plan[node_id].op.value, 0.5)
        miss *= 1.0 - min(1.0, weight * coverage)
    if not found:
        return 0.0
    return 1.0 - miss
