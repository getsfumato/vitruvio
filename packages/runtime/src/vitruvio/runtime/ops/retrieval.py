"""Searching the brain, and explaining what the planner decided.

The two halves of one operation. `search` runs the plan and returns the evidence; `explain` runs the planner and
returns the plan without executing it, which is what makes a cost model auditable rather than merely fast.

Nothing here writes prose. The bundle is evidence with scores and provenance, and the caller decides what to say
about it -- which is the sentence the whole service layer exists to keep true.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from boltzmann.query.request import Query

from vitruvio.kernel import ResolvedConfig, VitruvioError
from vitruvio.runtime import wire
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.coerce import block_id
from vitruvio.runtime.coerce import memory_type as coerce_memory_type
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.session import BrainSession


class RetrievalOps:
    """Retrieval, as operations."""

    def __init__(self, session: BrainSession) -> None:
        """
        Args:
            session (BrainSession): The shared session.
        """
        self.session = session

    @property
    def config(self) -> ResolvedConfig:
        """The resolved configuration, read through the session that owns it."""
        return self.session.config

    def _build_query(
        self,
        text: str,
        *,
        memory_types: Iterable[str] | None,
        subject: str | None,
        since: str | None,
        until: str | None,
        tags: Iterable[str] | None,
        evidence: Iterable[str] | None,
        include_superseded: bool,
        mode: str | None,
        limit: int,
        expand_depth: int,
    ) -> Query:
        """
        Build the declarative query.

        One place, so ``search`` and ``explain`` cannot drift on what a filter means -- an explanation of a different
        query than the one that ran would be worse than no explanation.

        Returns:
            Query: The query. It names no index, by protocol: which to consult is the planner's decision.
        """
        from boltzmann.query.request import Query, QueryFilters, QueryHints, RetrievalMode

        with translated():
            return Query(
                text=text,
                filters=QueryFilters(
                    memory_types=[coerce_memory_type(item) for item in memory_types] if memory_types else None,
                    subject=subject,
                    since=since,
                    until=until,
                    tags=list(tags) if tags else None,
                    evidence=[block_id(item) for item in evidence] if evidence else None,
                    include_superseded=include_superseded,
                ),
                hints=QueryHints(
                    mode=RetrievalMode(mode) if mode else self.config.project.planner.mode_default,
                    limit=limit,
                    expand_depth=expand_depth,
                ),
            )

    def search(
        self,
        text: str = "",
        *,
        memory_types: Iterable[str] | None = None,
        subject: str | None = None,
        since: str | None = None,
        until: str | None = None,
        tags: Iterable[str] | None = None,
        evidence: Iterable[str] | None = None,
        include_superseded: bool = False,
        mode: str | None = None,
        limit: int = 10,
        expand_depth: int = 0,
        diagnostics: bool = False,
    ) -> dict[str, Any]:
        """
        Retrieve evidence.

        The query names no index. Which indices to consult, and how to combine them, is the planner's decision
        -- that is the protocol's rule, not an implementation convenience.

        Args:
            text (str): What to look for.
            memory_types (Iterable[str] | None): Restrict to these modules. This is the filter that stops
                "what happened in May" from competing with "define a Fourier series".
            subject (str | None): Restrict to one subject.
            since (str | None): RFC3339 lower bound on ``occurred_at``.
            until (str | None): RFC3339 upper bound.
            tags (Iterable[str] | None): Require these tags.
            evidence (Iterable[str] | None): Require citation of these canonical blocks.
            include_superseded (bool): Include blocks a newer one has superseded.
            mode (str | None): A retrieval hint. It restricts the plans considered; it does not choose one.
            limit (int): How many matches to return.
            expand_depth (int): How far to expand along graph edges.
            diagnostics (bool): Include query-scoped visual data for a human interface. Ordinary API calls leave it
                off because projecting embeddings has a cost and is not part of an Evidence Bundle.

        Returns:
            dict[str, Any]: An Evidence Bundle. Blocks, provenance and scores -- never prose.
        """
        query = self._build_query(
            text,
            memory_types=memory_types,
            subject=subject,
            since=since,
            until=until,
            tags=tags,
            evidence=evidence,
            include_superseded=include_superseded,
            mode=mode,
            limit=limit,
            expand_depth=expand_depth,
        )
        brain = self.session.brain(Capability.RETRIEVE)
        with translated():
            bundle = brain.search(query)
            payload = wire.evidence(bundle)

        planner = getattr(brain, "planner", None)
        explanation = getattr(planner, "last_explanation", None)
        if explanation is not None:
            payload["plan"] = {
                "signature": explanation.chosen.signature,
                "intent": explanation.intent.kind,
                "indices_consulted": {scope: list(kinds) for scope, kinds in explanation.indices_consulted.items()},
                "indices_available": {scope: list(kinds) for scope, kinds in explanation.indices_available.items()},
                "operators": [item.model_dump(mode="json") for item in explanation.chosen.operators],
                "est_cost_us": explanation.chosen.total_est_cost_us,
                "est_recall": explanation.chosen.est_recall,
                "degradations": [item.model_dump(mode="json") for item in explanation.degradations],
            }
            if diagnostics:
                from vitruvio.runtime.query_diagnostics import query_diagnostics

                visual = query_diagnostics(brain, text, list(payload.get("matches", [])), explanation)
                payload["diagnostics"] = visual
                # GraphExpand executes over a federated view, so its operator scope is not the complete set of graph
                # indices touched. The human plan view names the actual scopes from the diagnostic pass.
                for scope in visual["graph"]["scopes"]:
                    kinds = payload["plan"]["indices_consulted"].setdefault(scope, [])
                    if "graph" not in kinds:
                        kinds.append("graph")
                        kinds.sort()
        return payload

    def explain(
        self,
        text: str = "",
        *,
        memory_types: Iterable[str] | None = None,
        subject: str | None = None,
        since: str | None = None,
        until: str | None = None,
        tags: Iterable[str] | None = None,
        evidence: Iterable[str] | None = None,
        include_superseded: bool = False,
        mode: str | None = None,
        limit: int = 10,
        expand_depth: int = 0,
        analyze: bool = False,
    ) -> dict[str, Any]:
        """
        Report how a query would be answered, or was.

        Args:
            analyze (bool): Execute and record actuals, so the estimates can be checked against them. Without it,
                nothing runs and only the estimates are reported.

        Returns:
            dict[str, Any]: The full explanation: the chosen plan, the alternatives with their costs, each
            predicate's disposition, and which indices were available but not chosen.
        """
        query = self._build_query(
            text,
            memory_types=memory_types,
            subject=subject,
            since=since,
            until=until,
            tags=tags,
            evidence=evidence,
            include_superseded=include_superseded,
            mode=mode,
            limit=limit,
            expand_depth=expand_depth,
        )
        brain = self.session.brain(Capability.RETRIEVE)
        planner = getattr(brain, "planner", None)
        if planner is None or not hasattr(planner, "explain"):
            raise VitruvioError(
                "no cost-based planner is configured, so there is no plan to explain",
                hint="the SDK's linear scan has no plan; register indices with `vitruvio index build`",
            )

        with translated():
            modules = brain.modules()
            if analyze:
                _, explanation = planner.analyze(query, modules)
            else:
                explanation = planner.explain(query, modules)
        payload: dict[str, Any] = explanation.model_dump(mode="json")
        return payload
