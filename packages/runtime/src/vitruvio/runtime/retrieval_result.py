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
and ``cast`` wherever mypy cannot follow the construction -- a pydantic dump, a dictionary arriving from another
layer, or a union spread into a new one. Six of them carry this slice: ``wire.evidence``, the explanation and the
two per-operator dumps in :mod:`vitruvio.runtime.ops.retrieval`, and the two index diagnostics, which are plain
dictionaries built in :mod:`vitruvio.indices` rather than dumps. The cost is that each one is a claim a reader has
to check by eye, which is why they are named here rather than counted.

**No ``from __future__ import annotations`` here, unlike every other module in this package.** Under PEP 563 an
annotation is a string when the class body runs, so ``TypedDict`` cannot see ``NotRequired`` and files every
optional key under ``__required_keys__`` instead -- silently, on 3.11. This module and ``block_result`` are the two
that use ``NotRequired``, so they are the two that must not have it.
"""

from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict

from vitruvio.runtime.block_result import (
    CanonicalContent,
    EpisodicContent,
    ProceduralContent,
    ProvenanceContent,
    SemanticContent,
)
from vitruvio.runtime.browse import UNNAMED, identify


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
    """What every match carries whatever module it came from, so the five variants below state only their difference.

    Private because nothing should annotate against it: a reader narrows the ``MatchResult`` union on ``memory_type``
    and gets ``content`` with it. Naming this type in a signature would hand back a match whose content is unknown,
    which is the state this slice exists to remove.
    """

    block_id: str
    score: str
    sources: list[SourceRefResult]
    verified: bool
    resolvable: bool
    superseded_by: str | None


class CanonicalMatchResult(_Match):
    """A block that *is* its bytes, so ``content`` names them: ``media_type``, ``blob``, a ``normalized_view``."""

    memory_type: Literal["canonical"]
    content: CanonicalContent


class EpisodicMatchResult(_Match):
    """A block that records something that happened, so ``content`` is dated: ``summary``, ``occurred_at``,
    ``participants``, ``outcome``."""

    memory_type: Literal["episodic"]
    content: EpisodicContent


class SemanticMatchResult(_Match):
    """A block that asserts something, so ``content`` carries the assertion: a ``label`` or ``statement``, a
    ``kind``, and the ``relations`` that place it among others."""

    memory_type: Literal["semantic"]
    content: SemanticContent


class ProceduralMatchResult(_Match):
    """A block that says how to do something, so ``content`` is ordered: a ``goal``, ``steps``, ``preconditions``
    and ``success_criteria``."""

    memory_type: Literal["procedural"]
    content: ProceduralContent


class ProvenanceMatchResult(_Match):
    """A ledger entry retrieved like any other block. ``content`` holds a single ``record``, itself a union over
    ``record_type``, so narrowing happens twice: once on the memory type here, once on the record there."""

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
    """A block as a point in the relation diagram.

    ``memory_type`` and ``score`` are nullable because a node can be a neighbour the query never returned: it is
    drawn so the reader sees what the result is attached to, and inventing a score for it would say the planner
    ranked something it never saw.
    """

    id: str
    label: str
    memory_type: str | None
    role: Literal["result", "related"]
    score: str | None


class GraphEdgeResult(TypedDict):
    """One relation between two blocks. ``predicate`` is nullable because not every edge kind names one -- an
    evidence link is an edge without a predicate, and an empty string would read as a predicate that is blank."""

    source: str
    target: str
    kind: str
    predicate: str | None
    weight: float
    scope: str


class GraphDiagnosticsResult(TypedDict):
    """The relation diagram, and whether the planner actually consulted it.

    ``selected`` is separate from an empty ``nodes`` list on purpose: an index the planner skipped and an index it
    used and found nothing in are different answers to "why did I get this result", and one empty list would say
    neither.
    """

    selected: bool
    scopes: list[str]
    nodes: list[GraphNodeResult]
    edges: list[GraphEdgeResult]


class VectorPointResult(TypedDict):
    """One point in the two-dimensional projection of a vector scope.

    ``block_id`` and ``chunk`` are nullable because the query itself is plotted alongside the results -- that is
    the whole point of the picture, and it belongs to no block.
    """

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
    """Every vector scope's projection, and whether the planner consulted any of them. See
    :class:`GraphDiagnosticsResult` for why ``selected`` is not inferred from an empty list."""

    selected: bool
    scopes: list[VectorScopeResult]


class BTreeEntryResult(TypedDict):
    """One key in the ordered index, inside the window a range query scanned.

    Neighbours the query did not match are carried too, with ``selected`` false: a window showing only the hits
    cannot answer whether the range was too narrow, which is the question a reader opens the diagnostic with.
    """

    position: int
    value: str
    block_id: str | None
    selected: bool


class BTreeScopeResult(TypedDict):
    """One module's ordered index, and the slice of it a range query touched.

    ``low`` and ``high`` are nullable because a half-open range is a legitimate query; ``start`` and ``end`` locate
    the window inside ``total``, so a reader can tell a window at the edge of the index from one in the middle.
    """

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
    """Every ordered scope's window, and whether the planner consulted any of them. See
    :class:`GraphDiagnosticsResult` for why ``selected`` is not inferred from an empty list."""

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
    """What the planner decided the query was asking for, mirrored from ``planner.explain.IntentExplain``.

    Total, like every mirror here: a full ``model_dump`` keeps each field, so a missing key would mean the model
    changed rather than that the value was absent.
    """

    kind: str
    features: list[str]
    weights: dict[str, float]
    authority: dict[str, float]
    recall_floor: float
    out_of_vocabulary: float


class PredicateResult(TypedDict):
    """One filter the planner considered, and what it did with it.

    ``selectivity`` is nullable because the planner reports the estimate only when statistics existed to make one,
    and ``note`` carries the reason when ``disposition`` alone would not explain the choice.
    """

    field: str
    operator: str
    disposition: str
    selectivity: float | None
    exact: bool
    note: str | None


class PlanExplainResult(TypedDict):
    """One candidate plan as ``explain`` reports it -- the chosen one and each rejected alternative alike.

    ``rejected_reason`` is nullable because the chosen plan has none; that asymmetry is the point of the field, so
    it stays a distinct key rather than an empty string.
    """

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
    """What the planner knew about one module when it chose, and how stale that was.

    ``freshness`` and ``reason`` are reported together because a plan made on stale statistics is not wrong, it is
    explainable -- and without the reason a reader cannot tell a never-built index from an out-of-date one.
    """

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


@dataclass(frozen=True, slots=True)
class MatchView:
    """Shared interpretation of one match for the CLI and the TUI: what the block is called, and what it says.

    Delegates to :func:`vitruvio.runtime.browse.identify`, so a match and a browse row of the same block agree --
    except that a bundle carries no registration origin, so a canonical match is named by its media type where a
    row is named by its file.
    """

    match: MatchResult

    @property
    def title(self) -> str:
        """What the block is called, or ``(unnamed)``."""
        return identify(self.match["memory_type"], self.match["content"])[0] or UNNAMED

    @property
    def detail(self) -> str:
        """What the block says, as distinct from what it is called."""
        return identify(self.match["memory_type"], self.match["content"])[1]


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
    "MatchView",
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
