"""``vitruvio dist`` -- publish a brain, and install one.

A published brain is an OCI artifact: a small manifest, one layer per module, and a config blob that is the snapshot
document. Because the modules are separate blobs, a consumer can install one and update only what changed -- which is the
whole point of the packaging, and why ``plan-pull`` exists.

Two guards come from the SDK and are worth knowing rather than discovering:

* **A push that would narrow the module set is refused.** Publishing fewer modules than the last version would make a
  consumer's selective update silently lose one.
* **A push that is not a fast-forward is refused**, and the check fails *closed* on any error that is not a 404 -- so a
  registry refusal that looks like an absence cannot quietly disable it. Exit 8 means the histories diverged: pull,
  re-commit, and push again. Never ``--force``, which discards someone else's version.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter
from rich.text import Text

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.cli.render import short
from vitruvio.kernel import ExitCode

app = App(
    name="dist",
    help="Publish a brain to a registry, and install one from a registry.",
    result_action="return_value",
    exit_on_error=False,
)


def _warn(result: dict[str, Any]) -> None:
    """Surface whatever the transport wanted to say, and any vector index that will not be published."""
    console = current().console
    for warning in result.get("warnings") or ():
        console.warn(str(warning))
    for module, outcome in sorted((result.get("vouched") or {}).items()):
        if outcome != "vouched":
            # A publish that omits the vector index is a publish nobody else can search semantically, and the omission
            # is otherwise silent.
            console.warn(f"{module}: the vector index will not be published -- {outcome}")


@app.command(name="pack")
def pack(
    *,
    tag: str | None = None,
    module: Annotated[list[str] | None, Parameter(name=["--module", "-m"], negative=())] = None,
) -> ExitCode:
    """Build the OCI artifact locally, without pushing.

    Useful on its own: it is how you see what a publish would contain, including whether the vector index made it in.

    Parameters
    ----------
    tag
        The tag to file it under.
    module
        Publish only these modules. Repeatable.
    """
    console = current().console
    result = current().service().pack(tag=tag, modules=module)
    _warn(result)

    head = render.fields(
        [
            ("manifest", render.digest(result["digest"], full=True)),
            ("artifact", result["artifact_type"]),
        ]
    )
    table = render.table("module", "layer", ("bytes", "right"), "digest")
    for layer in result["layers"]:
        kind = (layer.get("annotations") or {}).get("ai.gaussia.boltzmann.memory-type", "?")
        # Whether the vector index made it in is the question this command is usually run to answer, so the layer
        # kind is a column rather than a suffix on the media type nobody reads.
        vector = "index.vector" in layer["media_type"]
        table.add_row(
            render.kind(kind),
            Text("vector index", style="ok") if vector else Text("module", style="muted"),
            str(layer["size"]),
            render.digest(layer["digest"]),
        )
    return console.emit("dist.pack", result, view=render.stack(head, "", table))


@app.command(name="push")
def push(
    reference: str | None = None,
    *,
    tag: str | None = None,
    module: Annotated[list[str] | None, Parameter(name=["--module", "-m"], negative=())] = None,
    force: bool = False,
    anonymous: bool = False,
    insecure: bool = False,
    local: Annotated[
        Path | None,
        Parameter(
            name=["--local"],
            help="Use a filesystem registry of OCI layouts rooted here. No network, no credentials, same contract.",
        ),
    ] = None,
    all_: Annotated[bool, Parameter(name=["--all"])] = False,
) -> ExitCode:
    """Publish the brain to a registry.

    Exit 8 means the histories diverged: someone else pushed since this brain was pulled. Pull, re-commit, push again --
    `--force` discards their version, which is almost never what you want.

    Parameters
    ----------
    reference
        `<host>/<namespace>/<repo>`. Defaults to the configured one. `docker.io` is resolved to
        `registry-1.docker.io`, which is where the API lives.
    tag
        The tag to publish under.
    module
        Publish only these modules. Repeatable. Narrowing an existing artifact's module set is refused.
    force
        Publish even when it would not be a fast-forward. Discards the other version.
    anonymous
        Push without credentials. Docker Hub will refuse this.
    insecure
        Allow plain HTTP, for a local registry.
    all_
        Publish every brain in the project, each to its own derived repository. Refuses `reference`, which can
        only name one.
    """
    console = current().console
    if all_:
        return _push_all(
            tag=tag,
            modules=module,
            force=force,
            anonymous=anonymous,
            insecure=insecure,
            local=local,
            reference=reference,
        )

    result = (
        current()
        .service()
        .push(reference, tag=tag, modules=module, force=force, anonymous=anonymous, insecure=insecure, local=local)
    )
    _warn(result)
    view = render.fields(
        [
            ("pushed", f"{result['reference']}:{result['tag']}"),
            ("endpoint", result["effective"]),
            ("digest", render.digest(result["digest"], full=True)),
        ]
    )
    return console.emit("dist.push", result, view=view)


def _push_all(
    *,
    tag: str | None,
    modules: list[str] | None,
    force: bool,
    anonymous: bool,
    insecure: bool,
    local: Path | None,
    reference: str | None,
) -> ExitCode:
    """
    Publish every brain in the project, each to its own repository.

    Keeps going after a failure rather than stopping at the first one. Publishing five of six brains and being
    told which one did not go is a better outcome than publishing two and stopping, because the four that would
    have worked are still not published and nobody knows that either.
    """
    from vitruvio.kernel import VitruvioError
    from vitruvio.runtime import BrainService

    console = current().console
    context = current()
    if reference:
        raise VitruvioError(
            "--all publishes several brains and a reference names one repository",
            hint="drop the reference; each brain derives its own from the project namespace",
        )

    # Resolved once. Every brain in a project shares its actor, its policy and its registry, so the only thing
    # that varies per brain is which layout is open -- and re-reading the file per brain would let a project
    # change underneath a half-finished publish.
    base = context.resolve(require_brain=False)
    project = BrainService(base).project()
    brains = [brain for brain in project["brains"] if brain["exists"]]
    if not brains:
        raise VitruvioError(
            "this project holds no brains to publish",
            hint="add one with `vitruvio project add <name>`",
        )

    results: list[dict[str, Any]] = []
    for brain in brains:
        name = str(brain["name"])
        config = base.model_copy(update={"brain": Path(str(brain["path"])), "brain_name": name})
        service = BrainService(config)

        # A brain declared unpublishable is skipped rather than attempted, for the same reason an empty one is: it
        # is the project working as configured, and reporting it as a failure would make `--all` exit non-zero on a
        # project that holds one upstream brain -- which is the normal shape for a team.
        if not config.publish_allowed:
            console.note(f"skip  {name:<18} publish = false")
            results.append({"brain": name, "ok": True, "skipped": True, "reason": "publish = false"})
            continue

        # An empty brain is skipped, not attempted. A project where one subject has not been started yet is the
        # ordinary state rather than an error, and letting it come back as a failed push would make `--all` exit
        # non-zero on a perfectly healthy project until every last brain had something in it.
        if service.state()["block_count"] == 0:
            console.note(f"skip  {name:<18} nothing committed yet")
            results.append({"brain": name, "ok": True, "skipped": True, "reason": "nothing committed yet"})
            continue

        try:
            outcome = service.push(
                None, tag=tag, modules=modules, force=force, anonymous=anonymous, insecure=insecure, local=local
            )
            _warn(outcome)
            console.note(f"ok    {name:<18} {outcome['reference']}:{outcome['tag']}")
            results.append({"brain": name, "ok": True, "skipped": False, **outcome})
        except VitruvioError as error:
            console.warn(f"{name}: {error.message}")
            results.append({"brain": name, "ok": False, "skipped": False, "error": error.message, "code": error.code})

    published = [item for item in results if item["ok"] and not item["skipped"]]
    skipped = [item for item in results if item["skipped"]]
    failed = [item for item in results if not item["ok"]]

    pairs: list[tuple[str, object]] = [
        ("published", Text.assemble((str(len(published)), "count"), f" of {len(results)} brains"))
    ]
    if skipped:
        # Counted by reason rather than lumped together. "skipped 1 with nothing committed yet" was printed for a
        # brain holding 326 blocks the moment a second reason to skip existed, and a summary that states something
        # false about the thing it just skipped is worse than one that states nothing.
        tally: dict[str, int] = {}
        for item in skipped:
            reason = str(item.get("reason") or "skipped")
            tally[reason] = tally.get(reason, 0) + 1
        pairs.append(("skipped", ", ".join(f"{count} {reason}" for reason, count in sorted(tally.items()))))
    table = render.table("", "brain", "detail")
    for item in results:
        if item["skipped"]:
            state = Text("skip", style="warn")
        else:
            state = render.verdict(bool(item["ok"]), yes="ok", no="FAIL")
        detail = item.get("reference") or item.get("error") or item.get("reason") or ""
        table.add_row(state, str(item["brain"]), Text(str(detail), style="muted" if item["ok"] else "warn"))

    if failed:
        raise VitruvioError(
            f"{len(failed)} of {len(results)} brains were not published",
            hint="the failures are listed above; each brain is independent, so the rest did publish",
        )
    return console.emit(
        "dist.push-all",
        {"brains": results, "published": len(published), "skipped": len(skipped)},
        view=render.stack(render.fields(pairs), "", table),
    )


def _local_work_line(result: dict[str, Any]) -> str | None:
    """
    One line about what a pull replaces, or ``None`` when it replaces nothing of yours.

    Shared by `plan-pull` and `pull` so the two cannot come to phrase the same fact differently -- the phrasing is
    the feature here, and two copies of it drift.
    """
    work = result.get("local_work") or {}
    if not work.get("diverged"):
        return None
    blocks = work.get("blocks")
    count = "an unknown number of blocks" if blocks is None else f"{blocks} block{'' if blocks == 1 else 's'}"
    where = f" (they are in {short(str(work['snapshot']))})" if work.get("snapshot") else ""
    return f"this pull discards {count} committed here since the last pull{where}"


@app.command(name="plan-pull")
def plan_pull(
    reference: str | None = None,
    *,
    tag: str | None = None,
    module: Annotated[list[str] | None, Parameter(name=["--module", "-m"], negative=())] = None,
    ignore_vector_indices: bool = False,
    anonymous: bool = False,
    insecure: bool = False,
    local: Annotated[
        Path | None,
        Parameter(
            name=["--local"],
            help="Use a filesystem registry of OCI layouts rooted here. No network, no credentials, same contract.",
        ),
    ] = None,
) -> ExitCode:
    """Report what a pull would transfer, before transferring it.

    Worth doing every time, for two reasons. A canonical layer can be gigabytes, and "how much is this going to
    cost" should be answerable without paying it. And an install adopts the published composition, so anything
    committed here since the last pull stops being part of it — `discards` says how much, before rather than after.

    Parameters
    ----------
    reference
        The repository.
    tag
        Which tag.
    module
        Install only these modules. Repeatable.
    ignore_vector_indices
        Do not transfer published vector indices. Modules remain complete and verified; build compatible
        vectors locally with `vitruvio index build --force` before relying on semantic retrieval.
    anonymous
        Resolve without credentials, for a public repository.
    insecure
        Allow plain HTTP.
    """
    console = current().console
    result = (
        current()
        .service()
        .plan_pull(
            reference,
            tag=tag,
            modules=module,
            ignore_vector_indices=ignore_vector_indices,
            anonymous=anonymous,
            insecure=insecure,
            local=local,
        )
    )
    _warn(result)

    size = result.get("fetch_bytes")
    pairs: list[tuple[str, object]] = [
        ("reference", f"{result['reference']}:{result['tag']}"),
        ("modules", ", ".join(result["modules"]) or "(none)"),
        ("fetch", ", ".join(result["fetch_layers"]) or "(nothing -- already current)"),
        ("reuse", ", ".join(result["reuse_layers"]) or "(none)"),
        ("vectors", ", ".join(result["fetch_vector_indices"]) or "(none)"),
        ("ignored vectors", ", ".join(result["ignored_vector_indices"]) or "(none)"),
        # The one number worth paying attention to before agreeing to a transfer that can be gigabytes.
        ("transfer", render.count(f"{size / 1024:.1f} KiB") if size is not None else "(unknown)"),
    ]
    if (discards := _local_work_line(result)) is not None:
        pairs.append(("discards", Text(discards.removeprefix("this pull discards "), style="warn")))
        console.warn(discards)
    noop = render.empty("this brain is already at the published state") if result["is_noop"] else None
    return console.emit("dist.plan-pull", result, view=render.stack(render.fields(pairs), "" if noop else None, noop))


@app.command(name="pull")
def pull(
    reference: str | None = None,
    *,
    tag: str | None = None,
    module: Annotated[list[str] | None, Parameter(name=["--module", "-m"], negative=())] = None,
    ignore_vector_indices: bool = False,
    anonymous: bool = False,
    insecure: bool = False,
    local: Annotated[
        Path | None,
        Parameter(
            name=["--local"],
            help="Use a filesystem registry of OCI layouts rooted here. No network, no credentials, same contract.",
        ),
    ] = None,
) -> ExitCode:
    """Install a published brain.

    A selective pull is a legitimate, permanent state: the modules you did not take are *missing*, not broken, and
    `inspect resolvability` reports them as such.

    Parameters
    ----------
    reference
        The repository.
    tag
        Which tag.
    module
        Install only these modules. Repeatable.
    ignore_vector_indices
        Do not download or load published vector indices. The modules and their Merkle roots are still
        installed and verified; run `vitruvio index build --force` afterwards for local vector search.
    anonymous
        Pull without credentials.
    insecure
        Allow plain HTTP.
    """
    console = current().console
    result = (
        current()
        .service()
        .pull(
            reference,
            tag=tag,
            modules=module,
            ignore_vector_indices=ignore_vector_indices,
            anonymous=anonymous,
            insecure=insecure,
            local=local,
        )
    )
    _warn(result)
    if result["partial"]:
        console.warn("a selective install leaves the other modules missing; `inspect resolvability` reports which")

    pairs: list[tuple[str, object]] = [("pulled", f"{result['reference']}:{result['tag']}")]
    if ignored := result["ignored_vector_indices"]:
        pairs.append(("ignored vectors", ", ".join(ignored)))
    if discarded := int(result["discarded"]):
        # Counted exactly here rather than estimated: this is the one moment both compositions are known, so the
        # report says what happened instead of what was likely to. Stated after the fact because a pull is a
        # request to install the other side's version -- `plan-pull` is where it is stated before.
        console.warn(
            f"{discarded} block{'' if discarded == 1 else 's'} committed here are no longer in the composition; "
            f"the snapshot that held them is still in `brain history`"
        )
        pairs.append(
            ("discarded", Text(f"{discarded} blocks committed here, now outside the composition", style="warn"))
        )
    return console.emit(
        "dist.pull",
        result,
        view=render.stack(render.fields(pairs), "", *render.snapshot(result["snapshot"])),
    )


@app.command(name="tags")
def tags(
    reference: str | None = None,
    *,
    anonymous: bool = False,
    insecure: bool = False,
    local: Annotated[
        Path | None,
        Parameter(
            name=["--local"],
            help="Use a filesystem registry of OCI layouts rooted here. No network, no credentials, same contract.",
        ),
    ] = None,
) -> ExitCode:
    """List the tags a repository holds.

    Parameters
    ----------
    reference
        The repository.
    anonymous
        List without credentials.
    insecure
        Allow plain HTTP.
    """
    console = current().console
    result = current().service().tags(reference, anonymous=anonymous, insecure=insecure, local=local)
    _warn(result)
    view = render.lines(result["tags"]) if result["tags"] else render.empty("(no tags)")
    return console.emit("dist.tags", result, view=view)
