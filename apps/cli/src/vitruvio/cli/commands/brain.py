"""``vitruvio brain`` -- create a brain, select one, and inspect its state.

Every command here except ``init`` opens the brain at ``Capability.INSPECT``, which registers no index and so
never constructs an embedder. That is not an optimisation detail: ``Brain.__init__`` rebuilds every registered
index, so without the capability gate ``vitruvio brain state`` -- a command that reads a pointer file -- would
import torch.
"""

from __future__ import annotations

from pathlib import Path

from cyclopts import App

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
        lines=[f"using {resolved}"],
    )


@app.command(name="list")
def list_() -> ExitCode:
    """List the brains this machine knows about, most recently used first.

    A remembered path that no longer holds a layout is reported rather than hidden: a brain that moved is
    something to know about, and silently dropping it from the list would make the next `--brain` failure
    look like it came from nowhere.
    """
    console = current().console
    state = read_state()
    current_brain = state.get("current")
    known = [item for item in state.get("known", []) if isinstance(item, str)]

    entries = [
        {
            "brain": item,
            "current": item == current_brain,
            "present": is_layout(Path(item)),
        }
        for item in known
    ]

    if not entries:
        console.warn("no brains recorded yet")

    lines = [
        f"{'*' if entry['current'] else ' '} {entry['brain']}" + ("" if entry["present"] else "   (missing)")
        for entry in entries
    ]
    return console.emit("brain.list", {"brains": entries, "current": current_brain}, lines=lines)


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

    lines = [
        f"{'created' if result['created'] else 'opened'} {result['brain']}",
        *(["", f"wrote {result['config_file']}"] if result["config_file"] else []),
    ]
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
    return console.emit("brain.init", result, lines=lines)


@app.command(name="state")
def state() -> ExitCode:
    """Print what is installed, at which version, and where it came from."""
    console = current().console
    result = current().service().state()
    lines = [
        f"brain      {result['brain']}  (selected by {result['brain_origin']})",
        f"actor      {result['actor']['id'] or '(not set)'} [{result['actor']['kind']}]",
        "",
        *render.snapshot(result["snapshot"]),
    ]
    if origin := result.get("origin"):
        lines += [
            "",
            f"pulled from {origin['reference']}:{origin['tag']}" + ("  (partial)" if origin.get("partial") else ""),
        ]
    return console.emit("brain.state", result, lines=lines)


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
    lines = [f"ok  {result['block_count']} blocks verify against {len(result['roots'])} module roots"]
    return console.emit("brain.verify", result, lines=lines)


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
    lines = [
        f"{render.short(item['digest'])}  {item['created_at']}  {item['block_count']:>6} blocks"
        for item in result["snapshots"]
    ]
    if not lines:
        lines = ["No snapshots yet. A brain with no canonical evidence has no version to retain."]
    return console.emit("brain.history", result, lines=lines)


@app.command(name="info")
def info() -> ExitCode:
    """Print the per-module anatomy: roots, block counts, and which indices are registered."""
    console = current().console
    result = current().service().info()
    lines = [
        f"brain  {result['brain']}",
        "",
        *render.modules(result["modules"]),
    ]
    travelling = result.get("travelling_indices") or []
    lines += [
        "",
        f"travelling indices  {', '.join(travelling) if travelling else '(none)'}",
    ]
    if not travelling:
        # Worth saying plainly: the vector index is the one derived structure a consumer cannot rebuild, so if
        # none is vouched for, a `dist push` will publish a brain nobody else can search semantically.
        lines.append("  no vector index will be published, so a consumer cannot search this brain semantically")
    return console.emit("brain.info", result, lines=lines)
