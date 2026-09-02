"""``vitruvio brain`` -- create a brain, select one, and inspect its state.

Every command here except ``init`` opens the brain at ``Capability.INSPECT``, which registers no index and so
never constructs an embedder. That is not an optimisation detail: ``Brain.__init__`` rebuilds every registered
index, so without the capability gate ``vitruvio brain state`` -- a command that reads a pointer file -- would
import torch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter
from rich.console import RenderableType
from rich.text import Text

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.cli.documents import load_document
from vitruvio.kernel import (
    BrainNotFoundError,
    ConfigError,
    ExitCode,
    VitruvioError,
    is_layout,
    read_state,
    remember_brain,
)

app = App(name="brain", help="Select a brain and inspect its state.", result_action="return_value", exit_on_error=False)


def _auth_document(path: Path | None) -> dict[str, Any] | None:
    """Read a TOML or JSON trust-root document for init and migration."""
    if path is None:
        return None
    return load_document(path, label="trust-root document", error_type=ConfigError)


@app.command(name="use")
def use(brain: str) -> ExitCode:
    """Record a brain as this project's default, for a shell.

    Takes a brain the project declares, by name, or a path to any layout. The weakest layer of the ones that
    select a brain: an explicit `--brain`, then `$VITRUVIO_BRAIN`, then `[brain].path`, then a project's only
    brain, then this.

    **Scoped to the project**, and that is the important part. A name recorded here answers for this project and
    reaches no other, so two terminals working in two projects do not overwrite each other's choice — and a
    project that declares brains never resolves one from a pointer some other project left behind. A brain that
    belongs to no project still records the machine-wide pointer, which is the one case it was always right for.

    It remains the weakest answer for a reason: `--project` and `--brain` on the invocation are the ones that
    survive being read by somebody else, and the only ones an agent should rely on.

    Parameters
    ----------
    brain
        A brain the project declares, or a path to a layout directory.
    """
    from vitruvio.kernel import load_project, selection_key

    console = current().console
    context = current()

    document = load_project(context.config_file())
    key = selection_key(document)

    named = document.brain_path(brain)
    resolved = named if named is not None else Path(brain).expanduser().resolve()
    name = brain if named is not None else None

    if not is_layout(resolved):
        detail = "does not exist" if not resolved.exists() else "is not an OCI layout"
        known = ", ".join(sorted(document.brains))
        raise BrainNotFoundError(
            f"{resolved} {detail}, so it is not a brain",
            hint=(
                f"this project declares: {known}" if known else f"run `vitruvio brain init {resolved}` to create one"
            ),
        )

    state = remember_brain(resolved, project=key, name=name)
    scope = f"in project {key}" if key and name else "on this machine"
    return console.emit(
        "brain.use",
        {"brain": str(resolved), "name": name, "project": key if name else None, "state_file": str(state)},
        view=render.fields([("using", name or str(resolved)), ("scope", scope), ("path", str(resolved))]),
    )


@app.command(name="list")
def list_() -> ExitCode:
    """List this project's brains, then the ones this machine remembers.

    Two different lists, kept apart because they answer different questions. The project's brains are the ones
    `--brain <name>` selects and `dist push --all` publishes; the remembered ones are layouts this machine has
    seen, on any project. Merging them would make a name that works here look the same as a path that worked
    somewhere else last week.

    `vitruvio project list` is the list above this one: every project `--project` accepts, and their brains.

    A remembered path that no longer holds a layout is reported rather than hidden: a brain that moved is
    something to know about, and silently dropping it would make the next `--brain` failure look like it came
    from nowhere.
    """
    from vitruvio.kernel import load_project, selected_brain

    console = current().console
    context = current()

    project = load_project(context.config_file())
    chosen = selected_brain(project)
    members = [
        {
            "name": name,
            "path": str(project.brain_path(name)),
            "present": is_layout(path) if (path := project.brain_path(name)) else False,
            "description": project.brains[name].description,
            "selected": name == chosen,
        }
        for name in sorted(project.brains)
    ]

    state = read_state()
    current_brain = state.get("current")
    known = [item for item in state.get("known", []) if isinstance(item, str)]
    entries = [{"brain": item, "current": item == current_brain, "present": is_layout(Path(item))} for item in known]

    declared = None
    if members:
        declared = render.table(
            "", "brain", "description", "state", title=f"project {project.project.name or '(unnamed)'}"
        )
        for member in members:
            declared.add_row(
                # Marks this project's saved choice, not a machine-wide one -- and only ever a *default*, since
                # `--brain` on the invocation is what an agent is expected to pass.
                Text("*", style="ok") if member["selected"] else "",
                str(member["name"]),
                Text(str(member["description"] or ""), style="muted"),
                render.verdict(bool(member["present"]), yes="created", no="not created"),
            )

    remembered = None
    if entries:
        remembered = render.table("", "brain", "state", title="remembered on this machine")
        for entry in entries:
            remembered.add_row(
                # Marks the machine-wide pointer, which now applies only to a brain in no project at all -- a
                # column of identical paths with one asterisk is read faster than a repeated "current" label.
                Text("*", style="ok") if entry["current"] else "",
                str(entry["brain"]),
                Text("", style="muted") if entry["present"] else Text("missing", style="bad"),
            )

    if not members and not entries:
        console.warn("no brains recorded yet, and this project declares none")

    return console.emit(
        "brain.list",
        {
            "project": project.project.name,
            "members": members,
            "brains": entries,
            "current": current_brain,
        },
        view=render.stack(declared, "" if declared and remembered else None, remembered),
    )


@app.command(name="init")
def init(
    path: Path | None = None,
    *,
    policy: str | None = None,
    force: bool = False,
    governed: bool = False,
    trust_root: Path | None = None,
    sign_with: Annotated[list[str] | None, Parameter(name=["--sign-with"], negative=())] = None,
    govern_quorum: int = 1,
) -> ExitCode:
    """Create a brain, and a vitruvio.toml beside it.

    The configuration file is what makes the brain reproducible: it records the actor, the retention policy and
    the embedder, so that a second clone of the project retrieves comparably rather than by coincidence.

    An existing brain is opened rather than overwritten. `--force` only rewrites the configuration file.

    `--actor` is the global option and applies here too: it is what gets written into the new configuration file.

    Parameters
    ----------
    path
        Where to create it. Defaults to whatever the usual brain selection resolves to.
    policy
        A retention profile: conservative (the default), permissive, or archival.
    force
        Rewrite an existing vitruvio.toml.
    governed
        Create a governed genesis. Requires --trust-root or at least one --sign-with key.
    trust_root
        TOML or JSON trust-root document. Implies --governed.
    sign_with
        SSH-agent fingerprint that signs the genesis. Repeatable; signing is never automatic.
    govern_quorum
        Quorum when the trust root is synthesized from --sign-with keys.
    """
    from vitruvio.kernel import PolicyProfile, resolve

    console = current().console
    context = current()

    config = resolve(
        brain=path or context.brain,
        config=context.config,
        project=context.project,
        actor_id=context.actor_id,
        actor_kind=context.actor_kind,
        assisted_by=context.assisted_by,
        require_layout=False,
    )
    if policy:
        try:
            profile = PolicyProfile(policy)
        except ValueError as error:
            permitted = ", ".join(item.value for item in PolicyProfile)
            raise ConfigError(f"{policy!r} is not a retention profile; expected one of: {permitted}") from error
        config = config.model_copy(
            update={
                "project": config.project.model_copy(
                    update={"policy": config.project.policy.model_copy(update={"profile": profile})}
                )
            }
        )

    from vitruvio.runtime import BrainService

    root = _auth_document(trust_root)
    result = BrainService(config).init(
        force=force,
        governed=governed,
        trust_root=root,
        sign_with=sign_with or (),
        govern_quorum=govern_quorum,
    )
    # Scoped when the brain belongs to a named project, machine-wide when it belongs to none. A fresh brain in a
    # project must not become every *other* project's answer to "which brain", which is what an unscoped write did.
    from vitruvio.kernel import selection_key

    remember_brain(config.brain, project=selection_key(config.project), name=config.brain_name)

    head: list[tuple[str, object]] = [("created" if result["created"] else "opened", result["brain"])]
    if result["config_file"]:
        head.append(("wrote", result["config_file"]))
    # From the context, not from a parameter of this function: `--actor` is a global option owned by the meta app, so
    # a local `actor` parameter here would stay None even when the flag was passed -- which is exactly how the check
    # below silently never fired the first time it was written.
    requested = context.actor_id
    if requested and not result["config_file"] and config.config_file:
        # An existing vitruvio.toml is never rewritten without --force, so a --actor that disagrees with it would
        # otherwise be accepted in silence and then ignored on every subsequent command -- attributing this brain's
        # writes to whoever the neighbouring project named. Provenance that is wrong is worse than provenance that
        # is missing, so the disagreement is said out loud.
        from vitruvio.kernel import load_project

        # The file's actor, not `config.project.actor` -- the latter already has `--actor` merged in, so reporting it
        # would say the flag took effect, which is the opposite of what this warning is about.
        on_file = load_project(config.config_file).actor.id
        if on_file != requested:
            console.warn(
                f"--actor was not recorded: {config.config_file} already exists and names "
                f"{on_file or '(no actor)'}, which is what the next command will use. Pass --force to rewrite it, or "
                f"`vitruvio config set actor.id {requested}`"
            )
    if not config.project.actor.id:
        console.warn(
            "no actor is configured, so writes will be refused: every write is attributed in provenance. "
            "Pass --actor, or set [actor] id in vitruvio.toml"
        )
    return console.emit("brain.init", result, view=render.fields(head))


@app.command(name="migrate")
def migrate(
    *,
    to: Path,
    governed: bool = True,
    trust_root: Path | None = None,
    sign_with: Annotated[list[str] | None, Parameter(name=["--sign-with"], negative=())] = None,
    govern_quorum: int = 1,
    allow_partial: bool = False,
    dry_run: bool = False,
    report: Path | None = None,
    force_report: bool = False,
) -> ExitCode:
    """Recreate a legacy brain's current accessible state under the current protocol.

    This temporary compatibility command never migrates in place and never changes the source.
    Snapshot history and old provenance identities cannot carry across: the destination records a
    new genesis and new provenance while preserving knowledge block identities where reproducible.

    Parameters
    ----------
    to
        New destination path. It must not exist.
    governed
        Create a governed destination (the default). Use --no-governed only deliberately.
    trust_root
        Explicit TOML or JSON trust root for the new genesis.
    sign_with
        SSH-agent key that signs both genesis and final migrated snapshot. Repeatable.
    govern_quorum
        Quorum when synthesizing the trust root from --sign-with keys.
    allow_partial
        Skip non-reproducible blocks and record each omission in the report.
    dry_run
        Inspect and report without creating the destination.
    report
        Optional JSON report file in addition to normal command output.
    force_report
        Replace an existing report file; never affects either brain.
    """
    console = current().console
    console.warn("brain migrate is a temporary compatibility command; keep the source brain as the immutable archive")
    if report is not None and report.exists():
        if report.is_dir():
            raise ConfigError(f"migration report {report} is a directory", hint="choose a JSON report file path")
        if not force_report:
            raise ConfigError(f"migration report {report} already exists", hint="pass --force-report to replace it")
    result = (
        current()
        .service()
        .migrate(
            to,
            governed=governed,
            trust_root=_auth_document(trust_root),
            sign_with=sign_with or (),
            govern_quorum=govern_quorum,
            allow_partial=allow_partial,
            dry_run=dry_run,
        )
    )
    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    return console.emit(
        "brain.migrate",
        result,
        view=render.fields(
            [
                ("source", result["source"]),
                ("destination", result["destination"]),
                ("verified", render.verdict(result["verified"], no="FAILED")),
                ("completed", render.verdict(result["completed"])),
                ("preserved ids", result.get("preserved_id_count", "dry run")),
                ("skipped", len(result.get("skipped", result.get("problems", [])))),
                ("report", str(report) if report else "stdout"),
            ]
        ),
    )


@app.command(name="state")
def state() -> ExitCode:
    """Print what is installed, at which version, and where it came from."""
    console = current().console
    result = current().service().state()
    head = render.fields(
        [
            ("brain", f"{result['brain']}  (selected by {result['brain_origin']})"),
            ("actor", f"{result['actor']['id'] or '(not set)'} [{result['actor']['kind']}]"),
        ]
    )
    pulled = None
    if origin := result.get("origin"):
        pulled = render.fields(
            [
                ("pulled from", f"{origin['reference']}:{origin['tag']}"),
                *([("install", Text("partial", style="warn"))] if origin.get("partial") else []),
            ]
        )
    return console.emit(
        "brain.state",
        result,
        view=render.stack(head, "", *render.snapshot(result["snapshot"]), "" if pulled else None, pulled),
    )


@app.command(name="verify")
def verify() -> ExitCode:
    """Recompute every module's Merkle root from its blocks and compare.

    Exits 5 when the brain does not verify, so this is usable as a gate. A failure here means the stored blocks
    do not hash to the composition the snapshot commits to -- that is corruption, not a stale index.
    """
    console = current().console
    result = current().service().verify()
    if not result["verified"]:
        raise VitruvioError(
            "the brain does not verify: recomputed roots do not match the installed snapshot",
            hint="run `vitruvio inspect resolvability` to see which blocks are missing or tombstoned",
        )
    view = Text.assemble(
        (f"{result['block_count']} blocks", "count"),
        " verify against ",
        (f"{len(result['roots'])} module roots", "count"),
        ("  ok", "ok"),
    )
    return console.emit("brain.verify", result, view=view)


@app.command(name="history")
def history(*, limit: int | None = None, graph: bool = False) -> ExitCode:
    """Audit every retained or reachable commit, with HEAD first.

    Parameters
    ----------
    limit
        How many to show.
    graph
        Draw the lineage instead of a flat list. A reconciliation names more than one parent, so history is a DAG
        rather than a chain, and `*` marks the first-parent line — the one the protocol reads as what this brain is,
        and the one an audit follows — while `o` is history that arrived by being merged and `M` is where that
        happened.
    """
    console = current().console
    result = current().service().history(limit=limit)
    if graph:
        view: RenderableType | list[RenderableType] = render.graph(
            result["commits"], ancestry=result.get("ancestry") or ()
        )
    else:
        view = render.history_table(result)
    return console.emit("brain.history", result, view=view)


@app.command(name="info")
def info() -> ExitCode:
    """Print the per-module anatomy: roots, block counts, and which indices are registered."""
    console = current().console
    result = current().service().info()
    travelling = result.get("travelling_indices") or []
    footer = render.fields(
        [
            (
                "travelling indices",
                Text(", ".join(travelling), style="ok") if travelling else Text("(none)", style="warn"),
            )
        ]
    )
    # Worth saying plainly: the vector index is the one derived structure a consumer cannot rebuild, so if
    # none is vouched for, a `dist push` will publish a brain nobody else can search semantically.
    caveat = (
        None
        if travelling
        else render.empty("no vector index will be published, so a consumer cannot search this brain semantically")
    )
    return console.emit(
        "brain.info",
        result,
        view=render.stack(
            render.fields([("brain", result["brain"])]),
            "",
            *render.modules(result["modules"]),
            "",
            footer,
            caveat,
        ),
    )
