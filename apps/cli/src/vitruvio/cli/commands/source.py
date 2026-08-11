"""``vitruvio source`` -- canonical registration, by hand or from a declared source.

Registering a source does not declare it *true*. The canonical module asserts that evidence was incorporated
and preserved; every interpretation of it is a separate block that cites it through provenance. That
distinction is what makes a brain re-interpretable when the models improve, and it is why these commands
are separate from ``vitruvio task``, which is where interpretation happens.

There is no in-place edit of evidence. A newer edition is a new block plus a supersession edge, which is what
``replace`` does.

``pull`` and ``register`` are in the same group deliberately: the noun is identical and only the mover differs --
``register`` moves bytes you already had, ``pull`` moves bytes vitruvio fetched. A user should not have to know
which of the two happened in order to find the command.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter
from rich.text import Text

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.ingest.media import EXTRA_MEDIA_TYPES, FALLBACK_MEDIA_TYPE, media_type_for
from vitruvio.kernel import ExitCode, UsageError, VitruvioError

app = App(name="source", help="Register canonical evidence.", result_action="return_value", exit_on_error=False)

__all__ = ["EXTRA_MEDIA_TYPES", "FALLBACK_MEDIA_TYPE", "app", "media_type_for"]
"""The three media-type names are re-exports.

They lived here first and moved to ``vitruvio.ingest.media`` once a ``Source`` needed them: a source runs inside
``ingest``, which sits below ``apps/cli``, and importing uphill is what ``lint-imports`` refuses. Re-exported rather
than relocated silently so that every reference to them keeps resolving where it was written.
"""


def _require_file(path: Path) -> Path:
    """Reject a missing or non-file path before opening a brain for a write."""
    resolved = path.expanduser()
    if not resolved.is_file():
        detail = "does not exist" if not resolved.exists() else "is not a file"
        raise VitruvioError(f"{resolved} {detail}", hint="pass the path of a file to register")
    return resolved


@app.command(name="register")
def register(
    path: Path,
    *,
    media_type: Annotated[str | None, Parameter(name=["--media-type"])] = None,
    origin: str | None = None,
    license_id: Annotated[str | None, Parameter(name=["--license"])] = None,
    retention_policy: Annotated[str | None, Parameter(name=["--retention-policy"])] = None,
    normalize_with: Annotated[str | None, Parameter(name=["--normalize-with"])] = None,
) -> ExitCode:
    """Register a source as canonical evidence.

    Re-registering identical bytes is a no-op: the identity is derived from the content, so the second call
    reports `duplicate` and mints no version.

    Parameters
    ----------
    path
        The file to register.
    media_type
        What the bytes are. Guessed from the extension when omitted, which is a claim rather than evidence.
    origin
        Where it came from -- a URL, a citation. Recorded in provenance, not in the block.
    license_id
        Under what licence it is held.
    retention_policy
        Under what retention policy it is held.
    normalize_with
        A normalization pipeline to produce a deterministic view, e.g. text extraction from a PDF. The view is
        canonical too: it is still evidence for ingestion, not consolidated knowledge.
    """
    console = current().console
    file = _require_file(path)

    result = (
        current()
        .service()
        .register(
            file,
            media_type=media_type_for(file, media_type),
            origin=origin,
            license_id=license_id,
            retention_policy=retention_policy,
            normalize_with=normalize_with,
        )
    )

    if result["duplicate"]:
        console.warn("identical bytes were already registered; no new version was created")
    view = render.fields(
        [
            ("block", render.digest(result["block_id"], full=True)),
            ("snapshot", render.digest(result["snapshot"])),
        ]
    )
    return console.emit("source.register", result, view=view)


@app.command(name="replace")
def replace(
    path: Path,
    *,
    supersedes: Annotated[str, Parameter(name=["--supersedes"])],
    media_type: Annotated[str | None, Parameter(name=["--media-type"])] = None,
    origin: str | None = None,
    license_id: Annotated[str | None, Parameter(name=["--license"])] = None,
    normalize_with: Annotated[str | None, Parameter(name=["--normalize-with"])] = None,
) -> ExitCode:
    """Register a newer edition and record that it supersedes an older block.

    The old block stays in the composition and keeps proving into the root. What changed is precedence, and
    precedence is a provenance edge rather than a field of either block -- so both remain pure statements about
    the bytes they preserve.

    Parameters
    ----------
    path
        The new file.
    supersedes
        The block the new edition takes precedence over.
    media_type
        What the bytes are.
    origin
        Where it came from.
    license_id
        Under what licence.
    normalize_with
        A normalization pipeline.
    """
    console = current().console
    file = _require_file(path)

    result = (
        current()
        .service()
        .replace(
            file,
            supersedes=supersedes,
            media_type=media_type_for(file, media_type),
            origin=origin,
            license_id=license_id,
            normalize_with=normalize_with,
        )
    )
    view = render.fields(
        [
            ("block", render.digest(result["block_id"], full=True)),
            ("supersedes", render.digest(result["supersedes"], full=True)),
            ("snapshot", render.digest(result["snapshot"])),
        ]
    )
    return console.emit("source.replace", result, view=view)


@app.command(name="put")
def put(
    path: Path,
    *,
    media_type: Annotated[str | None, Parameter(name=["--media-type"])] = None,
) -> ExitCode:
    """Store bytes addressably without registering a canonical block.

    For content a block will *reference* -- an image a canonical block points at, a view produced elsewhere --
    rather than content that is itself evidence.

    Parameters
    ----------
    path
        The file.
    media_type
        What the bytes are.
    """
    console = current().console
    file = _require_file(path)
    result = current().service().put_content(file, media_type=media_type_for(file, media_type))
    return console.emit(
        "source.put",
        result,
        view=render.fields(
            [
                ("blob", render.digest(result["blob"], full=True)),
                ("size", f"{result['size']} bytes"),
                ("type", result["media_type"]),
            ]
        ),
    )


# --- Declared sources ---------------------------------------------------------
#
# A source is declared in vitruvio.toml, which is committed. It names a kind and can never define one: a kind is a
# Python class, either shipped by vitruvio or written by you under $XDG_CONFIG_HOME. That means cloning a repository
# and running `source pull` cannot execute a stranger's command line, which is a property of the schema rather than
# of a confirmation prompt.


@app.command(name="pull")
def pull(
    name: str | None = None,
    *,
    all_sources: Annotated[bool, Parameter(name=["--all"])] = False,
    dry_run: Annotated[bool, Parameter(name=["--dry-run"])] = False,
    limit: int | None = None,
    refetch: bool = False,
) -> ExitCode:
    """Acquire from a declared source and register what is new as canonical evidence.

    A repeated pull is cheap rather than merely idempotent: an origin already registered is skipped *before*
    anything is fetched, by one lookup in the provenance index. Changing a source's `--media-type` or
    `normalize_with` re-registers, because both are part of a block's identity.

    Nothing here can restore redacted bytes. A digest that was tombstoned is refused, out loud, rather than
    re-fetched -- otherwise a scheduled pull would quietly undo `vitruvio retain redact`.

    Parameters
    ----------
    name
        Which declared source. Omit it with --all.
    all_sources
        Pull every declared source, each into the brain it declares. Keeps going past a failure.
    dry_run
        List and decide, fetch nothing and register nothing. What to run first when a source has just been
        pointed at a directory.
    limit
        Stop after this many registrations -- per source, and not counting skips, so it still does something on
        the second run.
    refetch
        Ignore the origin index. For a source whose addresses turned out to be unstable, or to bring back a
        block that was dropped.
    """
    console = current().console
    if all_sources:
        if name is not None:
            raise UsageError(
                "--all pulls every source and a name selects one",
                hint="drop the name, or drop --all",
            )
        result = current().service(require_brain=False).pull_all(dry_run=dry_run, limit=limit, refetch=refetch)
        for entry in result["sources"]:
            if entry["ok"]:
                counts = ", ".join(f"{value} {key}" for key, value in sorted(entry["counts"].items())) or "nothing"
                console.note(f"ok    {entry['source']!s:<18} {counts}")
            else:
                console.warn(f"{entry['source']}: {entry['error']}")
        view = render.fields(
            [
                ("registered", render.count(result["registered"])),
                ("sources", str(len(result["sources"]))),
            ]
        )
        code = console.emit("source.pull", result, view=view)
        return code if result["ok"] else ExitCode.SOURCE

    if name is None:
        raise UsageError("name which source to pull, or pass --all", hint="`vitruvio source status` lists them")

    result = current().service().pull_source(name, dry_run=dry_run, limit=limit, refetch=refetch)
    for item in result["items"]:
        if item["outcome"] == "failed":
            console.warn(f"{item['title'] or item['id']}: {item['reason']}")
        elif item["outcome"] == "skipped":
            console.note(f"skip  {item['title'] or item['id']!s:<28} {item['reason']}")
    view = render.fields(
        [
            ("source", f"{result['source']} ({result['kind']})"),
            ("listed", str(result["listed"])),
            (
                "registered",
                Text.assemble(
                    (str(result["registered"]), "count"),
                    ("  (dry run)", "warn") if result["dry_run"] else "",
                ),
            ),
        ]
    )
    failed = result["counts"].get("failed", 0)
    code = console.emit("source.pull", result, view=view)
    return ExitCode.SOURCE if failed else code


@app.command(name="status")
def status() -> ExitCode:
    """What sources this project declares, and whether each one can be used.

    `status` rather than `list`, because in this group "list" reads as "list the canonical sources in the brain",
    which is what `vitruvio inspect module canonical` does.

    A source that cannot be used is a row, not an error: a directory that has not been created yet, or a kind
    whose plugin is missing, is something to be told about rather than something to fail on.
    """
    console = current().console
    result = current().service(require_brain=False).sources()
    if not result["sources"]:
        return console.emit("source.status", result, view=render.empty("no sources declared"))
    table = render.table("", "source", "kind", "brain", "detail")
    for row in result["sources"]:
        table.add_row(
            render.verdict(bool(row["available"]), yes="ok", no="--"),
            str(row["name"]),
            Text(str(row["kind"]), style="muted"),
            Text(f"-> {row['brain']}" if row["brain"] else "", style="muted"),
            Text(row["reason"] or row["path"] or "", style="warn" if row["reason"] else "muted"),
        )
    return console.emit("source.status", result, view=table)


@app.command(name="kinds")
def kinds() -> ExitCode:
    """Every source kind this installation can construct, and where each came from.

    "built-in" is one vitruvio ships; "plugin:<path>" is a file you wrote; "entry-point:<name>" arrived with an
    installed distribution. Which of the three it is happens to be the first question worth asking when a kind
    behaves unexpectedly.
    """
    console = current().console
    result = current().service(require_brain=False).source_kinds()
    table = render.table("kind", "from")
    for row in result["kinds"]:
        table.add_row(str(row["kind"]), Text(str(row["provenance"]), style="muted"))
    return console.emit(
        "source.kinds",
        result,
        view=render.stack(
            table,
            render.empty(f"write your own in {result['plugin_dir']} -- `vitruvio source scaffold <kind>`"),
        ),
    )


@app.command(name="scaffold")
def scaffold(kind: str, *, force: bool = False) -> ExitCode:
    """Write a starter plugin for a source kind vitruvio does not ship.

    A command rather than a documentation section, because "inherit from BaseSource" leaves an author to discover
    the containment helper and the stability requirement on `origin` the hard way -- and the hard way there is a
    directory's worth of duplicate or unsafe blocks.

    Parameters
    ----------
    kind
        The name vitruvio.toml will select it by.
    force
        Overwrite an existing file. Refused by default: that file is hand-written code, and it is the one thing
        here no content address can recover.
    """
    console = current().console
    result = current().service(require_brain=False).scaffold_source(kind, force=force)
    view = render.stack(
        render.fields([("wrote", result["path"])]),
        render.empty(f'declare [sources.<name>] kind = "{result["kind"]}" in vitruvio.toml'),
    )
    return console.emit("source.scaffold", result, view=view)


@app.command(name="add")
def add(
    name: str,
    *,
    kind: Annotated[str, Parameter(name=["--kind"])],
    brain: Annotated[str | None, Parameter(name=["--brain-name"])] = None,
    path: str | None = None,
    media_type: Annotated[str | None, Parameter(name=["--media-type"])] = None,
    normalize_with: Annotated[str | None, Parameter(name=["--normalize-with"])] = None,
    license_id: Annotated[str | None, Parameter(name=["--license"])] = None,
    option: Annotated[list[str] | None, Parameter(name=["--option"])] = None,
) -> ExitCode:
    """Declare a source in vitruvio.toml.

    Parameters
    ----------
    name
        What to call it. Lowercase, because you will type it on a command line.
    kind
        Which strategy acquires from it. `vitruvio source kinds` lists what is installed.
    brain
        Which named brain it feeds. Spelled --brain-name because --brain is a global option that selects the
        brain for *this invocation*; this one is written into the file and decides every future pull.
    path
        Its root. Recorded as given and resolved against vitruvio.toml, never against the working directory.
    media_type
        Override the media type inferred from each item's name.
    normalize_with
        A normalization pipeline applied to everything this source produces.
    license_id
        Recorded on every block from this source.
    option
        A kind-specific field, as `key=value`. Repeatable.
    """
    console = current().console
    options: dict[str, object] = {}
    for entry in option or []:
        if "=" not in entry:
            raise UsageError(f"--option expects key=value, got {entry!r}", hint="e.g. --option glob='*.pdf'")
        key, value = entry.split("=", 1)
        options[key.strip()] = _typed(value.strip())

    result = (
        current()
        .service(require_brain=False)
        .add_source(
            name,
            kind=kind,
            brain=brain,
            path=path,
            media_type=media_type,
            normalize_with=normalize_with,
            license_id=license_id,
            options=options,
        )
    )
    if result["warning"]:
        console.warn(result["warning"])
    pairs: list[tuple[str, object]] = [("declared", f"{result['name']} ({result['kind']})")]
    if result["path"]:
        pairs.append(("path", result["path"]))
    pairs.append(("in", result["config_file"]))
    return console.emit("source.add", result, view=render.fields(pairs))


@app.command(name="remove")
def remove(name: str) -> ExitCode:
    """Undeclare a source. Nothing it ever registered is touched.

    Parameters
    ----------
    name
        The source's name.
    """
    console = current().console
    result = current().service(require_brain=False).remove_source(name)
    return console.emit("source.remove", result, view=render.fields([("removed", result["name"])]))


def _typed(value: str) -> object:
    """
    Read an option value as a bool, an int, or the string it is.

    Because `--option recursive=false` written into TOML as the string "false" is *true* to `bool()`, and the
    resulting behaviour -- a recursive glob nobody asked for -- is both surprising and expensive.
    """
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        return value
