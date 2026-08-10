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

    lines = [f"manifest    {result['digest']}", f"artifact    {result['artifact_type']}", ""]
    for layer in result["layers"]:
        kind = (layer.get("annotations") or {}).get("ai.gaussia.boltzmann.memory-type", "?")
        vector = "vector index" if "index.vector" in layer["media_type"] else "module"
        lines.append(f"  {kind:<12} {vector:<14} {layer['size']:>9} bytes  {short(layer['digest'])}")
    return console.emit("dist.pack", result, lines=lines)


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
        return _push_all(tag=tag, modules=module, force=force, insecure=insecure, local=local, reference=reference)

    result = (
        current()
        .service()
        .push(reference, tag=tag, modules=module, force=force, anonymous=anonymous, insecure=insecure, local=local)
    )
    _warn(result)
    lines = [
        f"pushed      {result['reference']}:{result['tag']}",
        f"endpoint    {result['effective']}",
        f"digest      {result['digest']}",
    ]
    return console.emit("dist.push", result, lines=lines)


def _push_all(
    *,
    tag: str | None,
    modules: list[str] | None,
    force: bool,
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
            outcome = service.push(None, tag=tag, modules=modules, force=force, insecure=insecure, local=local)
            _warn(outcome)
            console.note(f"ok    {name:<18} {outcome['reference']}:{outcome['tag']}")
            results.append({"brain": name, "ok": True, "skipped": False, **outcome})
        except VitruvioError as error:
            console.warn(f"{name}: {error.message}")
            results.append({"brain": name, "ok": False, "skipped": False, "error": error.message, "code": error.code})

    published = [item for item in results if item["ok"] and not item["skipped"]]
    skipped = [item for item in results if item["skipped"]]
    failed = [item for item in results if not item["ok"]]

    lines = [f"published   {len(published)} of {len(results)} brains"]
    if skipped:
        # Counted by reason rather than lumped together. "skipped 1 with nothing committed yet" was printed for a
        # brain holding 326 blocks the moment a second reason to skip existed, and a summary that states something
        # false about the thing it just skipped is worse than one that states nothing.
        tally: dict[str, int] = {}
        for item in skipped:
            reason = str(item.get("reason") or "skipped")
            tally[reason] = tally.get(reason, 0) + 1
        lines.append("skipped     " + ", ".join(f"{count} {reason}" for reason, count in sorted(tally.items())))
    lines.append("")
    for item in results:
        state = "skip" if item["skipped"] else ("ok  " if item["ok"] else "FAIL")
        detail = item.get("reference") or item.get("error") or item.get("reason") or ""
        lines.append(f"  {state}  {item['brain']:<18} {detail}")

    if failed:
        raise VitruvioError(
            f"{len(failed)} of {len(results)} brains were not published",
            hint="the failures are listed above; each brain is independent, so the rest did publish",
        )
    return console.emit(
        "dist.push-all",
        {"brains": results, "published": len(published), "skipped": len(skipped)},
        lines=lines,
    )


@app.command(name="plan-pull")
def plan_pull(
    reference: str | None = None,
    *,
    tag: str | None = None,
    module: Annotated[list[str] | None, Parameter(name=["--module", "-m"], negative=())] = None,
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

    Worth doing every time: a canonical layer can be gigabytes, and "how much is this going to cost" should be
    answerable without paying it.

    Parameters
    ----------
    reference
        The repository.
    tag
        Which tag.
    module
        Install only these modules. Repeatable.
    anonymous
        Resolve without credentials, for a public repository.
    insecure
        Allow plain HTTP.
    """
    console = current().console
    result = (
        current()
        .service()
        .plan_pull(reference, tag=tag, modules=module, anonymous=anonymous, insecure=insecure, local=local)
    )
    _warn(result)

    size = result.get("fetch_bytes")
    lines = [
        f"reference   {result['reference']}:{result['tag']}",
        f"modules     {', '.join(result['modules']) or '(none)'}",
        f"fetch       {', '.join(result['fetch_layers']) or '(nothing -- already current)'}",
        f"reuse       {', '.join(result['reuse_layers']) or '(none)'}",
        f"vectors     {', '.join(result['fetch_vector_indices']) or '(none)'}",
        f"transfer    {f'{size / 1024:.1f} KiB' if size is not None else '(unknown)'}",
    ]
    if result["is_noop"]:
        lines.append("")
        lines.append("this brain is already at the published state")
    return console.emit("dist.plan-pull", result, lines=lines)


@app.command(name="pull")
def pull(
    reference: str | None = None,
    *,
    tag: str | None = None,
    module: Annotated[list[str] | None, Parameter(name=["--module", "-m"], negative=())] = None,
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
    anonymous
        Pull without credentials.
    insecure
        Allow plain HTTP.
    """
    from vitruvio.cli import render

    console = current().console
    result = (
        current()
        .service()
        .pull(reference, tag=tag, modules=module, anonymous=anonymous, insecure=insecure, local=local)
    )
    _warn(result)
    if result["partial"]:
        console.warn("a selective install leaves the other modules missing; `inspect resolvability` reports which")

    lines = [f"pulled      {result['reference']}:{result['tag']}", "", *render.snapshot(result["snapshot"])]
    return console.emit("dist.pull", result, lines=lines)


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
    lines = list(result["tags"]) or ["(no tags)"]
    return console.emit("dist.tags", result, lines=lines)
