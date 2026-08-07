"""``CostBasedPlanner``: build candidate plans, cost them, run the winner, explain the choice.

The shape of the thing, in order:

1. **Capabilities.** Probe what each installed index can actually answer. An index that is registered but *empty* is
   excluded rather than consulted -- an empty index does not announce itself, so consulting one yields no candidates
   and a confident nothing.
2. **Predicates.** Extract filters and decide each one's disposition. A predicate is pushdown-able iff some index can
   evaluate it *without decoding a block*; everything else is a residual, and a residual costs a block read per row,
   which is roughly 150-900x a bitmap word scan. That ratio is why the dispositions are costed rather than assumed.
3. **Intent.** Classify the query shape deterministically.
4. **Enumerate.** Generate the handful of physical templates the capabilities permit, at a couple of ``k`` values.
   Not Cascades: the combinatorial explosion a memo structure exists for comes from join reordering, and there are no
   joins here -- one "table" per module, and fusion is commutative and fixed-cost. Exhaustive enumeration over a
   small space also buys a property a memo does not: **adding an index can never worsen the chosen plan**, because
   the new space is a strict superset and both are searched completely. That is testable, and it is worth a lot.
5. **Cost, filter, choose.** Validity rule, then recall floor, then ``J``. Layered so that pruning is free to be
   aggressive without ever being able to reach a plan the protocol forbids.
6. **Execute, then finalise.** The tail is fixed and the optimiser never sees it:
   ``Accessibility -> TopK -> Resolve -> Verify -> Bundle``.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from boltzmann.blocks.memory_type import MemoryType
from boltzmann.indices.base import IndexKind
from boltzmann.module.ledger import Ledger
from boltzmann.query.evidence import EvidenceBundle

from vitruvio.planner.cost import (
    DEFAULT_CALIBRATION,
    Calibration,
    Objective,
    estimate,
    plan_recall,
)
from vitruvio.planner.explain import (
    Degradation,
    Explanation,
    IntentExplain,
    PredicateExplain,
    StatsExplain,
    describe_plan,
)
from vitruvio.planner.intent import classify
from vitruvio.planner.ir import Op

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from boltzmann.module.module import Module
    from boltzmann.query.request import Query

    from vitruvio.kernel import PlannerConfig
    from vitruvio.planner.ir import Metrics, Plan
    from vitruvio.stats import ModuleStats

RESERVE_FACTOR = 1
"""Extra rows kept past the limit, as a multiple of it.

A ``Resolve`` or ``Verify`` drop can then be backfilled from the spillover rather than shortening the bundle. If the
reserve empties, fewer are returned and ``truncated`` says so.
"""


@dataclass(frozen=True, slots=True)
class Capabilities:
    """
    What is installed and usable, per module.

    Attributes:
        available (dict[str, list[str]]): Every registered index kind, per scope.
        usable (dict[str, frozenset[str]]): The subset that may be planned on.
        degradations (tuple[Degradation, ...]): Why the difference.
    """

    available: dict[str, list[str]]
    usable: dict[str, frozenset[str]]
    degradations: tuple[Degradation, ...] = ()

    def has(self, scope: str, kind: IndexKind) -> bool:
        """Whether one index may be planned on for one module."""
        return kind.value in self.usable.get(scope, frozenset())


class CostBasedPlanner:
    """
    A ``QueryPlanner`` that chooses indices by estimated cost and expected recall.

    Attributes:
        config (PlannerConfig): The knobs -- lambda, the miss cost, the brute-force threshold, RRF's K.
        calibration (Calibration): Per-unit costs.
        statistics (Mapping[str, ModuleStats]): What to cost against. Supplied by the runtime, which knows where the
            catalogue lives.
    """

    def __init__(
        self,
        config: PlannerConfig,
        *,
        calibration: Calibration = DEFAULT_CALIBRATION,
        statistics: Mapping[str, ModuleStats] | None = None,
    ) -> None:
        """
        Build a planner.

        Args:
            config (PlannerConfig): Objective weights and limits.
            calibration (Calibration): Per-unit costs.
            statistics (Mapping[str, ModuleStats] | None): Per-scope statistics.
        """
        self.config = config
        self.calibration = calibration
        self.statistics: dict[str, ModuleStats] = dict(statistics or {})
        self._objective = Objective(weight=config.objective_lambda, miss_cost=float(config.miss_cost_us))
        self._ledger: tuple[str, Ledger] | None = None
        self._last: Explanation | None = None

    # --- The protocol's one method --------------------------------------------

    def plan(self, query: Query, modules: dict[MemoryType, Module]) -> EvidenceBundle:
        """
        Answer a query.

        Args:
            query (Query): What was asked. Names no index, by protocol.
            modules (dict[MemoryType, Module]): The installed modules.

        Returns:
            EvidenceBundle: Verified blocks with provenance and a score. Never prose.
        """
        bundle, self._last = self._run(query, modules, analyze=False)
        return bundle

    def explain(self, query: Query, modules: dict[MemoryType, Module]) -> Explanation:
        """
        Plan without executing, and report what would happen.

        Args:
            query (Query): The query.
            modules (dict[MemoryType, Module]): The modules.

        Returns:
            Explanation: The chosen plan, the alternatives, and what it was costed against.
        """
        _, explanation = self._run(query, modules, analyze=False, execute=False)
        return explanation

    def analyze(self, query: Query, modules: dict[MemoryType, Module]) -> tuple[EvidenceBundle, Explanation]:
        """
        Execute, recording actuals so the estimates can be checked against them.

        Args:
            query (Query): The query.
            modules (dict[MemoryType, Module]): The modules.

        Returns:
            tuple[EvidenceBundle, Explanation]: The result, and what it actually cost.
        """
        return self._run(query, modules, analyze=True)

    @property
    def last_explanation(self) -> Explanation | None:
        """The most recent plan's explanation, for a caller that ran ``plan`` and then wants the detail."""
        return self._last

    # --- Capability probing ---------------------------------------------------

    def capabilities(self, modules: Mapping[MemoryType, Module]) -> Capabilities:
        """
        What each module's indices can currently answer.

        Args:
            modules (Mapping[MemoryType, Module]): The installed modules.

        Returns:
            Capabilities: Available, usable, and why they differ.
        """
        available: dict[str, list[str]] = {}
        usable: dict[str, set[str]] = {}
        degradations: list[Degradation] = []

        for memory_type, module in modules.items():
            scope = memory_type.value
            available[scope] = sorted(module.indices)
            usable[scope] = set()
            for kind, index in sorted(module.indices.items()):
                population = getattr(index, "population", None)
                if population == 0:
                    # The failure this guards against: an empty index yields no candidates, which a planner would
                    # otherwise report as a confident nothing.
                    degradations.append(
                        Degradation(kind="index_empty", detail=f"{scope}.{kind} holds no blocks and was excluded")
                    )
                    continue
                bound = getattr(index, "bound_root", None)
                if bound is not None and bound != str(module.root):
                    degradations.append(
                        Degradation(kind="stats_stale", detail=f"{scope}.{kind} was built against another composition")
                    )
                    continue
                usable[scope].add(kind)

            stats = self.statistics.get(scope)
            if stats is not None and not stats.freshness.is_fresh and module.block_ids:
                degradations.append(
                    Degradation(
                        kind="stats_stale",
                        detail=f"{scope} statistics are {stats.freshness.state}: {stats.freshness.reason}",
                    )
                )

        return Capabilities(
            available=available,
            usable={scope: frozenset(kinds) for scope, kinds in usable.items()},
            degradations=tuple(degradations),
        )

    # --- Planning -------------------------------------------------------------

    def _run(
        self,
        query: Query,
        modules: dict[MemoryType, Module],
        *,
        analyze: bool,
        execute: bool = True,
    ) -> tuple[EvidenceBundle, Explanation]:
        """Plan, optionally execute, and explain. The one path all three public methods share."""
        started = time.perf_counter()
        scopes = self._scopes(query, modules)
        capabilities = self.capabilities(modules)
        predicates = self._predicates(query, scopes, capabilities)
        intent = self._classify(query, modules, scopes, capabilities)

        candidates = self._enumerate(query, scopes, capabilities, intent)
        available = self._generators(scopes, capabilities)

        # Cost and score every plan first, and check the *validity* rule -- which is absolute and never relaxed --
        # before deciding what the recall floor should be.
        scored: list[tuple[Plan, Any, float, bool, str | None]] = []
        for plan in candidates:
            estimates = estimate(plan, self.statistics, self.calibration)
            recall = plan_recall(plan, estimates, intent.authority)
            valid, reason = Objective.admissible(plan, len(available), exact_intent=intent.is_exact)
            scored.append((plan, estimates, recall, valid, reason))

        # The floor is then capped at the best recall any *valid* plan actually achieves.
        #
        # The per-intent floors are written for a brain with a lexical and a vector index, where neither alone clears
        # them -- which is what forces a plan to consult both. Applied absolutely they are frequently unsatisfiable:
        # measured on a synthetic corpus, a lexical-only brain rejected every plan that used its index and chose a
        # 5.3-second exhaustive scan over a 600-microsecond term probe at 100k blocks, and even a lexical+vector brain
        # missed an 0.85 floor by 0.013. An unsatisfiable floor does not enforce quality, it just disables the indices.
        #
        # Capping against what is achievable cannot be unsatisfiable by construction, keeps the floor's real job --
        # rejecting a plan that settles for less recall than a *sibling* plan offers -- and is reported rather than
        # applied quietly, because a lowered floor is a weaker guarantee than the intent asked for.
        # Computed over plans that actually use an index. The exhaustive scan is excluded deliberately: its recall is
        # 1.0 by construction, so counting it pins the floor at 1.0 and rejects every index-backed plan -- the same
        # disabling failure as an absolute floor, arrived at from the other direction.
        indexed_best = max(
            (
                recall
                for plan, _, recall, valid, _ in scored
                if valid and any(plan[node].op is not Op.SEQ_SCAN for node in plan.generators())
            ),
            default=0.0,
        )
        floor = min(intent.recall_floor, indexed_best) if indexed_best else 0.0
        floor_note: Degradation | None = None
        if floor < intent.recall_floor - 1e-9:
            floor_note = Degradation(
                kind="recall_floor_lowered",
                detail=(
                    f"the {intent.kind.value} floor of {intent.recall_floor:.2f} is unreachable with the installed "
                    f"indices ({', '.join(sorted(available)) or 'none only an exhaustive scan'}); "
                    f"lowered to the best achievable {floor:.2f}"
                ),
                recall_before=intent.recall_floor,
                recall_after=floor,
            )

        costed = []
        for plan, estimates, recall, valid, reason in scored:
            admissible, why = valid, reason
            if admissible and recall < floor - 1e-9:
                admissible, why = (
                    False,
                    f"expected recall {recall:.2f} is below the achievable {intent.kind.value} floor of {floor:.2f}",
                )
            costed.append(
                (plan, estimates, recall, self._objective.score(estimates.total_cost, recall), admissible, why)
            )

        feasible = [entry for entry in costed if entry[4]]
        pool = feasible or costed
        # Lexicographic on (J, signature): the second component is not decoration, it is what makes a tie resolve
        # identically on every machine, which golden plan snapshots depend on.
        pool.sort(key=lambda entry: (entry[3], entry[0].signature))
        chosen_plan, chosen_estimates, chosen_recall, chosen_objective, _, _ = pool[0]

        frontier = self._pareto({entry[0].signature: (entry[1].total_cost, entry[2]) for entry in costed})
        metrics: Metrics | None = None
        bundle = EvidenceBundle(matches=[], verified_against={}, truncated=False)
        prelude = 0.0
        degradations = list(capabilities.degradations)
        if floor_note is not None:
            degradations.append(floor_note)

        if execute:
            from vitruvio.planner.execute import Executor

            executor = Executor(
                planner=self,
                modules=modules,
                query=query,
                intent=intent,
                capabilities=capabilities,
                analyze=analyze,
            )
            bundle, metrics, prelude, extra = executor.run(chosen_plan)
            degradations.extend(extra)

        explanation = Explanation(
            query_digest=_digest(query),
            intent=IntentExplain(
                kind=intent.kind.value,
                features=list(intent.features),
                weights=dict(intent.weights),
                authority=dict(intent.authority),
                recall_floor=intent.recall_floor,
                out_of_vocabulary=round(intent.out_of_vocabulary, 4),
            ),
            predicates=predicates,
            chosen=describe_plan(
                chosen_plan,
                chosen_estimates,
                recall=chosen_recall,
                objective=chosen_objective,
                metrics=metrics,
                pareto=chosen_plan.signature in frontier,
            ),
            considered=[
                describe_plan(
                    plan,
                    estimates,
                    recall=recall,
                    objective=objective,
                    admissible=admissible,
                    reason=reason,
                    pareto=plan.signature in frontier,
                )
                for plan, estimates, recall, objective, admissible, reason in costed
            ],
            statistics=[
                StatsExplain(
                    memory_type=scope,
                    freshness=self.statistics[scope].freshness.state if scope in self.statistics else "absent",
                    reason=self.statistics[scope].freshness.reason if scope in self.statistics else None,
                    root=self.statistics[scope].version.root if scope in self.statistics else None,
                    cardinality=self.statistics[scope].cardinality if scope in self.statistics else 0,
                    built_at=self.statistics[scope].version.built_at or None if scope in self.statistics else None,
                )
                for scope in scopes
            ],
            indices_available=capabilities.available,
            indices_consulted=chosen_plan.indices_consulted(),
            calibration=self.calibration.digest(),
            cache="bypass" if self._stale(scopes) else "miss",
            degradations=degradations,
            prelude_us=round(prelude, 3),
            analyzed=analyze,
            wall_us=round((time.perf_counter() - started) * 1e6, 3) if analyze else None,
            estimation_error=metrics.error_against(chosen_estimates) if metrics else {},
        )
        return bundle, explanation

    def _scopes(self, query: Query, modules: Mapping[MemoryType, Module]) -> tuple[str, ...]:
        """
        Which modules to search.

        ``memory_types`` is *scoping*, never an operator and never a residual: it selects which subplans exist at
        all. That is what makes it the filter that stops "what happened in May" from competing with "define a Fourier
        series".
        """
        installed = [memory_type.value for memory_type in modules if modules[memory_type].block_ids]
        requested = query.filters.memory_types
        if not requested:
            return tuple(sorted(installed))
        wanted = {item.value if isinstance(item, MemoryType) else str(item) for item in requested}
        return tuple(sorted(scope for scope in installed if scope in wanted))

    def _classify(
        self,
        query: Query,
        modules: Mapping[MemoryType, Module],
        scopes: Sequence[str],
        capabilities: Capabilities,
    ):
        """Classify the query, feeding the classifier the vocabulary it needs to compute an unseen-term ratio."""
        ratio = 0.0
        if query.text.strip():
            from vitruvio.indices import query_terms
            from vitruvio.indices.text import out_of_vocabulary

            terms = query_terms(query.text)
            vocabularies: list[str] = []
            for scope in scopes:
                stats = self.statistics.get(scope)
                if stats is not None and stats.terms.vocabulary:
                    vocabularies.extend(stats.terms.document_frequency)
            if vocabularies:
                ratio = out_of_vocabulary(terms, vocabularies)

        predicates: frozenset[str] = frozenset()
        for scope in scopes:
            stats = self.statistics.get(scope)
            if stats is not None:
                predicates |= frozenset(stats.graph.predicates)

        return classify(
            query.text,
            mode=query.hints.mode,
            has_filters=self._has_filters(query),
            expand_depth=query.hints.expand_depth,
            out_of_vocabulary=ratio,
            known_predicates=predicates,
        )

    @staticmethod
    def _has_filters(query: Query) -> bool:
        """Whether any filter narrows the query."""
        filters = query.filters
        return bool(filters.subject or filters.since or filters.until or filters.tags or filters.evidence)

    def _predicates(
        self,
        query: Query,
        scopes: Sequence[str],
        capabilities: Capabilities,
    ) -> list[PredicateExplain]:
        """
        Extract filters and decide how each will be applied.

        The general rule, and the reason it is a rule rather than a table: **a predicate is pushdown-able iff some
        index can evaluate it without decoding a block.** Everything else is a residual at a block read per row.
        """
        filters = query.filters
        explained: list[PredicateExplain] = []

        if query.filters.memory_types:
            explained.append(
                PredicateExplain(field="memory_type", operator="in", disposition="scoping", note="selects the subplans")
            )

        if filters.subject:
            pushable = any(capabilities.has(scope, IndexKind.BITMAP) for scope in scopes)
            estimate_note, selectivity, exact = self._column_selectivity(scopes, "subject", filters.subject)
            explained.append(
                PredicateExplain(
                    field="subject",
                    operator="equals",
                    disposition="pushdown" if pushable else "residual",
                    selectivity=selectivity,
                    exact=exact,
                    note=estimate_note,
                )
            )

        if filters.tags:
            pushable = any(capabilities.has(scope, IndexKind.BITMAP) for scope in scopes)
            explained.append(
                PredicateExplain(
                    field="tags", operator="intersects", disposition="pushdown" if pushable else "residual"
                )
            )

        if filters.since or filters.until:
            pushable = any(capabilities.has(scope, IndexKind.BTREE) for scope in scopes)
            note = None
            window_selectivity: float | None = None
            for scope in scopes:
                stats = self.statistics.get(scope)
                if stats is None or "occurred_at" not in stats.time:
                    continue
                estimated = stats.time["occurred_at"].range_selectivity(filters.since, filters.until, stats.cardinality)
                window_selectivity = estimated.rows / max(1, stats.cardinality)
                note = estimated.note
            explained.append(
                PredicateExplain(
                    field="occurred_at",
                    operator="range",
                    disposition="pushdown" if pushable else "residual",
                    selectivity=window_selectivity,
                    note=note or "a block with no timestamp cannot satisfy a window",
                )
            )

        if filters.evidence:
            explained.append(
                PredicateExplain(
                    field="evidence",
                    operator="intersects",
                    disposition="residual",
                    note="read from the block's own evidence field, not from the provenance ledger, which can diverge",
                )
            )

        if not filters.include_superseded:
            explained.append(
                PredicateExplain(
                    field="superseded_by",
                    operator="absent",
                    disposition="pushdown",
                    note="applied from the provenance ledger before the limit, so the limit is not spent on hidden blocks",
                )
            )
        return explained

    def _column_selectivity(
        self, scopes: Sequence[str], field: str, value: str
    ) -> tuple[str | None, float | None, bool]:
        """The best available selectivity estimate for one point predicate, and whether it was measured."""
        for scope in scopes:
            stats = self.statistics.get(scope)
            if stats is None or field not in stats.columns:
                continue
            estimated = stats.column(field).selectivity(value, stats.cardinality)
            return estimated.note, estimated.rows / max(1, stats.cardinality), estimated.exact
        return None, None, False

    def _generators(self, scopes: Sequence[str], capabilities: Capabilities) -> set[str]:
        """
        Which **index-backed** generators the installation could supply.

        Feeds the validity rule, and the exhaustive scan is deliberately *not* counted. It is a baseline rather than a
        second opinion: counting it would make the two-generator rule unsatisfiable on a brain that has only one
        index, so every plan using that index would be rejected and the scan would win by default -- the index would
        never be consulted at all.

        The rule is about not trusting one index when another could corroborate. Where there is only one, the honest
        choice is between using it and scanning exhaustively, and that choice belongs to the cost model: the scan wins
        on a small module, where reading every block is cheaper than a probe, and loses at scale.
        """
        kinds: set[str] = set()
        for scope in scopes:
            if capabilities.has(scope, IndexKind.INVERTED):
                kinds.add("TermScan")
            if capabilities.has(scope, IndexKind.VECTOR):
                kinds.add("VectorSearch")
            if capabilities.has(scope, IndexKind.GRAPH):
                kinds.add("GraphExpand")
        return kinds

    def _stale(self, scopes: Sequence[str]) -> bool:
        """Whether any scope's statistics are stale, which bypasses the plan cache."""
        return any(scope in self.statistics and not self.statistics[scope].freshness.is_fresh for scope in scopes)

    def _enumerate(
        self,
        query: Query,
        scopes: Sequence[str],
        capabilities: Capabilities,
        intent: Any,
    ) -> list[Plan]:
        """
        Generate the candidate plans this query's capabilities permit.

        Small on purpose. The decisions that matter are which generators run, at what ``k``, and whether a filter is
        pushed or applied as a residual -- a space of tens of plans, exhaustively costable in well under a
        millisecond. A memo structure would be machinery without a problem, and it would make the "adding an index
        never worsens the plan" property harder to establish.
        """
        from vitruvio.planner.templates import build_templates

        return build_templates(
            query=query,
            scopes=scopes,
            capabilities=capabilities,
            intent=intent,
            statistics=self.statistics,
            config=self.config,
        )

    @staticmethod
    def _pareto(costed: Mapping[str, tuple[float, float]]) -> frozenset[str]:
        """
        Which plans sit on the cost/recall frontier.

        Reported in EXPLAIN because it says what the chosen plan was *traded against*, which is more useful than
        knowing only that it won.

        Args:
            costed (Mapping[str, tuple[float, float]]): Signature to cost and recall.

        Returns:
            frozenset[str]: The non-dominated signatures.
        """
        frontier: set[str] = set()
        for signature, (cost, recall) in costed.items():
            dominated = any(
                other != signature
                and other_cost <= cost
                and other_recall >= recall
                and (other_cost < cost or other_recall > recall)
                for other, (other_cost, other_recall) in costed.items()
            )
            if not dominated:
                frontier.add(signature)
        return frozenset(frontier)

    # --- Shared helpers the executor uses ------------------------------------

    def ledger_for(self, modules: Mapping[MemoryType, Module]) -> tuple[Ledger, float]:
        """
        The provenance view, cached by the provenance module's root.

        ``Ledger.of`` decodes **every** provenance block, so on a brain with fifty thousand of them it costs seconds.
        Caching it per root is not an optimisation, it is the difference between a usable planner and one that is
        blamed for latency it did not cause -- and the cost is reported in EXPLAIN as ``prelude_us`` for the same
        reason.

        Args:
            modules (Mapping[MemoryType, Module]): The installed modules.

        Returns:
            tuple[Ledger, float]: The ledger, and how many microseconds building it cost (zero on a cache hit).
        """
        provenance = modules.get(MemoryType.PROVENANCE)
        key = str(provenance.root) if provenance is not None else "none"
        if self._ledger is not None and self._ledger[0] == key:
            return self._ledger[1], 0.0

        started = time.perf_counter()
        ledger = Ledger.of(dict(modules))
        spent = (time.perf_counter() - started) * 1e6
        self._ledger = (key, ledger)
        return ledger, spent


def _digest(query: Query) -> str:
    """A short hash of a query, so two explanations can be compared without quoting the text."""
    rendered = query.model_dump_json()
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


def build_planner(
    config: PlannerConfig,
    *,
    statistics: Mapping[str, ModuleStats] | None = None,
    calibration: Calibration | None = None,
) -> CostBasedPlanner:
    """
    Construct the planner a configuration asks for.

    Args:
        config (PlannerConfig): The knobs.
        statistics (Mapping[str, ModuleStats] | None): What to cost against.
        calibration (Calibration | None): Per-unit costs.

    Returns:
        CostBasedPlanner: The planner.
    """
    return CostBasedPlanner(
        config,
        calibration=calibration or DEFAULT_CALIBRATION,
        statistics=statistics,
    )
