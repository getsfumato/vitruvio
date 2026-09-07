"""``vitruvio inspect`` -- read the brain's structure without changing it.

The command that earns its place here is ``resolvability``. A block can be a **verifiable member** of a version
and still not be readable: after a selective install, or after a redaction destroyed its bytes under an erasure
policy. Those two are different from each other and both are different from corruption, and a tool that
reported them identically would make a removed block indistinguishable from a broken one -- which the protocol
explicitly forbids.

``blocks``, ``content`` and ``links`` are the reading commands. They answer "what is in here", which no index
is consulted for and no plan is made for -- as distinct from ``search``, which ranks. They are also exactly what
``vitruvio browse`` draws: the TUI is a second interface over these three calls rather than a second
implementation of them, which is why a fix to either shows up in both.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter
from rich.console import RenderableType
from rich.text import Text

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.cli.render import short
from vitruvio.kernel import ExitCode, VitruvioError

app = App(
    name="inspect",
    help="Read the brain's structure: roots, modules, blocks, content, resolvability.",
    result_action="return_value",
    exit_on_error=False,
)


@app.command(name="resolvability")
def resolvability() -> ExitCode:
    """Report which blocks are readable, which are tombstoned, and which are simply absent.

    `intact` being false is not automatically a problem: a brain installed selectively is missing layers on
    purpose. What it does mean is that some block a root still names cannot be read, and the report says which.
    """
    console = current().console
    result = current().service().resolvability()

    counts = result["counts"]
    modules = sorted(set(counts["resolvable"]) | set(counts["tombstoned"]) | set(counts["missing"]))
    if not modules:
        view: list[RenderableType] = [render.empty("No modules installed.")]
    else:
        table = render.table("module", ("resolvable", "right"), ("tombstoned", "right"), ("missing", "right"))
        for kind in modules:
            tombstoned, missing = counts["tombstoned"].get(kind, 0), counts["missing"].get(kind, 0)
            table.add_row(
                render.kind(kind),
                str(counts["resolvable"].get(kind, 0)),
                Text(str(tombstoned), style="warn" if tombstoned else "muted"),
                Text(str(missing), style="warn" if missing else "muted"),
            )
        view = [table, ""]

    view.append(render.fields([("intact", render.verdict(result["intact"]))]))
    if not result["intact"]:
        view.append(
            render.lines(
                [
                    "a tombstoned block is a verifiable member whose bytes were destroyed under policy",
                    "a missing block was never installed -- a selective pull is the usual reason",
                ],
                style="muted",
            )
        )
    return console.emit("inspect.resolvability", result, view=view)


@app.command(name="roots")
def roots() -> ExitCode:
    """Print every installed module's Merkle root, and the snapshot digest that pins the set.

    Two brains holding the same knowledge have the same roots, whatever they were stored or transported by.
    That is what makes a root the identity of a knowledge state rather than of a file.
    """
    console = current().console
    result = current().service().roots()
    if not result["roots"]:
        return console.emit("inspect.roots", result, view=render.empty("No modules installed."))

    table = render.table("module", "root")
    for kind, root in sorted(result["roots"].items()):
        table.add_row(render.kind(kind), render.digest(root, full=True))
    return console.emit(
        "inspect.roots",
        result,
        view=render.stack(render.fields([("snapshot", render.digest(result["snapshot"], full=True))]), "", table),
    )


@app.command(name="module")
def module(
    memory_type: str,
    *,
    limit: Annotated[int, Parameter(name=["--limit"])] = 20,
) -> ExitCode:
    """Print one module's shape and a sample of its block identities.

    Parameters
    ----------
    memory_type
        canonical, episodic, semantic, procedural or provenance.
    limit
        How many block identities to list.
    """
    console = current().console
    result = current().service().module(memory_type, limit=limit)
    head = render.fields(
        [
            ("module", render.kind(result["memory_type"])),
            ("root", render.digest(result["root"], full=True)),
            ("blocks", str(result["block_count"])),
            ("indices", ", ".join(result["indices"]) or "(none)"),
        ]
    )
    identities = render.lines(result["block_ids"], style="digest")
    more = None
    if result["truncated"]:
        remaining = result["block_count"] - len(result["block_ids"])
        more = Text(
            f"... {remaining} more -- raise --limit, or run `vitruvio inspect blocks` for what they say", "muted"
        )
    return console.emit("inspect.module", result, view=render.stack(head, "", identities, more))


@app.command(name="blocks")
def blocks(
    memory_type: str,
    *,
    limit: Annotated[int, Parameter(name=["--limit"])] = 50,
    offset: Annotated[int, Parameter(name=["--offset"])] = 0,
    contains: Annotated[str | None, Parameter(name=["--contains"])] = None,
) -> ExitCode:
    """List what a module holds, one line per block, in the module's own order.

    The difference from `inspect module` is that this reads the blocks: it prints what each one says rather than
    only its identity. A canonical block is listed by the origin its registration recorded, so a canonical module
    reads as the files that went into it.

    There is no score column, and there is no ranking. `--contains` filters rows that were already read; it names
    no index and cannot rank. Retrieval is `vitruvio search`, where a cost model chooses the plan.

    Parameters
    ----------
    memory_type
        canonical, episodic, semantic, procedural or provenance.
    limit
        How many rows to print.
    offset
        How many matching rows to skip.
    contains
        Only rows whose title, detail, subject, tags or identity contain this, case-insensitively.
    """
    console = current().console
    result = current().service().blocks(memory_type, limit=limit, offset=offset, contains=contains)
    return console.emit("inspect.blocks", result, view=render.rows(result))


@app.command(name="content")
def content(
    digest: str,
    *,
    out: Annotated[Path | None, Parameter(name=["--out", "-o"])] = None,
    open_: Annotated[bool, Parameter(name=["--open"])] = False,
    media_type: Annotated[str | None, Parameter(name=["--media-type"])] = None,
    page: Annotated[int, Parameter(name=["--page"])] = 0,
    width: Annotated[int, Parameter(name=["--width"])] = 80,
) -> ExitCode:
    """Show, open or export the bytes a block names.

    Without a flag this draws what a terminal can draw: text and Markdown as text, an image or a PDF page as
    half-block graphics when `vitruvio[vision]` is installed. A drawing is bounded by the character cells it has —
    a page in a 60-column pane is 60x80 pixels, which shows you the layout and not the words.

    `--open` is the answer to that: the bytes are written to a temporary file and handed to whatever this desktop
    opens them with, at full resolution. `--out FILE` writes them somewhere you choose and keeps them.

    The digest is a *content* address -- the `blob` or `normalized_view.blob` of a block, not the block identity.
    `inspect blocks` prints both.

    Content is not evidence: it is the block's own datum, and the block is what other blocks cite.

    Parameters
    ----------
    digest
        The `sha256:...` content address.
    out
        Write the bytes here instead of drawing them. A directory is written into.
    open_
        Open the bytes in the desktop's own handler. Spelled `--open`.
    media_type
        How to interpret the bytes when drawing. Taken from the block by `browse`; here it defaults to text.
    page
        Which page of a PDF to draw, zero-based.
    width
        How many terminal cells wide to draw an image.
    """
    console = current().console
    service = current().service()

    if open_:
        from vitruvio.cli.render import desktop

        # The media type reaches the file name, not only the drawing: without a suffix the desktop picks a handler
        # by guesswork and lands on a text editor, which for a PDF means a screenful of binary.
        target = out if out is not None else desktop.scratch(None, digest, media_type)
        result = service.export_content(digest, target)
        try:
            ran = desktop.open_path(Path(result["path"]))
        except desktop.NoOpenerError as error:
            # The bytes are written either way, so this is a warning about the *opening* rather than a failure of
            # the command: over SSH with no display, the path is the whole useful answer.
            console.warn(str(error))
            return console.emit(
                "inspect.content",
                {**result, "opened": False},
                view=render.fields([("wrote", result["path"]), ("bytes", str(result["size"]))]),
            )
        return console.emit(
            "inspect.content",
            {**result, "opened": True},
            view=render.fields([("opened", result["path"]), ("with", ran), ("bytes", str(result["size"]))]),
        )

    if out is not None:
        result = service.export_content(digest, out)
        return console.emit(
            "inspect.content",
            result,
            view=render.fields([("wrote", result["path"]), ("bytes", str(result["size"]))]),
        )

    data = service.content(digest)
    kind = media_type or "text/plain"
    return console.emit(
        "inspect.content",
        {"digest": digest, "size": len(data), "media_type": kind},
        view=render.stack(
            render.fields([("content", render.digest(digest, full=True)), ("bytes", str(len(data))), ("as", kind)]),
            "",
            render.media.preview(
                data,
                kind,
                width=width,
                page=page,
                # Named only when something was cut: the footer is then the one place a reader learns the file
                # goes on, and the flag that fetches the rest belongs next to that fact.
                hint="pass --out FILE for all of it" if len(data) > render.media.MAX_TEXT_BYTES else None,
            ),
        ),
    )


@app.command(name="links")
def links(block_id: str, *, limit: Annotated[int, Parameter(name=["--limit"])] = 50) -> ExitCode:
    """Print the provenance records that name a block: where it came from, and what has been done to it.

    This is the brain's own link graph -- registration, derivation, normalization, supersession, demotion,
    removal -- not a similarity neighbourhood. A block with no records is normal in a selectively pulled brain,
    where provenance may not be installed.

    Parameters
    ----------
    block_id
        A `sha256:...` block identity.
    limit
        How many records to print.
    """
    console = current().console
    result = current().service().related(block_id, limit=limit)
    return console.emit("inspect.links", result, view=render.records(result))


@app.command(name="block")
def block(block_id: str) -> ExitCode:
    """Read one block by identity.

    The bytes are verified against the identity on the way out of the store, so a block that resolves is a block
    that hashes to the name it was filed under.

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
    return console.emit("inspect.block", result, view=render.stack(head, "", render.payload(result["payload"])))


@app.command(name="prove")
def prove(
    block_id: str,
    *,
    memory_type: Annotated[str, Parameter(name=["--memory-type"])],
) -> ExitCode:
    """Produce a Merkle inclusion proof for one block, already checked against the module's root.

    The proof is `O(log n)` sibling hashes, so membership in a version can be demonstrated without holding the
    rest of the module. It is returned already verified: leaving that to the caller would hand over the one
    thing the protocol does not leave to a caller.

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
            ("audit path", f"{len(result['audit_path'])} hashes"),
            ("verified", render.verdict(result["verified"], no="NO")),
        ]
    )
    return console.emit("inspect.prove", result, view=view)


SEVERITY_STYLES = {"ok": ("ok", "ok"), "warn": ("warn", "warn"), "fail": ("FAIL", "bad"), "skip": ("skip", "muted")}
"""How a doctor row's severity is drawn: the word in the first column, and the style it and a tripped detail take."""


@app.command(name="doctor")
def doctor(
    *,
    registry: Annotated[bool, Parameter(name=["--registry"], negative=())] = False,
    local: Annotated[
        Path | None,
        Parameter(
            name=["--local"],
            help="Probe a filesystem registry of OCI layouts rooted here instead of a remote one.",
        ),
    ] = None,
    anonymous: bool = False,
) -> ExitCode:
    """Check the environment and the brain: what is installed, what is configured, and what would disappoint you.

    Reports rather than fixes, and exits 0 whatever it finds -- a broken setup is what this exists to describe. Every
    row carries a stable `code`, a `severity` of ok, warn, fail or skip, and a `remedy` when there is something to do.
    Stale or unbuilt indices, a vector index built with another embedder, tombstoned or missing blocks, a partial
    install, an embedder this build cannot run, hashed features standing in for semantics: all of it offline. The
    registry is probed only with `--registry`, with one read-only request.

    Parameters
    ----------
    registry
        Also ask the configured registry for a manifest. Off by default, so doctor can run with no network.
    local
        Probe a filesystem registry of OCI layouts rooted here instead of a remote one.
    anonymous
        Probe without credentials.
    """
    console = current().console
    context = current()

    try:
        service = context.service(require_brain=False, require_layout=False)
    except VitruvioError as error:
        # The one thing the runtime cannot report about itself: a configuration it could not load. Said in the same
        # shape, so a caller still gets one envelope with one failing row rather than an error envelope from doctor.
        from vitruvio.runtime.ops.diagnosis import row

        result: dict[str, Any] = {
            "checks": [row("config.invalid", "fail", f"{error.code}: {error.message}", data={"code": error.code})],
            "failures": 1,
            "warnings": 0,
            "verdict": "fail",
            "registry_probed": False,
        }
    else:
        result = service.doctor(registry=registry, local=local, anonymous=anonymous)

    table = render.table("", "check", "detail", "remedy")
    for item in result["checks"]:
        word, style = SEVERITY_STYLES.get(str(item["severity"]), ("?", "warn"))
        table.add_row(
            Text(word, style=style),
            str(item["check"]),
            Text(str(item["detail"]), style="muted" if item["ok"] else style),
            Text(str(item.get("remedy") or ""), style="muted"),
        )
    return console.emit("inspect.doctor", result, view=table)


__all__ = ["app", "short"]
