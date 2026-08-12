"""Turning a query and a set of capabilities into candidate plans.

Every plan gets the same fixed tail -- ``Accessibility -> TopK -> Resolve -> Verify -> Bundle`` -- appended after
enumeration, by a function the enumerator does not see. Two of those positions are not stylistic:

* ``Accessibility`` sits **before** ``TopK``, or the limit gets spent on blocks that will then be hidden.
* ``Verify`` sits after ``Resolve`` and drops anything that fails, rather than returning it with ``verified=False``.
  ``Match.verified`` is therefore always true, which is what ``require_verified()`` presumes and what the SDK's own
  scan does.

The generator ``k`` is deliberately larger than the caller's limit: fusion needs a pool to fuse, verification may drop
rows, and a graph expansion multiplies its seeds. The over-fetch factor is configuration because the right value
depends on how much the generators disagree, which is a property of the brain.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from boltzmann.indices.base import IndexKind
from boltzmann.query.request import Query

from vitruvio.kernel import PlannerConfig
from vitruvio.planner.cost import BRUTE_THRESHOLD
from vitruvio.planner.intent import Intent, IntentKind, admissible_generators, requires
from vitruvio.planner.ir import Op, Plan, PlanBuilder
from vitruvio.planner.planner import Capabilities
from vitruvio.stats import ModuleStats

K_GRID = (1, 4)
"""Over-fetch multipliers to try, as multiples of the configured factor.

Two points, not five. The cost curve in ``k`` is smooth and shallow, so a denser grid multiplies the plan count
without changing which plan wins -- and a plan count that stays in the tens is what keeps exhaustive enumeration
honest.
"""


def build_templates(
    *,
    query: Query,
    scopes: Sequence[str],
    capabilities: Capabilities,
    intent: Intent,
    statistics: Mapping[str, ModuleStats],
    config: PlannerConfig,
) -> list[Plan]:
    """
    Enumerate the candidate plans.

    Args:
        query (Query): The query.
        scopes (Sequence[str]): Which modules to search.
        capabilities (Capabilities): What is installed and usable.
        intent (Intent): The classification.
        statistics (Mapping[str, ModuleStats]): For sizing the operators.
        config (PlannerConfig): Over-fetch, thresholds, expansion ceiling.

    Returns:
        list[Plan]: Candidates, each with the fixed tail already appended. Always at least one: the exhaustive plan
        is always expressible, so "no plan" is never an outcome.
    """
    if not scopes:
        return [_empty_plan()]

    limit = max(1, query.hints.limit)
    permitted = admissible_generators(query.hints.mode)
    mandatory = requires(query.hints.mode)

    # A navigational query has no text, so a lexical or vector generator has nothing to probe with. Enumerating those
    # plans anyway would not be wrong -- they would cost more and recall nothing, so they would lose -- but it fills
    # EXPLAIN with alternatives that were never plausible, and reading EXPLAIN is the point of having it.
    if intent.kind is IntentKind.NAVIGATIONAL:
        permitted = frozenset({"GraphExpand"}) if permitted is None else permitted & {"GraphExpand"}

    candidates: list[Plan] = []

    for multiplier in K_GRID:
        pool = max(limit, limit * config.overfetch * multiplier)
        for generators in _generator_sets(scopes, capabilities, permitted, mandatory):
            plan = _assemble(
                query=query,
                scopes=scopes,
                capabilities=capabilities,
                statistics=statistics,
                config=config,
                generators=generators,
                pool=pool,
                limit=limit,
            )
            if plan is not None:
                candidates.append(plan)

    # The exhaustive plan is always in the space. It is not a fallback: on a small module it legitimately *wins*,
    # because reading every block costs less than embedding one query -- and a planner that could not express it
    # would be forced into a worse plan there.
    candidates.append(
        _assemble(
            query=query,
            scopes=scopes,
            capabilities=capabilities,
            statistics=statistics,
            config=config,
            generators=("SeqScan",),
            pool=limit,
            limit=limit,
        )
        or _empty_plan()
    )

    unique: dict[str, Plan] = {}
    for plan in candidates:
        unique.setdefault(plan.signature, plan)
    return list(unique.values())


def _generator_sets(
    scopes: Sequence[str],
    capabilities: Capabilities,
    permitted: frozenset[str] | None,
    mandatory: frozenset[str],
) -> list[tuple[str, ...]]:
    """
    Which combinations of generators are worth costing.

    Every non-empty subset of what is available, which for four generators is fifteen sets -- small enough to cost
    exhaustively, and exhaustive enumeration is what guarantees that installing an index cannot make the chosen plan
    worse.
    """
    available: list[str] = []
    if any(capabilities.has(scope, IndexKind.INVERTED) for scope in scopes):
        available.append("TermScan")
    if any(capabilities.has(scope, IndexKind.VECTOR) for scope in scopes):
        available.append("VectorSearch")
    if any(capabilities.has(scope, IndexKind.GRAPH) for scope in scopes):
        available.append("GraphExpand")

    if permitted is not None:
        available = [name for name in available if name in permitted]

    sets: list[tuple[str, ...]] = []
    for mask in range(1, 1 << len(available)):
        chosen = tuple(name for position, name in enumerate(available) if mask & (1 << position))
        if mandatory and not (mandatory & set(chosen)) and mandatory & set(available):
            # A mode that requires the vector generator excludes plans without it -- but only when one exists. The
            # hint restricts the space; it cannot conjure an index.
            continue
        sets.append(chosen)
    return sets or [("SeqScan",)]


def _assemble(
    *,
    query: Query,
    scopes: Sequence[str],
    capabilities: Capabilities,
    statistics: Mapping[str, ModuleStats],
    config: PlannerConfig,
    generators: tuple[str, ...],
    pool: int,
    limit: int,
) -> Plan | None:
    """Build one plan: a subplan per scope, merged, then the fixed tail."""
    builder = PlanBuilder()
    scope_roots: list[int] = []

    for scope in scopes:
        stats = statistics.get(scope)
        mask = _mask_for(builder, query, scope, capabilities, stats)
        produced: list[int] = []

        # An exact lookup is always worth adding when a hash map is installed and the query has text: it is a dict
        # probe, so it costs nothing to include, and a label or digest match should outrank every similarity score.
        if capabilities.has(scope, IndexKind.HASH_MAP) and query.text.strip():
            produced.append(
                builder.add(
                    Op.EXACT_LOOKUP,
                    scope=scope,
                    index=IndexKind.HASH_MAP.value,
                    count=1,
                )
            )

        for generator in generators:
            node = _generator_node(
                builder=builder,
                generator=generator,
                scope=scope,
                mask=mask,
                stats=stats,
                capabilities=capabilities,
                config=config,
                query=query,
                pool=pool,
            )
            if node is not None:
                produced.append(node)

        if not produced:
            continue

        if len(produced) == 1:
            fused = produced[0]
        else:
            fused = builder.add(
                Op.FUSE,
                scope=scope,
                inputs=produced,
                method="rrf",
                k=config.rrf_k,
            )

        if mask is not None and any(builder_node_is_generator(builder, node) for node in produced):
            fused = builder.add(Op.MASK_APPLY, scope=scope, inputs=[fused, mask], selectivity=1.0)

        residual = _residual_for(query, scope, capabilities)
        if residual is not None:
            fused = builder.add(Op.RESIDUAL, scope=scope, inputs=[fused], **residual)

        scope_roots.append(fused)

    if not scope_roots:
        return _empty_plan()

    merged = scope_roots[0] if len(scope_roots) == 1 else builder.add(Op.MERGE_MODULES, inputs=scope_roots)
    return _with_tail(builder, merged, query=query, statistics=statistics, scopes=scopes, limit=limit)


def builder_node_is_generator(builder: PlanBuilder, node_id: int) -> bool:
    """Whether a node produces scored candidates, read back out of the builder."""
    return builder._nodes[node_id].op.is_generator


def _mask_for(
    builder: PlanBuilder,
    query: Query,
    scope: str,
    capabilities: Capabilities,
    stats: ModuleStats | None,
) -> int | None:
    """
    A pre-filter node for one scope, when the filters can be pushed down.

    Returned as a single node id even when it covers several clauses, so that two generators consuming it share the
    *same* node -- evaluated once, costed once. That sharing is the reason the IR is a DAG.
    """
    filters = query.filters
    clauses = 0
    rows = stats.cardinality if stats else 0
    exact = False

    if capabilities.has(scope, IndexKind.BITMAP):
        if filters.subject:
            clauses += 1
            if stats is not None:
                estimated = stats.column("subject").selectivity(filters.subject, stats.cardinality)
                rows, exact = estimated.rows, estimated.exact
        if filters.tags:
            clauses += len(filters.tags)
            rows = min(rows, max(1, rows // 2))

    if not clauses:
        return None
    return builder.add(
        Op.BITMAP_FILTER,
        scope=scope,
        index=IndexKind.BITMAP.value,
        clauses=clauses,
        rows=rows,
        exact=exact,
    )


def _generator_node(
    *,
    builder: PlanBuilder,
    generator: str,
    scope: str,
    mask: int | None,
    stats: ModuleStats | None,
    capabilities: Capabilities,
    config: PlannerConfig,
    query: Query,
    pool: int,
) -> int | None:
    """One generator node, or ``None`` when this scope cannot supply it."""
    cardinality = stats.cardinality if stats else 0

    if generator == "SeqScan":
        return builder.add(
            Op.SEQ_SCAN,
            scope=scope,
            terms=len(query.text.split()),
            selectivity=1.0,
        )

    if generator == "TermScan":
        if not capabilities.has(scope, IndexKind.INVERTED) or not query.text.strip():
            return None
        terms = stats.terms if stats else None
        postings = terms.postings if terms else cardinality
        return builder.add(
            Op.TERM_SCAN,
            scope=scope,
            index=IndexKind.INVERTED.value,
            inputs=[mask] if mask is not None else [],
            terms=max(1, len(query.text.split())),
            postings=postings,
            rows=cardinality,
            k=pool,
            recall=0.95,
        )

    if generator == "VectorSearch":
        if not capabilities.has(scope, IndexKind.VECTOR) or not query.text.strip():
            return None
        view = next(iter(stats.vectors.values()), None) if stats else None
        vectors = view.vectors if view else cardinality
        dimensions = view.dimensions if view else 384

        # The decision worth explaining. Feeding a very selective mask into HNSW *loses* recall -- the graph walk
        # visits a fixed number of nodes and only a fraction survive the filter -- so below a threshold an exact scan
        # of the masked vectors is both cheaper and perfectly accurate. The threshold is a knob; the reason it exists
        # is in the cost model, not here.
        if vectors and vectors <= max(config.brute_threshold, BRUTE_THRESHOLD // 1):
            return builder.add(
                Op.BRUTE_VECTOR,
                scope=scope,
                index=IndexKind.VECTOR.value,
                inputs=[mask] if mask is not None else [],
                vectors=vectors,
                dimensions=dimensions,
                k=pool,
            )
        return builder.add(
            Op.VECTOR_SEARCH,
            scope=scope,
            index=IndexKind.VECTOR.value,
            inputs=[mask] if mask is not None else [],
            vectors=vectors,
            dimensions=dimensions,
            effort=64,
            k=pool,
            recall=view.recall_at(64) if view else 0.8,
        )

    if generator == "GraphExpand":
        if not capabilities.has(scope, IndexKind.GRAPH):
            return None
        depth = min(query.hints.expand_depth or 1, config.graph_expand_max)
        graph = stats.graph if stats else None
        reach = graph.reach_by_depth[depth - 1] if graph and len(graph.reach_by_depth) >= depth else 1.5
        return builder.add(
            Op.GRAPH_EXPAND,
            scope=scope,
            index=IndexKind.GRAPH.value,
            depth=depth,
            reach=reach,
            degree=graph.out_degree_mean if graph else 1.0,
            recall=0.7,
        )
    return None


def _residual_for(query: Query, scope: str, capabilities: Capabilities) -> dict[str, Any] | None:
    """
    A residual filter node's parameters, when some predicate could not be pushed down.

    ``decode=True`` is the expensive case and the one the cost model exists to price: it reads a block per row, which
    is why the pushdown rules are worth having.
    """
    filters = query.filters
    needs_decode = bool(filters.evidence)
    if filters.subject and not capabilities.has(scope, IndexKind.BITMAP):
        needs_decode = True
    if (filters.since or filters.until) and not capabilities.has(scope, IndexKind.BTREE):
        needs_decode = True
    if not needs_decode:
        return None
    return {"decode": True, "selectivity": 0.5, "predicate": "residual filters"}


def _with_tail(
    builder: PlanBuilder,
    produced: int,
    *,
    query: Query,
    statistics: Mapping[str, ModuleStats],
    scopes: Sequence[str],
    limit: int,
) -> Plan:
    """
    Append the fixed tail. The optimiser never sees this, so it cannot trade any of it away.

    ``Accessibility`` before ``TopK`` is the ordering that matters: the other way round spends the limit on blocks
    that are about to be hidden.
    """
    provenance = statistics.get("provenance")
    accessible = builder.add(
        Op.ACCESSIBILITY,
        inputs=[produced],
        include_superseded=query.filters.include_superseded,
        hidden=0.0 if query.filters.include_superseded else 0.05,
        # Reported rather than costed: the view is cached per provenance root, and its cost appears as `prelude_us`.
        # Carrying the size here is what makes a slow first query understandable instead of mysterious.
        ledger_blocks=provenance.cardinality if provenance else 0,
    )
    topk = builder.add(Op.TOP_K, inputs=[accessible], k=limit, reserve=limit)
    resolved = builder.add(Op.RESOLVE, inputs=[topk])
    verified = builder.add(Op.VERIFY, inputs=[resolved], scopes=tuple(scopes))
    root = builder.add(Op.BUNDLE, inputs=[verified])
    return builder.finish(root)


def _empty_plan() -> Plan:
    """
    A plan that returns nothing.

    Not an error. "The brain holds nothing matching" is a legitimate answer, and so is "every scope was pruned
    because a predicate proved unsatisfiable".
    """
    builder = PlanBuilder()
    empty = builder.add(Op.EMPTY)
    root = builder.add(Op.BUNDLE, inputs=[empty])
    return builder.finish(root)
