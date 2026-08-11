"""``vitruvio brain`` -- create a brain, select one, and inspect its state.

Every command here except ``init`` opens the brain at ``Capability.INSPECT``, which registers no index and so
never constructs an embedder. That is not an optimisation detail: ``Brain.__init__`` rebuilds every registered
index, so without the capability gate ``vitruvio brain state`` -- a command that reads a pointer file -- would
import torch.
"""

from __future__ import annotations

from pathlib import Path

from cyclopts import App
from rich.console import RenderableType
from rich.text import Text

from vitruvio.cli import render
from vitruvio.cli.context import current
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


@app.command(name="use")
def use(path: Path) -> ExitCode:
    """Record a brain as the interactive default.

    This is the weakest layer of the four that select a brain: an explicit `--brain`, then
    `$VITRUVIO_BRAIN`, then `[brain].path` in the nearest vitruvio.toml, then this. Committing the path in a
    vitruvio.toml is the reproducible answer; this one is for a shell.

    Parameters
    ----------
    path
        The brain's layout directory.
    """
    console = current().console
    resolved = path.expanduser().resolve()

    if not is_layout(resolved):
        detail = "does not exist" if not resolved.exists() else "is not an OCI layout"
        raise BrainNotFoundError(
            f"{resolved} {detail}, so it is not a brain",
            hint=f"run `vitruvio brain init {resolved}` to create one",
        )

    state = remember_brain(resolved)
    return console.emit(
        "brain.use",
        {"brain": str(resolved), "state_file": str(state)},
        view=render.fields([("using", str(resolved))]),
    )


@app.command(name="list")
def list_() -> ExitCode:
    """List this project's brains, then the ones this machine remembers.

    Two different lists, kept apart because they answer different questions. The project's brains are the ones
    `--brain <name>` selects and `dist push --all` publishes; the remembered ones are wherever you happened to
    run `brain use`, on any project. Merging them would make a name that works here look the same as a path that
    worked somewhere else last week.

    A remembered path that no longer holds a layout is reported rather than hidden: a brain that moved is
    something to know about, and silently dropping it would make the next `--brain` failure look like it came
    from nowhere.
    """
    from vitruvio.kernel import find_config_file, load_project

    console = current().console
    context = current()

    project = load_project(context.config or find_config_file())
    members = [
        {
            "name": name,
            "path": str(project.brain_path(name)),
            "present": is_layout(path) if (path := project.brain_path(name)) else False,
            "description": project.brains[name].description,
        }
        for name in sorted(project.brains)
    ]

    state = read_state()
    current_brain = state.get("current")
    known = [item for item in state.get("known", []) if isinstance(item, str)]
    entries = [{"brain": item, "current": item == current_brain, "present": is_layout(Path(item))} for item in known]

    declared = None
    if members:
        declared = render.table("brain", "description", "state", title=f"project {project.project.name or '(unnamed)'}")
        for member in members:
            declared.add_row(
                str(member["name"]),
                Text(str(member["description"] or ""), style="muted"),
                render.verdict(bool(member["present"]), yes="created", no="not created"),
            )

    remembered = None
    if entries:
        remembered = render.table("", "brain", "state", title="remembered on this machine")
        for entry in entries:
            remembered.add_row(
                # The current brain is marked rather than named twice: this list is scanned, and a column of
                # identical paths with one asterisk is read faster than a repeated "current" label.
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
    """
    from vitruvio.kernel import PolicyProfile, resolve

    console = current().console
    context = current()

    config = resolve(
        brain=path or context.brain,
        config=context.config,
        actor_id=context.actor_id,
        actor_kind=context.actor_kind,
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

    result = BrainService(config).init(force=force)
    remember_brain(config.brain)

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
def history(*, limit: int | None = None) -> ExitCode:
    """List the retained snapshots, most recent first.

    Parameters
    ----------
    limit
        How many to show.
    """
    console = current().console
    result = current().service().history(limit=limit)
    if not result["snapshots"]:
        view: RenderableType = render.empty(
            "No snapshots yet. A brain with no canonical evidence has no version to retain."
        )
    else:
        table = render.table("snapshot", "created", ("blocks", "right"))
        for item in result["snapshots"]:
            table.add_row(render.digest(item["digest"]), item["created_at"], str(item["block_count"]))
        view = table
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
