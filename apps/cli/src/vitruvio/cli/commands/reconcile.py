"""``vitruvio reconcile`` -- join a history someone else advanced into this one.

Its own group rather than more verbs under ``dist``, because the two answer different questions. ``dist`` is
transport: bytes to and from a registry. This is history: which versions this brain descends from, and whose
name stays on the work. The same split git makes between ``fetch`` and ``merge``, and for the same reason.

**The three strategies are one computation recorded three ways.** A snapshot states a whole composition rather
than a patch, so there is nothing to replay sequentially and all three land the same blocks. What differs is
the lineage, and therefore the attribution: a merge keeps their snapshots and anything they signed, a rebase
and a squash do not. So they are three commands rather than one ``--strategy`` flag -- a choice this
consequential should be in what somebody typed, not in a default they did not notice.

**A conflict here is a validation failure, not a differencing failure.** There is no merged file with markers
in it, because the unit is an immutable block: the only questions are whether a block enters and, where two
histories replaced the same block with different successors, which one wins. `resolve` answers them.

Exit 12 from any of these means the reconciliation is waiting on a decision. It is not a failure -- it is the
operation asking, and `status` lists what it asked.
"""

from __future__ import annotations

from typing import Annotated, Any

from cyclopts import App, Parameter
from rich.text import Text

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.cli.render import short
from vitruvio.kernel import ExitCode, UsageError

app = App(
    name="reconcile",
    help="Join another history into this one: merge, rebase or squash, and decide what did not apply.",
    result_action="return_value",
    exit_on_error=False,
)

STRATEGIES = ("merge", "rebase", "squash")
"""The three, in the order the attribution table reads best: most preserving first."""


def _attribution(plan: dict[str, Any]) -> Any:
    """
    What each strategy would cost, as one table.

    All three are shown together rather than only the chosen one, because the composition is identical under
    all of them -- this table *is* the difference between them, and it is only useful before the choice.
    """
    table = render.table("strategy", ("parents", "right"), ("snapshots", "right"), "keeps theirs", "their signatures")
    for name in STRATEGIES:
        entry = (plan.get("attribution") or {}).get(name)
        if entry is None:
            continue
        table.add_row(
            name,
            str(entry["parents"]),
            str(entry["snapshots_written"]),
            render.verdict(bool(entry["keeps_their_snapshots"]), yes="yes", no="no"),
            render.verdict(bool(entry["their_signatures_survive"]), yes="survive", no="LOST"),
        )
    return table


def _verdicts(plan: dict[str, Any]) -> Any:
    """Every incoming block and what the gate made of it, which is the report a maintainer acts on."""
    table = render.table("block", "module", "status", "detail")
    for entry in (plan.get("incoming") or {}).get("verdicts") or ():
        detail: list[str] = [issue.get("code", "") for issue in entry.get("issues") or ()]
        for block, why in (entry.get("missing_evidence") or {}).items():
            # The diagnosis rather than the verdict, because this is the pair that carries opposite advice:
            # dropped deliberately means do not resend, never held means resend the whole thing.
            detail.append(f"{short(block)} {why}")
        for other in entry.get("conflicts_with") or ():
            detail.append(f"vs {short(other)}")
        table.add_row(
            render.digest(entry["block"]),
            render.kind(entry.get("memory_type")),
            entry["status"],
            Text(", ".join(filter(None, detail)) or "-", style="muted"),
        )
    return table


def _warn_withdrawn(payload: dict[str, Any]) -> None:
    """
    Say out loud that work already here would leave.

    Warned rather than tabulated. Exclusion wins by construction in the set arithmetic, so a block the other
    history dropped does leave -- and a reconciliation that quietly removed something of yours would be a
    decision taken on your behalf, which is the one thing this whole loop exists to prevent.
    """
    console = current().console
    withdrawn = payload.get("withdrawn") or {}
    total = sum(len(blocks) for blocks in withdrawn.values())
    if not total:
        return
    where = ", ".join(f"{len(blocks)} in {module}" for module, blocks in sorted(withdrawn.items()) if blocks)
    console.warn(
        f"{total} block{'' if total == 1 else 's'} this brain holds would leave the composition ({where}); "
        "nothing is destroyed, but the new roots will not name them"
    )


def _plan_view(payload: dict[str, Any]) -> Any:
    """The plan, as the one screen somebody decides from."""
    pairs: list[tuple[str, Any]] = [
        ("ancestor", render.digest(payload.get("ancestor"), full=True)),
        ("theirs", render.digest(payload.get("theirs"), full=True)),
        ("clean", render.verdict(bool(payload.get("is_clean")), yes="yes", no="no -- decisions needed")),
        ("their versions", render.count(payload.get("collapsed", 0))),
    ]
    if payload.get("replayable", 0) != payload.get("collapsed", 0):
        # Fewer replayable than collapsed means the artifact carried only its head's compositions, so a rebase
        # cannot preserve the granularity of the rest. Worth stating before someone picks rebase for that.
        pairs.append(("replayable", render.count(payload.get("replayable", 0))))
    if untransferred := payload.get("untransferred"):
        pairs.append(("never arrived", Text(", ".join(untransferred), style="warn")))
    return render.stack(render.fields(pairs), "", _attribution(payload), "", _verdicts(payload))


@app.command(name="plan")
def plan(theirs: str, *, ancestor: str | None = None) -> ExitCode:
    """Report what joining another history would produce, and what each strategy would cost.

    Writes nothing. Worth running every time: the incoming blocks arrive already judged, so which parts of a
    contribution fit is known before anything is decided rather than inferred by reading a diff.

    Parameters
    ----------
    theirs
        The other history's head, already held locally. `vitruvio dist fetch` is what brings one.
    ancestor
        The snapshot to reconcile against, if you know it. Defaults to the nearest one the two share.
    """
    console = current().console
    result = current().service().reconcile_ops.plan(theirs, ancestor=ancestor)
    _warn_withdrawn(result)
    if result["is_noop"]:
        return console.emit("reconcile.plan", result, view=render.empty("this brain already contains that history"))
    return console.emit("reconcile.plan", result, view=_plan_view(result))


def _run(strategy: str, theirs: str, reason: str, ancestor: str | None) -> ExitCode:
    """
    One strategy, carried out. The three commands differ in one argument, so they share everything else.

    A halt is reported as the questions it raised rather than as a failure: it wrote nothing, it moved no
    pointer, and the caller's next step is to answer -- for which it needs to see what was asked.
    """
    console = current().console
    result = current().service().reconcile_ops.reconcile(theirs, strategy=strategy, reason=reason, ancestor=ancestor)
    if result.get("halted"):
        _warn_withdrawn(result.get("plan") or {})
        console.warn(f"the {strategy} stopped to ask: {len(result.get('unresolved') or ())} open, nothing was written")
        console.note("`vitruvio reconcile resolve` decides them, `abort` abandons the whole thing")
        return console.emit(f"reconcile.{strategy}", result, view=_status_view(result))

    attribution = result.get("attribution") or {}
    pairs: list[tuple[str, Any]] = [
        ("strategy", strategy),
        ("snapshot", render.digest(result.get("snapshot"), full=True)),
        ("parents", render.count(attribution.get("parents", "-"))),
        ("snapshots written", render.count(len(result.get("snapshots") or ()))),
    ]
    if attribution and not attribution.get("their_signatures_survive", True):
        # Stated as a consequence of the choice rather than left to be discovered. The SDK reports it for
        # exactly this reason: it must not present rebased or squashed work as bearing its author's signature.
        console.note(f"a {strategy} reissues their versions under new identities, so their work is now attributed here")
    return console.emit(f"reconcile.{strategy}", result, view=render.fields(pairs))


@app.command(name="merge")
def merge(theirs: str, *, reason: str, ancestor: str | None = None) -> ExitCode:
    """Join both histories, naming both as parents.

    The only strategy that keeps their snapshots in the history, and therefore the only one under which
    something they signed still covers something. Prefer it for a contribution from somebody else.

    Parameters
    ----------
    theirs
        The other history's head.
    reason
        Why. Required: an unattributed reconciliation is not auditable.
    ancestor
        The snapshot to reconcile against, if known.
    """
    return _run("merge", theirs, reason, ancestor)


@app.command(name="rebase")
def rebase(theirs: str, *, reason: str, ancestor: str | None = None) -> ExitCode:
    """Replay their history onto this one, minting new snapshot identities.

    Deterministic, because a snapshot states a composition rather than a patch. It invalidates any signature
    over the originals and any root already published, so it is legitimate only *before* publication — the same
    rule as any lineage rewrite.

    Parameters
    ----------
    theirs
        The other history's head.
    reason
        Why.
    ancestor
        The snapshot to reconcile against, if known.
    """
    return _run("rebase", theirs, reason, ancestor)


@app.command(name="squash")
def squash(theirs: str, *, reason: str, ancestor: str | None = None) -> ExitCode:
    """Collapse their snapshots into one.

    More useful here than in version control, because an ingestion session mints many intermediate versions
    nobody cares about individually. Every provenance record those versions produced is still kept — provenance
    is a module, so collapsing snapshots cannot drop one.

    Parameters
    ----------
    theirs
        The other history's head.
    reason
        Why.
    ancestor
        The snapshot to reconcile against, if known.
    """
    return _run("squash", theirs, reason, ancestor)


def _status_view(payload: dict[str, Any]) -> Any:
    """Where a reconciliation stands: what is open, what has been decided, and what is leaving."""
    state = payload.get("state") or {}
    pairs: list[tuple[str, Any]] = [
        ("theirs", render.digest(state.get("theirs"), full=True)),
        ("strategy", state.get("strategy", "-")),
        ("open", render.count(len(payload.get("unresolved") or ()))),
        ("decided", render.count(len(payload.get("resolved") or ()))),
    ]
    withdrawn = payload.get("withdrawn") or {}
    if sum(len(blocks) for blocks in withdrawn.values()):
        pairs.append(
            (
                "removals",
                render.verdict(
                    bool(payload.get("removals_accepted")), yes="accepted", no="NOT accepted -- accept-removals"
                ),
            )
        )
    table = render.table("block", "module", "status", "decided")
    decisions = payload.get("state", {}).get("resolutions") or {}
    for entry in (payload.get("plan") or {}).get("incoming", {}).get("verdicts") or ():
        if entry["status"] == "validated":
            continue
        made = decisions.get(entry["block"])
        table.add_row(
            render.digest(entry["block"]),
            render.kind(entry.get("memory_type")),
            entry["status"],
            Text(made["kind"], style="ok") if made else Text("-- open", style="warn"),
        )
    return render.stack(render.fields(pairs), "", table)


@app.command(name="status")
def status() -> ExitCode:
    """Report the reconciliation being resolved, if there is one.

    The plan inside it is recomputed rather than remembered, so this is the current answer and not the one from
    when the reconciliation started. What is persisted is what a person decided.
    """
    console = current().console
    result = current().service().reconcile_ops.status()
    if not result["open"]:
        return console.emit("reconcile.status", result, view=render.empty("no reconciliation is in progress"))
    _warn_withdrawn(result)
    return console.emit("reconcile.status", result, view=_status_view(result))


@app.command(name="resolve")
def resolve(
    block: str | None = None,
    *,
    admit: bool = False,
    reject: bool = False,
    prefer: str | None = None,
) -> ExitCode:
    """Decide what did not apply — one block at a time, or all of them in an interactive workspace.

    With no block, this opens a workspace: the open questions on the left, the verdict and the evidence beside
    them, and a key per decision. It starts the reconciliation if none is open yet.

    `--admit` is offered for a contradiction, which the protocol treats as information rather than a defect —
    holding two claims that disagree is a legitimate state and which one is right is not a question it answers.
    It is **refused for a rejection**: a derived block whose evidence is absent from the composition cannot be
    audited against its source, and no later check recovers it, because verification recomputes hashes and
    compositions rather than citations across modules. Supply the evidence in an ordinary commit instead.

    Parameters
    ----------
    block
        Which incoming block. Omit to open the workspace.
    admit
        Let it in anyway.
    reject
        Leave it out. The block is not destroyed — it stays in the store and in the history it came from.
    prefer
        The winning successor, when two histories replaced the same block with different ones.
    """
    console = current().console
    if block is None:
        if admit or reject or prefer:
            raise UsageError(
                "a decision needs a block to apply to",
                hint="name the block, or drop the flag to open the workspace and decide there",
            )
        return _workspace()

    chosen = [name for name, on in (("admit", admit), ("reject", reject), ("prefer", bool(prefer))) if on]
    if len(chosen) != 1:
        raise UsageError(
            "exactly one decision per block" if chosen else "no decision given",
            hint="one of --admit, --reject, or --prefer <BLOCK>",
        )
    result = current().service().reconcile_ops.resolve(block, kind=chosen[0], prefer=prefer)
    if result["is_resolved"]:
        console.note("every question is answered; `vitruvio reconcile continue` concludes it")
    return console.emit("reconcile.resolve", result, view=_status_view(result))


def _workspace() -> ExitCode:
    """
    Open the interactive resolver.

    The two refusals are `browse`'s, for its reasons: there is nothing to show without a terminal, and
    ``--json`` names an output mode this has no output in. Textual is imported only here -- it is a larger
    import than the whole rest of the CLI, and every other command starting fast depends on not paying it.
    """
    import os
    import sys

    console = current().console
    if console.json_mode:
        raise UsageError(
            "the resolver is interactive and has no JSON output",
            hint="`vitruvio reconcile status --json` is the same data, and `resolve <BLOCK> --admit` decides one",
        )
    served = "web_driver" in os.environ.get("TEXTUAL_DRIVER", "")
    if not served and not sys.stdout.isatty():
        raise UsageError(
            "the resolver needs a terminal, and stdout is not one",
            hint="decide them one at a time with `vitruvio reconcile resolve <BLOCK> --admit|--reject`",
        )

    from vitruvio.cli.tui.reconcile import Resolver

    context = current()
    service = context.service()
    strategy = service.reconcile_ops.declared_strategy()
    Resolver(service, strategy=str(strategy) if strategy else None).run()
    return ExitCode.OK


@app.command(name="accept-removals")
def accept_removals() -> ExitCode:
    """State that the work this reconciliation removes may go.

    One answer rather than one per block, because there is no per-block choice to offer: exclusion wins by
    construction, so a block the other history dropped cannot be kept by choosing to keep it. Deliberately
    re-admitting one is an ordinary commit, outside this.
    """
    console = current().console
    result = current().service().reconcile_ops.accept_removals()
    if result["is_resolved"]:
        console.note("every question is answered; `vitruvio reconcile continue` concludes it")
    return console.emit("reconcile.accept-removals", result, view=_status_view(result))


@app.command(name="continue")
def continue_() -> ExitCode:
    """Conclude the reconciliation now that its questions are answered.

    Refuses with exit 12 while any candidate is undecided: the protocol declined to decide it, and committing
    would decide it on your behalf.
    """
    console = current().console
    result = current().service().reconcile_ops.continue_()
    pairs: list[tuple[str, Any]] = [
        ("strategy", result.get("strategy", "-")),
        ("snapshot", render.digest(result.get("snapshot"), full=True)),
        ("snapshots written", render.count(len(result.get("snapshots") or ()))),
    ]
    return console.emit("reconcile.continue", result, view=render.fields(pairs))


@app.command(name="abort")
def abort() -> ExitCode:
    """Abandon the reconciliation being resolved.

    Nothing is undone, because a halted reconciliation never wrote a composition or moved the pointer. What is
    discarded is the record of the decisions taken so far.
    """
    console = current().console
    result = current().service().reconcile_ops.abort()
    view = render.fields(
        [
            ("abandoned", render.digest(result["theirs"], full=True)),
            ("strategy", result["strategy"]),
            ("decisions discarded", render.count(result["decisions"])),
        ]
    )
    return console.emit("reconcile.abort", result, view=view)


@app.command(name="tree")
def tree(
    theirs: str | None = None,
    *,
    ancestor: Annotated[str | None, Parameter(name=["--ancestor"])] = None,
) -> ExitCode:
    """Show where two histories parted, and what each has added since.

    Answers the question a reconciliation is about — what diverged — rather than the one `brain history`
    answers, which is what this brain is on its own.

    Parameters
    ----------
    theirs
        The other history's head. Defaults to the one an open reconciliation names.
    ancestor
        The shared snapshot, if known.
    """
    console = current().console
    result = current().service().reconcile_ops.tree(theirs, ancestor=ancestor)
    return console.emit("reconcile.tree", result, view=render.divergence(result))


__all__ = ["app"]
