"""``vitruvio branch`` -- a line of work per tag, so a team stops queueing on ``latest``.

One tag shared by a team is safe and not workable: the divergence check refuses every push after the first until
its writer reconciles, so ``dist push`` exits 8 for everybody but whoever was quickest. A branch publishes to a tag
of its own -- ``main`` to ``[registry].tag``, any other to ``br.<name>`` -- and nobody on another branch can refuse
it. The joining happens once, when somebody decides to, with ``branch merge``.

The model is the one git users already have, with one difference worth knowing: **a branch exists on the registry as
a tag**, so ``dist push`` on it creates ``br.<name>`` and a teammate picks it up with ``branch switch <name>``, which
pulls that tag into a new local branch. ``dist push`` and ``dist pull`` default to the current branch's tag; an
explicit ``--tag`` (a release, say) never renames the branch.

Switching moves the head without committing anything, so nothing is re-embedded. The vector index is bound to a
Merkle root and is never read against the wrong branch; ``vitruvio index build`` refreshes it when semantic search
comes back thin after a switch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from cyclopts import App, Parameter
from rich.text import Text

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.kernel import ExitCode, ReconciliationOpenError

app = App(
    name="branch",
    help="Work on a branch of your own, publish it to its own tag, and merge it when it is ready.",
    result_action="return_value",
    exit_on_error=False,
)

LocalRegistry = Annotated[
    Path | None,
    Parameter(
        name=["--local"],
        help="Use a filesystem registry of OCI layouts rooted here. No network, no credentials, same contract.",
    ),
]


@app.command(name="list")
def list_(
    *,
    remote: bool = False,
    reference: str | None = None,
    anonymous: bool = False,
    insecure: bool | None = None,
    local: LocalRegistry = None,
) -> ExitCode:
    """List the branches, the current one first.

    `unpublished` marks a branch whose head is not the one last pushed or pulled -- work only this machine has.

    Parameters
    ----------
    remote
        Also list the branches the registry publishes, read off its `br.` tags. A network request.
    reference
        The repository to list, when it is not the one this brain publishes to.
    anonymous
        List without credentials.
    insecure
        Allow plain HTTP. Unset defers to `[registry].insecure`.
    """
    console = current().console
    result = (
        current()
        .service()
        .branch_list(remote=remote, reference=reference, anonymous=anonymous, insecure=insecure, local=local)
    )
    for warning in result.get("warnings") or ():
        console.warn(str(warning))
    table = render.table("", "branch", "tag", "head", "state")
    for entry in result["branches"]:
        table.add_row(
            Text("*", style="ok") if entry["current"] else "",
            entry["name"],
            entry["tag"],
            render.digest(entry["snapshot"]),
            Text("unpublished", style="warn") if entry["unpublished"] else Text("published", style="muted"),
        )
    views: list[Any] = [table if result["branches"] else render.empty("(no branches: nothing committed yet)")]
    if result["remote"] is not None:
        published = render.table("published branch", "tag", "local")
        for entry in result["remote"]:
            published.add_row(entry["name"], entry["tag"], render.verdict(bool(entry["local"]), yes="yes", no="no"))
        views += ["", published if result["remote"] else render.empty("(the registry publishes no branches)")]
    return console.emit("branch.list", result, view=render.stack(*views))


@app.command(name="current")
def current_() -> ExitCode:
    """Print the branch this brain is on. `main` for a brain that never created one."""
    console = current().console
    name = current().service().branch_current()
    return console.emit("branch.current", {"branch": name}, view=Text(name))


@app.command(name="create")
def create(
    name: str,
    *,
    from_: Annotated[str | None, Parameter(name=["--from"])] = None,
    switch: bool = False,
) -> ExitCode:
    """Start a branch, at the current head unless told otherwise.

    Nothing is published. The first `dist push` on it creates `br.<name>` on the registry.

    Parameters
    ----------
    name
        Segments of letters, digits, `_` and `-`, separated by `/`, such as `ana/fix-typo`.
    from_
        Where it starts: another branch, or a snapshot digest this brain holds.
    switch
        Make it current straight away.
    """
    console = current().console
    result = current().service().branch_create(name, start=from_, switch=switch)
    view = render.fields(
        [
            ("branch", result["name"]),
            ("tag", result["tag"]),
            ("head", render.digest(result["snapshot"], full=True)),
            ("current", render.verdict(bool(result["current"]), yes="yes", no="no")),
        ]
    )
    return console.emit("branch.create", result, view=view)


@app.command(name="switch")
def switch(
    name: str,
    *,
    create: Annotated[bool, Parameter(name=["--create", "-c"], negative=())] = False,
    track: bool = True,
    reference: str | None = None,
    anonymous: bool = False,
    insecure: bool | None = None,
    local: LocalRegistry = None,
) -> ExitCode:
    """Make another branch current.

    A local branch is checked out, with the current head recorded under its own branch first, so nothing
    unpublished is lost. A branch that exists only on the registry is pulled from its `br.<name>` tag into a new
    local branch -- how you pick up a teammate's. Refused while a reconciliation is open (exit 12).

    Parameters
    ----------
    name
        The branch.
    create
        Create it at the current head when it does not exist.
    track
        Pull it from the registry when it exists only there. `--no-track` refuses instead.
    reference
        The repository to track from, when it is not the one this brain publishes to.
    anonymous
        Pull without credentials.
    insecure
        Allow plain HTTP. Unset defers to `[registry].insecure`.
    """
    console = current().console
    result = (
        current()
        .service()
        .branch_switch(
            name,
            create=create,
            track=track,
            reference=reference,
            anonymous=anonymous,
            insecure=insecure,
            local=local,
        )
    )
    pulled = result.get("pull") or {}
    for warning in pulled.get("warnings") or ():
        console.warn(str(warning))
    pairs: list[tuple[str, Any]] = [
        ("branch", result["branch"]),
        ("how", result["outcome"]),
        ("head", render.digest(result["snapshot"], full=True)),
    ]
    if pulled:
        pairs.append(("from", f"{pulled['reference']}:{pulled['tag']}"))
    return console.emit("branch.switch", result, view=render.fields(pairs))


@app.command(name="delete")
def delete(name: str, *, force: bool = False) -> ExitCode:
    """Delete a local branch. Its `br.` tag on the registry is left alone.

    Refuses the current branch, and a branch that is the only name for work nobody pushed -- a prune after the delete
    would reclaim it.

    Parameters
    ----------
    name
        The branch.
    force
        Delete it even when it holds the only copy of unpushed work.
    """
    console = current().console
    result = current().service().branch_delete(name, force=force)
    return console.emit("branch.delete", result, view=render.fields([("deleted", result["deleted"])]))


@app.command(name="merge")
def merge(
    name: str,
    *,
    strategy: Literal["merge", "rebase", "squash"] | None = None,
    reason: str | None = None,
    ff_only: Annotated[bool, Parameter(name=["--ff-only"], negative=())] = False,
    no_ff: Annotated[bool, Parameter(name=["--no-ff"], negative=())] = False,
) -> ExitCode:
    """Merge another branch into the current one.

    When the current branch has not moved since, this is a fast-forward: the head moves and nothing is written.
    Otherwise the two diverged and this is a reconciliation, which needs a strategy -- `--strategy` here, or the one
    the brain declares -- because all three land the same blocks and differ in who stays on record as author. A
    reconciliation that stops to ask exits 12; `vitruvio reconcile resolve` and `continue` finish it.

    Parameters
    ----------
    name
        The branch to merge in.
    strategy
        `merge` keeps the branch's snapshots and signatures; `rebase` and `squash` re-sign the work as yours.
        Defaults to the brain's declared strategy.
    reason
        Why, recorded with a reconciliation.
    ff_only
        Refuse anything but a fast-forward.
    no_ff
        Record a reconciliation even where a fast-forward was possible.
    """
    from vitruvio.cli.commands.reconcile import _status_view, _warn_withdrawn
    from vitruvio.kernel import UsageError

    console = current().console
    if ff_only and no_ff:
        raise UsageError("--ff-only and --no-ff contradict each other", hint="pass at most one of them")
    fast_forward: Literal["auto", "only", "never"] = "only" if ff_only else "never" if no_ff else "auto"
    result = current().service().branch_merge(name, strategy=strategy, reason=reason, fast_forward=fast_forward)

    if result["outcome"] == "halted":
        halted = result["reconciliation"]
        _warn_withdrawn(halted["plan"])
        return console.fail(
            "branch.merge",
            ReconciliationOpenError(
                f"merging {name!r} stopped to ask: {len(halted['unresolved'])} open, and nothing was written",
                hint="`vitruvio reconcile resolve` decides them, `continue` concludes it, `abort` abandons it",
            ),
            data=result,
            view=_status_view(halted),
        )

    if result["outcome"] == "up-to-date":
        return console.emit("branch.merge", result, view=render.empty(f"{result['into']} already contains {name}"))
    pairs: list[tuple[str, Any]] = [
        ("merged", f"{name} into {result['into']}"),
        ("how", result["outcome"]),
        ("head", render.digest(result["snapshot"], full=True)),
    ]
    reconciliation = result.get("reconciliation")
    if reconciliation and not reconciliation["attribution"]["their_signatures_survive"]:
        console.note(f"a {strategy or 'declared-strategy'} reconciliation reissues the branch's versions as yours")
    return console.emit("branch.merge", result, view=render.fields(pairs))
