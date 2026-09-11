"""Typed retrieval results: the bundle ``search`` returns, and the explanation ``explain`` returns.

The third vertical slice, after reconciliation and lifecycle, and the one ADR-0022 deferred: a match's ``content``
is polymorphic, so typing it meant modelling a block's payload per memory type first. That vocabulary lives in
:mod:`vitruvio.runtime.block_result`; this module adds the bundle around it.

Two regimes meet here. The bundle, its matches, their sources and the planner's explanation are full
``model_dump(mode="json")`` dumps, in which every field is present and an optional one is ``null`` -- so those types
are total, with ``X | None`` where the model says so. ``content`` is a payload dump and follows the rules stated in
``block_result``. Two keys are the operation's own rather than the SDK's: ``plan`` exists only when a cost-based
planner ran, ``diagnostics`` only when a caller asked, so both are ``NotRequired``.

The explanation mirrors :mod:`vitruvio.planner.explain` rather than importing it: importing the planner pulls
``vitruvio.stats`` onto the eager path of ``import vitruvio.runtime``, which ``test_import_cost`` forbids. A test
compares every field here with the model it mirrors, so the two cannot drift silently.

Same idiom as :mod:`vitruvio.runtime.reconcile_result`: ``TypedDict`` over a payload that stays a plain dictionary,
``cast`` at the one place a pydantic dump becomes it.
"""

from typing import Any, Literal, NotRequired, TypedDict

from vitruvio.runtime.block_result import (
    CanonicalContent,
    EpisodicContent,
    ProceduralContent,
    ProvenanceContent,
    SemanticContent,
)


class SourceRefResult(TypedDict):
    """Canonical evidence a match cites, and where in it."""

    block_id: str
    locator: str | None


class AuthorshipResult(TypedDict):
    """Who assembled the brain the evidence came from.

    Reported apart from ``verified`` on purpose: "intact, and signed by an authorized key" and "intact, provenance
    unknown" are different claims, and a bundle that folded them together could not express either.
    """

    state: str
    snapshot: str
    key: str | None
    subject: str | None
    trust_root: str | None
    pinned: bool


class _Match(TypedDict):
    block_id: str
    score: str
    sources: list[SourceRefResult]
    verified: bool
    resolvable: bool
    superseded_by: str | None


class CanonicalMatchResult(_Match):
    memory_type: Literal["canonical"]
    content: CanonicalContent


class EpisodicMatchResult(_Match):
    memory_type: Literal["episodic"]
    content: EpisodicContent


class SemanticMatchResult(_Match):
    memory_type: Literal["semantic"]
    content: SemanticContent


class ProceduralMatchResult(_Match):
    memory_type: Literal["procedural"]
    content: ProceduralContent


class ProvenanceMatchResult(_Match):
    memory_type: Literal["provenance"]
    content: ProvenanceContent


MatchResult = (
    CanonicalMatchResult | EpisodicMatchResult | SemanticMatchResult | ProceduralMatchResult | ProvenanceMatchResult
)
"""One retrieved block. Comparing ``memory_type`` narrows ``content`` to that memory type's vocabulary; ``content``
is empty when ``resolvable`` is false."""


class OperatorResult(TypedDict):
    """One node of a physical plan, with its estimates and, after ``analyze``, its actuals."""

    node_id: int
    op: str
    scope: str | None
    params: dict[str, Any]
    index: str | None
    inputs: list[int]
    est_rows: int
    est_cost_us: float
    est_recall: float | None
    act_rows: int | None
    act_cost_us: float | None
    notes: list[str]


class DegradationResult(TypedDict):
    """Why an answer may be worse than it could be."""

    kind: str
    detail: str
    recall_before: float | None
    recall_after: float | None


class SearchPlanResult(TypedDict):
    """What the planner did for this search.

    ``intent`` is the intent's kind alone, where ``explain`` reports the whole intent; the two shapes predate this
    contract and stay as recorded.
    """

    signature: str
    intent: str
    indices_consulted: dict[str, list[str]]
    indices_available: dict[str, list[str]]
    operators: list[OperatorResult]
    est_cost_us: float
    est_recall: float
    degradations: list[DegradationResult]


class GraphNodeResult(TypedDict):
    id: str
    label: str
    memory_type: str | None
    role: Literal["result", "related"]
    score: str | None


class GraphEdgeResult(TypedDict):
    source: str
    target: str
    kind: str
    predicate: str | None
    weight: float
    scope: str


class GraphDiagnosticsResult(TypedDict):
    selected: bool
    scopes: list[str]
    nodes: list[GraphNodeResult]
    edges: list[GraphEdgeResult]


class VectorPointResult(TypedDict):
    role: Literal["query", "result"]
    block_id: str | None
    chunk: int | None
    x: float
    y: float
    label: str


class VectorScopeResult(TypedDict):
    """One module's projection. A projection that failed keeps the scope visible, with ``error`` and no ``method``."""

    scope: str
    dimensions: int
    points: list[VectorPointResult]
    method: NotRequired[str]
    error: NotRequired[str]


class VectorDiagnosticsResult(TypedDict):
    selected: bool
    scopes: list[VectorScopeResult]


class BTreeEntryResult(TypedDict):
    position: int
    value: str
    block_id: str | None
    selected: bool


class BTreeScopeResult(TypedDict):
    scope: str
    key: str
    engine: str
    total: int
    low: str | None
    high: str | None
    start: int
    end: int
    entries: list[BTreeEntryResult]


class BTreeDiagnosticsResult(TypedDict):
    selected: bool
    scopes: list[BTreeScopeResult]


class DiagnosticsResult(TypedDict):
    """Query-scoped views of the indices the plan chose, for a human interface. Opt-in: not part of the bundle."""

    graph: GraphDiagnosticsResult
    vector: VectorDiagnosticsResult
    btree: BTreeDiagnosticsResult


class SearchResult(TypedDict):
    """An Evidence Bundle: blocks, provenance and scores. There is no answer field, by design."""

    matches: list[MatchResult]
    verified_against: dict[str, str]
    truncated: bool
    authorship: AuthorshipResult | None
    all_verified: bool
    plan: NotRequired[SearchPlanResult]
    diagnostics: NotRequired[DiagnosticsResult]


class IntentResult(TypedDict):
    kind: str
    features: list[str]
    weights: dict[str, float]
    authority: dict[str, float]
    recall_floor: float
    out_of_vocabulary: float


class PredicateResult(TypedDict):
    field: str
    operator: str
    disposition: str
    selectivity: float | None
    exact: bool
    note: str | None


class PlanExplainResult(TypedDict):
    signature: str
    operators: list[OperatorResult]
    root: int
    total_est_cost_us: float
    est_recall: float
    objective: float
    admissible: bool
    rejected_reason: str | None
    pareto: bool


class StatsResult(TypedDict):
    memory_type: str
    freshness: str
    reason: str | None
    root: str | None
    cardinality: int
    built_at: str | None


class ExplanationResult(TypedDict):
    """The planner's explanation, whole. ``estimation_error`` is keyed by node id, which JSON turns into a string."""

    query_digest: str
    intent: IntentResult
    predicates: list[PredicateResult]
    chosen: PlanExplainResult
    considered: list[PlanExplainResult]
    statistics: list[StatsResult]
    indices_available: dict[str, list[str]]
    indices_consulted: dict[str, list[str]]
    calibration: str
    cache: Literal["hit", "miss", "bypass"]
    degradations: list[DegradationResult]
    prelude_us: float
    analyzed: bool
    wall_us: float | None
    estimation_error: dict[str, float]


__all__ = [
    "AuthorshipResult",
    "BTreeDiagnosticsResult",
    "BTreeEntryResult",
    "BTreeScopeResult",
    "CanonicalMatchResult",
    "DegradationResult",
    "DiagnosticsResult",
    "EpisodicMatchResult",
    "ExplanationResult",
    "GraphDiagnosticsResult",
    "GraphEdgeResult",
    "GraphNodeResult",
    "IntentResult",
    "MatchResult",
    "OperatorResult",
    "PlanExplainResult",
    "PredicateResult",
    "ProceduralMatchResult",
    "ProvenanceMatchResult",
    "SearchPlanResult",
    "SearchResult",
    "SemanticMatchResult",
    "SourceRefResult",
    "StatsResult",
    "VectorDiagnosticsResult",
    "VectorPointResult",
    "VectorScopeResult",
]
