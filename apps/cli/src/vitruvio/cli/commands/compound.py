"""``vitruvio compound`` -- ask several brains of one project the same question.

A project holds a brain per subject, per client, per metric. Each answers a query on its own, and this group is how
two or more of them answer the *same* query in one invocation: every member plans, verifies and ranks by itself,
and what comes back is composed afterwards.

Members are named with ``--brains``, never ``--brain``: the singular is the meta app's global option and selects one
brain for every other command, while a compound is about the project rather than about any one of its brains. Names
are the project's -- a path is refused -- so composing across projects is not a thing this command can be asked to do.

Grouped by default and fused on request. Each brain's scores are normalised to its own best match, so two ``1.00``
from two brains do not compare; ``--fuse`` merges by rank instead, the rule the planner already uses inside one
brain, and a block two brains both hold rises for it.
"""

from __future__ import annotations

from typing import Annotated, Any

from cyclopts import App, Parameter

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.kernel import ExitCode

app = App(
    name="compound",
    help="Ask several brains of one project the same question.",
    result_action="return_value",
    exit_on_error=False,
)


def _names(values: list[str] | None) -> list[str] | None:
    """
    Brain names from a repeatable flag that also accepts a comma-separated value.

    ``--brains a --brains b`` and ``--brains a,b`` mean the same thing. cyclopts has no splitting of its own for
    command-line values, and making people repeat the flag for what reads naturally as one list is friction with no
    upside.

    Args:
        values (list[str] | None): What was passed, one entry per occurrence of the flag.

    Returns:
        list[str] | None: The names, in order; ``None`` when the flag was not given.
    """
    if values is None:
        return None
    return [part.strip() for value in values for part in value.split(",") if part.strip()]


def _warn_about(console: Any, result: dict[str, Any]) -> None:
    """Say which declared brains were skipped and which members were truncated, each by name."""
    for item in result.get("skipped", []):
        console.warn(f"{item['brain']}: skipped, {item['reason']}")
    for member in result.get("members", []):
        if member.get("truncated"):
            console.warn(f"{member['brain']}: the result is truncated: candidates were dropped, so there may be more")


@app.command(name="search")
def search(
    text: str = "",
    *,
    brains: Annotated[list[str] | None, Parameter(name=["--brains"], negative=())] = None,
    all_: Annotated[bool, Parameter(name=["--all"], negative=())] = False,
    fuse: Annotated[bool, Parameter(name=["--fuse"], negative=())] = False,
    memory_type: Annotated[list[str] | None, Parameter(name=["--memory-type", "-m"], negative=())] = None,
    subject: str | None = None,
    since: str | None = None,
    until: str | None = None,
    tag: Annotated[list[str] | None, Parameter(name=["--tag"], negative=())] = None,
    evidence: Annotated[list[str] | None, Parameter(name=["--evidence"], negative=())] = None,
    include_superseded: bool = False,
    mode: str | None = None,
    limit: int = 10,
    expand_depth: int = 0,
    content: bool = False,
) -> ExitCode:
    """Search several brains of this project at once and print the composed evidence.

    Every brain answers on its own -- its planner, its indices, its verification -- and the answers are composed
    afterwards. By default they are grouped: each brain's ranking intact, one after the other, with a `brains` entry
    on every match naming where it came from. With `--fuse` they are merged by reciprocal rank, and a block that two
    brains both returned is one match that rises for it.

    A score is agreement between retrieval strategies -- and, when fused, between brains. It is not a probability.
    Cite the block *and* the brain: each brain verified against its own roots, listed per member.

    Parameters
    ----------
    text
        What to look for. Natural language, terms, or a `sha256:` identity for an exact lookup.
    brains
        Brain names this project declares. Repeatable, or one comma-separated value. At least two; a path is
        refused, because a compound composes brains of this project only.
    all_
        Every brain the project declares whose layout exists on this machine. The others are reported as skipped.
    fuse
        One ranking across brains by reciprocal rank, rather than one ranking per brain.
    memory_type
        Restrict every brain to these modules. Repeatable.
    subject
        Restrict to one subject.
    since
        RFC3339 lower bound on `occurred_at`. A block with no timestamp cannot satisfy a time window.
    until
        RFC3339 upper bound.
    tag
        Require these tags. Repeatable.
    evidence
        Require citation of these canonical blocks. Repeatable.
    include_superseded
        Include blocks a newer one has superseded.
    mode
        auto, exact, lexical, semantic or associative. Restricts the plans considered in every brain; chooses none.
    limit
        How many matches to return per brain.
    expand_depth
        How far to expand along graph edges from the strongest hits, in every brain.
    content
        Print each block's full payload rather than one identifying line.
    """
    console = current().console
    result = (
        current()
        .service(require_layout=False, require_brain=False)
        .compound_search(
            text,
            brains=_names(brains),
            all_brains=all_,
            fuse=fuse,
            memory_types=memory_type,
            subject=subject,
            since=since,
            until=until,
            tags=tag,
            evidence=evidence,
            include_superseded=include_superseded,
            mode=mode,
            limit=limit,
            expand_depth=expand_depth,
        )
    )
    _warn_about(console, result)
    return console.emit("compound.search", result, view=render.compound(result, content=content))


@app.command(name="explain")
def explain(
    text: str = "",
    *,
    brains: Annotated[list[str] | None, Parameter(name=["--brains"], negative=())] = None,
    all_: Annotated[bool, Parameter(name=["--all"], negative=())] = False,
    memory_type: Annotated[list[str] | None, Parameter(name=["--memory-type", "-m"], negative=())] = None,
    subject: str | None = None,
    since: str | None = None,
    until: str | None = None,
    tag: Annotated[list[str] | None, Parameter(name=["--tag"], negative=())] = None,
    include_superseded: bool = False,
    mode: str | None = None,
    limit: int = 10,
    expand_depth: int = 0,
    analyze: bool = False,
) -> ExitCode:
    """Show how each brain of a compound would answer the query, side by side.

    One plan per brain, each chosen by that brain's own planner over its own statistics. There is no compound plan:
    composition happens after every brain has answered, and it is a rule rather than a decision. What this shows is
    why one brain scanned while another probed an index -- which is usually the answer to why a member returned
    nothing.

    Parameters
    ----------
    text
        The query to plan.
    brains
        Brain names this project declares. Repeatable, or one comma-separated value. At least two.
    all_
        Every brain the project declares whose layout exists on this machine.
    memory_type
        Restrict every brain to these modules. Repeatable.
    subject
        Restrict to one subject.
    since
        RFC3339 lower bound on `occurred_at`.
    until
        RFC3339 upper bound.
    tag
        Require these tags. Repeatable.
    include_superseded
        Include superseded blocks.
    mode
        auto, exact, lexical, semantic or associative.
    limit
        How many matches each plan should target.
    expand_depth
        Graph expansion depth.
    analyze
        Execute the plan in every brain and record actuals beside the estimates.
    """
    from vitruvio.planner import Explanation, render_tree

    console = current().console
    result = (
        current()
        .service(require_layout=False, require_brain=False)
        .compound_explain(
            text,
            brains=_names(brains),
            all_brains=all_,
            memory_types=memory_type,
            subject=subject,
            since=since,
            until=until,
            tags=tag,
            include_superseded=include_superseded,
            mode=mode,
            limit=limit,
            expand_depth=expand_depth,
            analyze=analyze,
        )
    )
    _warn_about(console, result)
    parts: list[Any] = []
    for member in result["members"]:
        explanation = member["explanation"]
        for degradation in explanation.get("degradations", []):
            console.warn(f"{member['brain']}: {degradation['kind']}: {degradation['detail']}")
        if parts:
            parts.append("")
        parts.append(render.fields([("brain", member["brain"])]))
        parts.append(render.lines(render_tree(Explanation.model_validate(explanation))))
    return console.emit("compound.explain", result, view=render.stack(*parts))
