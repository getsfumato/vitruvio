"""Asking several brains of one project the same question.

A project is several brains under one configuration -- a subject per brain, a metric per brain -- and until now
every retrieval addressed exactly one of them. A compound names two or more **by their project names** and runs the
same query against each: each brain plans, executes, verifies and ranks on its own, exactly as `search` would, and
the results are composed afterwards (:mod:`vitruvio.runtime.cross_brain`).

Names only, and that is the whole scoping rule. A path is refused, so a brain from another project cannot be
composed with these -- not as a permission but because the vocabulary a compound accepts is the project's.

The fan-out lives here rather than in the CLI for the reason `pull_all` gives: which failures are fatal, and what a
brain that was skipped looks like, must be answered once, so the MCP server does not answer them again.

Each member is opened through its own :class:`~vitruvio.runtime.session.BrainSession` over a configuration derived
from this one -- same actor, same policy, same planner calibration, a different layout. This operations object holds
those sessions for the duration of one call and never a ``Brain``, which keeps the session rule of ADR-0013 intact.
It opens every member at RETRIEVE, and a RETRIEVE open rebuilds that brain's indices: a compound of three brains
costs three rebuilds, which is the price of the feature and is stated in the guide rather than hidden.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import partial
from typing import Any

from vitruvio.kernel import ResolvedConfig, UsageError, VitruvioError, is_layout
from vitruvio.runtime.ops.retrieval import RetrievalOps
from vitruvio.runtime.session import BrainSession


class CompoundOps:
    """Several brains of one project, as operations."""

    def __init__(self, session: BrainSession) -> None:
        """
        Args:
            session (BrainSession): The shared session. Its configuration names the project; no brain of it is
                opened through this session.
        """
        self.session = session

    @property
    def config(self) -> ResolvedConfig:
        """The resolved configuration, read through the session that owns it."""
        return self.session.config

    def _members(
        self, brains: Iterable[str] | None, all_brains: bool
    ) -> tuple[list[tuple[str, RetrievalOps]], list[dict[str, Any]]]:
        """
        Which brains to consult, each behind its own retrieval operations, and which declared brains were skipped.

        Args:
            brains (Iterable[str] | None): Brain names the project declares. Order is kept, duplicates dropped.
            all_brains (bool): Every declared brain whose layout exists on this machine.

        Returns:
            tuple[list[tuple[str, RetrievalOps]], list[dict[str, Any]]]: The members, and the skipped brains with
            the reason each was skipped.

        Raises:
            UsageError: If both or neither selection was given, a name is not one this project declares, or fewer
                than two brains result.
        """
        document = self.config.project
        known = ", ".join(sorted(document.brains)) or "(none)"
        requested = list(brains or [])
        if all_brains and requested:
            raise UsageError(
                "all brains and a list of brains were both given, and a compound takes one or the other",
                hint=f"drop the list to compose every brain, or drop --all to compose the ones named; known: {known}",
            )
        if not all_brains and not requested:
            raise UsageError(
                "a compound needs the brains to compose",
                hint=f"name at least two of this project's brains, or ask for all of them; known: {known}",
            )

        names: list[str] = []
        skipped: list[dict[str, Any]] = []
        if all_brains:
            for name in sorted(document.brains):
                path = document.brain_path(name)
                if path is None or not is_layout(path):
                    skipped.append({"brain": name, "reason": f"no layout at {path}"})
                else:
                    names.append(name)
        else:
            for name in requested:
                if name not in document.brains:
                    # A path lands here too, deliberately: a compound composes brains of *this* project, and the
                    # project's vocabulary is its names.
                    raise UsageError(
                        f"this project has no brain called {name!r}",
                        hint=f"a compound takes brain names from this project only; known: {known}",
                    )
                if name not in names:
                    names.append(name)

        if len(names) < 2:
            raise UsageError(
                f"a compound needs at least two brains, and {len(names)} would be consulted",
                hint=f"for one brain use `vitruvio search`; known: {known}",
            )

        members: list[tuple[str, RetrievalOps]] = []
        for name in names:
            path = document.brain_path(name)
            # Derived rather than re-resolved, as `dist push --all` does: every brain in a project shares its actor,
            # policy and planner calibration, so the only thing that varies is which layout is open.
            derived = self.config.model_copy(update={"brain": path, "brain_name": name})
            members.append((name, RetrievalOps(BrainSession(derived))))
        return members, skipped

    @staticmethod
    def _consult(name: str, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """
        Run one member's operation, naming the brain on any failure.

        The error keeps its class, its code and its exit status -- ``mapping.translate`` sets those as instance
        attributes, which rebuilding the error would drop -- and only its message changes, so a caller can tell
        which of three brains refused.
        """
        try:
            return operation()
        except VitruvioError as error:
            error.message = f"{name}: {error.message}"
            error.args = (error.message,)
            raise

    def compound_search(
        self,
        text: str = "",
        *,
        brains: Iterable[str] | None = None,
        all_brains: bool = False,
        fuse: bool = False,
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
    ) -> dict[str, Any]:
        """
        Retrieve evidence from several brains of this project for one query.

        Each brain answers on its own -- its planner, its indices, its verification -- and the answers are then
        composed. By default they are **grouped**: every brain's ranking intact, one after the other, because each
        bundle's scores are normalised to that brain's best match and do not compare across brains. With ``fuse``
        they are merged by reciprocal rank, the same rule the planner applies across generators inside one brain,
        and a block two brains both returned is one match that rises for it.

        Args:
            text (str): What to look for.
            brains (Iterable[str] | None): Brain names this project declares. At least two. A path is refused.
            all_brains (bool): Every declared brain whose layout exists here; the others are reported as skipped.
            fuse (bool): One ranking across brains rather than one per brain.
            memory_types (Iterable[str] | None): Restrict every brain to these modules.
            subject (str | None): Restrict to one subject.
            since (str | None): RFC3339 lower bound on ``occurred_at``.
            until (str | None): RFC3339 upper bound.
            tags (Iterable[str] | None): Require these tags.
            evidence (Iterable[str] | None): Require citation of these canonical blocks.
            include_superseded (bool): Include blocks a newer one has superseded.
            mode (str | None): A retrieval hint, applied in every brain.
            limit (int): How many matches to return **per brain**.
            expand_depth (int): How far to expand along graph edges, in every brain.

        Returns:
            dict[str, Any]: The composed payload: ``members`` with each brain's own summary, roots and plan, and
            ``matches`` each naming the brain or brains it came from. Never prose.
        """
        from vitruvio.runtime.cross_brain import compose

        members, skipped = self._members(brains, all_brains)
        results = []
        for name, retrieval in members:
            payload = self._consult(
                name,
                partial(
                    retrieval.search,
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
                ),
            )
            results.append((name, payload))
        return compose(self.config.project.project.name, results, fuse=fuse, skipped=skipped)

    def compound_explain(
        self,
        text: str = "",
        *,
        brains: Iterable[str] | None = None,
        all_brains: bool = False,
        memory_types: Iterable[str] | None = None,
        subject: str | None = None,
        since: str | None = None,
        until: str | None = None,
        tags: Iterable[str] | None = None,
        include_superseded: bool = False,
        mode: str | None = None,
        limit: int = 10,
        expand_depth: int = 0,
        analyze: bool = False,
    ) -> dict[str, Any]:
        """
        Report how each brain of a compound would answer the query, side by side.

        One explanation per brain, each from that brain's own planner over its own statistics. There is no compound
        plan to explain: composition happens after every brain has answered, and it is a rule rather than a
        decision.

        Args:
            text (str): The query to plan.
            brains (Iterable[str] | None): Brain names this project declares. At least two.
            all_brains (bool): Every declared brain whose layout exists here.
            analyze (bool): Execute in every brain and record actuals beside the estimates.

        Returns:
            dict[str, Any]: ``members``, each with its brain's full explanation.
        """
        members, skipped = self._members(brains, all_brains)
        explanations = []
        for name, retrieval in members:
            explanation = self._consult(
                name,
                partial(
                    retrieval.explain,
                    text,
                    memory_types=memory_types,
                    subject=subject,
                    since=since,
                    until=until,
                    tags=tags,
                    include_superseded=include_superseded,
                    mode=mode,
                    limit=limit,
                    expand_depth=expand_depth,
                    analyze=analyze,
                ),
            )
            explanations.append({"brain": name, "explanation": explanation})
        payload: dict[str, Any] = {
            "project": self.config.project.project.name,
            "brains": [name for name, _ in members],
            "skipped": skipped,
            "members": explanations,
        }
        return payload
