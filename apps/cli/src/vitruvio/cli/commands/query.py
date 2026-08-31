"""``vitruvio query`` -- retrieve evidence.

The query names no index. Filters narrow the candidate set, hints *advise* the planner, and which indices to
consult is the planner's decision -- that is the protocol's rule, not an implementation convenience, and it is
why there is no ``--index`` flag to add.

The filter that matters most in practice is ``--memory-type``. Without it, "what happened in the class of May
14" and "the definition of a Fourier series" compete in a single similarity ranking, which is exactly the
failure that typed memories exist to prevent.
"""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.kernel import ExitCode

app = App(name="query", help="Retrieve evidence from the brain.", result_action="return_value", exit_on_error=False)


@app.command(name="search")
def search(
    text: str = "",
    *,
    memory_type: Annotated[list[str] | None, Parameter(name=["--memory-type", "-m"], negative=())] = None,
    subject: str | None = None,
    since: str | None = None,
    until: str | None = None,
    tag: Annotated[list[str] | None, Parameter(name=["--tag"], negative=())] = None,
    classes: Annotated[list[str] | None, Parameter(name=["--class"], negative=())] = None,
    evidence: Annotated[list[str] | None, Parameter(name=["--evidence"], negative=())] = None,
    include_superseded: bool = False,
    mode: str | None = None,
    limit: int = 10,
    expand_depth: int = 0,
    content: bool = False,
) -> ExitCode:
    """Search the brain and print the Evidence Bundle.

    What comes back is data with provenance and a score -- never prose. Turning it into an answer is the
    caller's job, which is the boundary the whole architecture is built around.

    A score is agreement between retrieval strategies, not a probability. Do not present it as confidence.

    Parameters
    ----------
    text
        What to look for. Natural language, terms, or a `sha256:` identity for an exact lookup.
    memory_type
        Restrict to these modules. Repeatable. This is the filter that keeps episodes from competing with
        definitions.
    subject
        Restrict to one subject.
    since
        RFC3339 lower bound on `occurred_at`. A block with no timestamp cannot satisfy a time window.
    until
        RFC3339 upper bound.
    tag
        Require these tags. Repeatable.
    classes
        Require evidence placed in every case-sensitive `scheme/label` class. Descendants count.
    evidence
        Require citation of these canonical blocks. Repeatable.
    include_superseded
        Include blocks a newer one has superseded. They stay in the composition and stay verifiable; what
        changed is accessibility.
    mode
        auto, exact, lexical, semantic or associative. Restricts the plans considered; does not choose one.
    limit
        How many matches to return.
    expand_depth
        How far to expand along graph edges from the strongest hits.
    content
        Print each block's full payload rather than one identifying line.
    """
    console = current().console
    result = (
        current()
        .service()
        .search(
            text,
            memory_types=memory_type,
            subject=subject,
            since=since,
            until=until,
            tags=tag,
            classes=classes,
            evidence=evidence,
            include_superseded=include_superseded,
            mode=mode,
            limit=limit,
            expand_depth=expand_depth,
        )
    )
    if result.get("truncated"):
        console.warn("the result is truncated: candidates were dropped, so there may be more")
    return console.emit("query.search", result, view=render.bundle(result, content=content))


@app.command(name="resolve")
def resolve(block_id: str) -> ExitCode:
    """Read one block by identity.

    Parameters
    ----------
    block_id
        A `sha256:...` block identity.
    """
    console = current().console
    result = current().service().resolve(block_id)
    head = render.fields(
        [
            ("block", render.digest(result["block_id"], full=True)),
            ("memory type", render.kind(result["memory_type"])),
        ]
    )
    return console.emit("query.resolve", result, view=render.stack(head, "", render.payload(result["payload"])))


@app.command(name="prove")
def prove(
    block_id: str,
    *,
    memory_type: Annotated[str, Parameter(name=["--memory-type"])],
) -> ExitCode:
    """Produce a verified Merkle inclusion proof for one block.

    Parameters
    ----------
    block_id
        The block.
    memory_type
        Which module should contain it.
    """
    console = current().console
    result = current().service().prove(block_id, memory_type)
    view = render.fields(
        [
            ("block", render.digest(result["block_id"], full=True)),
            ("root", render.digest(result["root"], full=True)),
            ("leaf index", f"{result['leaf_index']} of {result['tree_size']}"),
            ("verified", render.verdict(result["verified"], no="NO")),
        ]
    )
    return console.emit("query.prove", result, view=view)


@app.command(name="explain")
def explain(
    text: str = "",
    *,
    memory_type: Annotated[list[str] | None, Parameter(name=["--memory-type", "-m"], negative=())] = None,
    subject: str | None = None,
    since: str | None = None,
    until: str | None = None,
    tag: Annotated[list[str] | None, Parameter(name=["--tag"], negative=())] = None,
    classes: Annotated[list[str] | None, Parameter(name=["--class"], negative=())] = None,
    include_superseded: bool = False,
    mode: str | None = None,
    limit: int = 10,
    expand_depth: int = 0,
    analyze: bool = False,
) -> ExitCode:
    """Show how a query would be answered, and what the alternatives cost.

    The most useful line is usually the one about indices: it names what was available but *not* chosen, which turns
    "why didn't it use the vector index" from an argument into a lookup. The four possible answers -- absent, empty,
    stale, or more expensive than the alternative -- are all visible here.

    Parameters
    ----------
    text
        The query to plan.
    memory_type
        Restrict to these modules. Repeatable.
    subject
        Restrict to one subject.
    since
        RFC3339 lower bound on `occurred_at`.
    until
        RFC3339 upper bound.
    tag
        Require these tags. Repeatable.
    classes
        Require evidence placed in every case-sensitive `scheme/label` class.
    include_superseded
        Include superseded blocks.
    mode
        auto, exact, lexical, semantic or associative.
    limit
        How many matches the plan should target.
    expand_depth
        Graph expansion depth.
    analyze
        Execute the plan and record actuals, so the estimates can be checked against them rather than trusted.
    """
    from vitruvio.planner import Explanation, render_tree

    console = current().console
    result = (
        current()
        .service()
        .explain(
            text,
            memory_types=memory_type,
            subject=subject,
            since=since,
            until=until,
            tags=tag,
            classes=classes,
            include_superseded=include_superseded,
            mode=mode,
            limit=limit,
            expand_depth=expand_depth,
            analyze=analyze,
        )
    )
    for degradation in result.get("degradations", []):
        console.warn(f"{degradation['kind']}: {degradation['detail']}")
    return console.emit("query.explain", result, lines=render_tree(Explanation.model_validate(result)))
