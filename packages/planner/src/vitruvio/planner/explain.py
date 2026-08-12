"""EXPLAIN as a first-class API, not a CLI feature.

The CLI and the MCP server that follows it both need this, and a pydantic model means both serialize the same object
with no extra code. ``render_tree`` lives here too, so the human form does not require a rendering dependency in the
planner.

The field that earns its place most is ``indices_available`` sitting beside ``indices_consulted``. The most common
complaint about any planner is "why didn't it use the vector index", and the answer is always one of four things: it
isn't installed, it's stale, its model tag doesn't match the configured embedder, or it cost more than the
alternative. All four are visible in one payload, which turns an argument into a lookup.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from vitruvio.planner.ir import Estimates, Metrics, Plan


class OperatorExplain(BaseModel):
    """
    One operator, as planned and as executed.

    Attributes:
        node_id (int): Position in the arena. Shared inputs appear once and are referenced twice, which is the arena
            paying off.
        op (str): Which operator.
        scope (str | None): Which memory type.
        params (dict[str, Any]): Its parameters.
        index (str | None): Which index kind it consulted.
        inputs (list[int]): Which nodes feed it.
        est_rows (int): Expected output cardinality.
        est_cost_us (float): Expected microseconds.
        est_recall (float | None): Expected recall, for a generator.
        act_rows (int | None): Actual cardinality, under ANALYZE.
        act_cost_us (float | None): Actual microseconds, under ANALYZE.
        notes (list[str]): Why it was estimated as it was.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: int
    op: str
    scope: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    index: str | None = None
    inputs: list[int] = Field(default_factory=list)
    est_rows: int = 0
    est_cost_us: float = 0.0
    est_recall: float | None = None
    act_rows: int | None = None
    act_cost_us: float | None = None
    notes: list[str] = Field(default_factory=list)


class PlanExplain(BaseModel):
    """
    One candidate plan, costed.

    Attributes:
        signature (str): Stable structural hash. Golden snapshots key on this rather than on costs, which move
            whenever the calibration or the statistics do.
        operators (list[OperatorExplain]): Every node.
        root (int): Which node produces the bundle.
        total_est_cost_us (float): Sum of per-node costs.
        est_recall (float): Expected recall for the plan.
        objective (float): ``J``, the number actually minimised.
        admissible (bool): Whether it was allowed to compete.
        rejected_reason (str | None): Why not, when it was not.
        pareto (bool): Whether it sits on the cost/recall Pareto frontier. Reporting the frontier is more useful
            than reporting only the winner: it shows what the chosen plan was traded against.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    signature: str
    operators: list[OperatorExplain] = Field(default_factory=list)
    root: int = 0
    total_est_cost_us: float = 0.0
    est_recall: float = 0.0
    objective: float = 0.0
    admissible: bool = True
    rejected_reason: str | None = None
    pareto: bool = False


class PredicateExplain(BaseModel):
    """
    One filter, and what became of it.

    Attributes:
        field (str): Which field.
        operator (str): equals, range, intersects.
        disposition (str): ``pushdown`` when an index could evaluate it without decoding a block, ``residual`` when
            it had to be applied by decoding, ``scoping`` when it selected which modules to search at all.
        selectivity (float | None): Estimated fraction admitted.
        exact (bool): Whether that estimate was measured rather than interpolated.
        note (str | None): How it was estimated.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    operator: str
    disposition: str
    selectivity: float | None = None
    exact: bool = False
    note: str | None = None


class IntentExplain(BaseModel):
    """
    How the query was classified.

    Attributes:
        kind (str): The shape.
        features (list[str]): Which rules fired, so the classification can be checked rather than trusted.
        weights (dict[str, float]): Per-generator fusion weights.
        authority (dict[str, float]): Per-generator coverage priors. Every value below 1.0 *is* the claim that no
            single index can be trusted alone.
        recall_floor (float): The feasibility constraint an admissible plan had to clear.
        out_of_vocabulary (float): Fraction of terms no index has seen.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    features: list[str] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    authority: dict[str, float] = Field(default_factory=dict)
    recall_floor: float = 0.0
    out_of_vocabulary: float = 0.0


class StatsExplain(BaseModel):
    """
    Which statistics the plan was costed against.

    Attributes:
        memory_type (str): Which module.
        freshness (str): fresh, stale or absent.
        reason (str | None): Why it is not fresh.
        root (str | None): Which composition it describes.
        cardinality (int): Blocks.
        built_at (str | None): When.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_type: str
    freshness: str
    reason: str | None = None
    root: str | None = None
    cardinality: int = 0
    built_at: str | None = None


class Degradation(BaseModel):
    """
    Something the planner had to work around, reported rather than hidden.

    A degraded answer that looks identical to a clean one is the failure mode this whole design is built to avoid.

    Attributes:
        kind (str): What happened -- ``index_absent``, ``index_empty``, ``stats_stale``, ``model_mismatch``,
            ``embedder_unavailable``, ``verification_failed``.
        detail (str): The specifics.
        recall_before (float | None): Expected recall before the degradation.
        recall_after (float | None): Expected recall after it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    detail: str
    recall_before: float | None = None
    recall_after: float | None = None


class Explanation(BaseModel):
    """
    Everything about how one query was answered.

    Attributes:
        query_digest (str): A hash of the query, so two explanations can be compared without quoting the text.
        intent (IntentExplain): The classification.
        predicates (list[PredicateExplain]): Each filter and its disposition.
        chosen (PlanExplain): The plan that ran.
        considered (list[PlanExplain]): The alternatives, including rejected ones with their reasons.
        statistics (list[StatsExplain]): What it was costed against.
        indices_available (dict[str, list[str]]): What is installed and usable.
        indices_consulted (dict[str, list[str]]): What the chosen plan actually touched.
        calibration (str): Digest of the cost constants, and whether they were measured or defaulted.
        cache (str): hit, miss or bypass. Bypassed when statistics are stale.
        degradations (list[Degradation]): What had to be worked around.
        prelude_us (float): Cost of building the provenance view. Counted honestly, because it is charged to the
            query but caused by the brain's size.
        analyzed (bool): Whether actuals were recorded.
        wall_us (float | None): Total measured time, under ANALYZE.
        estimation_error (dict[int, float]): Per-node ``log10(actual / estimated)``, under ANALYZE. This is what
            makes the cost model checkable rather than merely plausible.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    query_digest: str
    intent: IntentExplain
    predicates: list[PredicateExplain] = Field(default_factory=list)
    chosen: PlanExplain
    considered: list[PlanExplain] = Field(default_factory=list)
    statistics: list[StatsExplain] = Field(default_factory=list)
    indices_available: dict[str, list[str]] = Field(default_factory=dict)
    indices_consulted: dict[str, list[str]] = Field(default_factory=dict)
    calibration: str = "default"
    cache: Literal["hit", "miss", "bypass"] = "miss"
    degradations: list[Degradation] = Field(default_factory=list)
    prelude_us: float = 0.0
    analyzed: bool = False
    wall_us: float | None = None
    estimation_error: dict[int, float] = Field(default_factory=dict)


def describe_plan(
    plan: Plan,
    estimates: Estimates,
    *,
    recall: float,
    objective: float,
    metrics: Metrics | None = None,
    admissible: bool = True,
    reason: str | None = None,
    pareto: bool = False,
) -> PlanExplain:
    """
    Turn a plan and its estimates into an explainable record.

    Args:
        plan (Plan): The plan.
        estimates (Estimates): What it was expected to cost.
        recall (float): Expected recall.
        objective (float): ``J``.
        metrics (Metrics | None): Actuals, when it ran.
        admissible (bool): Whether it was allowed to compete.
        reason (str | None): Why not.
        pareto (bool): Whether it is on the frontier.

    Returns:
        PlanExplain: The record.
    """
    operators = [
        OperatorExplain(
            node_id=node_id,
            op=node.op.value,
            scope=node.scope,
            params=dict(node.params),
            index=node.index,
            inputs=list(node.inputs),
            est_rows=estimates.rows[node_id],
            est_cost_us=round(estimates.cost[node_id], 3),
            est_recall=estimates.recall[node_id],
            act_rows=metrics.rows[node_id] if metrics else None,
            act_cost_us=round(metrics.micros[node_id], 3) if metrics else None,
            notes=list(estimates.notes[node_id]) if node_id < len(estimates.notes) else [],
        )
        for node_id, node in enumerate(plan.nodes)
    ]
    return PlanExplain(
        signature=plan.signature,
        operators=operators,
        root=plan.root,
        total_est_cost_us=round(estimates.total_cost, 3),
        est_recall=round(recall, 4),
        objective=round(objective, 3),
        admissible=admissible,
        rejected_reason=reason,
        pareto=pareto,
    )


def render_tree(explanation: Explanation) -> list[str]:
    """
    The human form: the chosen plan as a tree, then what it was traded against.

    Args:
        explanation (Explanation): What to render.

    Returns:
        list[str]: Lines to print.
    """
    chosen = explanation.chosen
    by_id = {operator.node_id: operator for operator in chosen.operators}
    printed: set[int] = set()
    lines: list[str] = []

    def walk(node_id: int, depth: int) -> None:
        operator = by_id.get(node_id)
        if operator is None:
            return
        indent = "   " * depth
        if node_id in printed:
            # A node reached twice is a shared subexpression: evaluated once, referenced here. Saying so is the
            # visible payoff of the arena over a tree.
            lines.append(f"{indent}+- #{node_id} (shared)")
            return
        printed.add(node_id)

        detail = ", ".join(
            f"{name}={value}" for name, value in sorted(operator.params.items()) if value not in (None, ())
        )
        actual = (
            f"   act {operator.act_rows} rows {operator.act_cost_us:.0f}us" if operator.act_rows is not None else ""
        )
        recall = f" recall {operator.est_recall:.2f}" if operator.est_recall is not None else ""
        lines.append(
            f"{indent}+- #{node_id} {operator.op}"
            f"{f'[{operator.scope}]' if operator.scope else ''}"
            f"{f'({detail})' if detail else ''}"
            f"   est {operator.est_rows} rows {operator.est_cost_us:.0f}us{recall}{actual}"
        )
        for feeding in operator.inputs:
            walk(feeding, depth + 1)
        for note in operator.notes:
            lines.append(f"{indent}     note: {note}")

    lines.append(f"intent      {explanation.intent.kind}  ({'; '.join(explanation.intent.features)})")
    if explanation.intent.out_of_vocabulary:
        lines.append(f"unseen      {explanation.intent.out_of_vocabulary:.0%} of query terms are not in any index")
    lines.append("")
    walk(chosen.root, 0)
    lines.append("")
    lines.append(
        f"chosen      J={chosen.objective:.0f}  cost={chosen.total_est_cost_us:.0f}us  recall={chosen.est_recall:.2f}"
    )

    runners = [plan for plan in explanation.considered if plan.signature != chosen.signature]
    for plan in runners[:3]:
        reason = f"  rejected: {plan.rejected_reason}" if not plan.admissible else ""
        lines.append(
            f"considered  J={plan.objective:.0f}  cost={plan.total_est_cost_us:.0f}us  "
            f"recall={plan.est_recall:.2f}{reason}"
        )
    if len(runners) > 3:
        lines.append(f"            ... {len(runners) - 3} more")

    for predicate in explanation.predicates:
        measured = " (measured)" if predicate.exact else ""
        selectivity = f" sel={predicate.selectivity:.3f}{measured}" if predicate.selectivity is not None else ""
        lines.append(f"predicate   {predicate.field} {predicate.operator} -> {predicate.disposition}{selectivity}")

    lines.append("")
    for entry in explanation.statistics:
        note = f" ({entry.reason})" if entry.reason else ""
        lines.append(f"statistics  {entry.memory_type}: {entry.freshness}{note}, {entry.cardinality} blocks")

    available = {scope: set(kinds) for scope, kinds in explanation.indices_available.items()}
    for scope, kinds in sorted(available.items()):
        consulted = set(explanation.indices_consulted.get(scope, []))
        unused = sorted(kinds - consulted)
        lines.append(
            f"indices     {scope}: consulted {', '.join(sorted(consulted)) or 'none'}"
            f"{f'; available but not chosen: {chr(44).join(unused)}' if unused else ''}"
        )

    for degradation in explanation.degradations:
        lines.append(f"DEGRADED    {degradation.kind}: {degradation.detail}")

    lines.append(f"cache       {explanation.cache}   calibration {explanation.calibration}")
    if explanation.prelude_us:
        lines.append(f"prelude     {explanation.prelude_us:.0f}us building the provenance view")
    if explanation.analyzed and explanation.wall_us is not None:
        lines.append(f"wall        {explanation.wall_us:.0f}us measured")
        misestimated = {node: error for node, error in explanation.estimation_error.items() if abs(error) > 0.5}
        if misestimated:
            worst = sorted(misestimated.items(), key=lambda pair: -abs(pair[1]))[:3]
            rendered = ", ".join(f"#{node} {error:+.1f} decades" for node, error in worst)
            lines.append(f"misestimate {rendered}")
    return lines
