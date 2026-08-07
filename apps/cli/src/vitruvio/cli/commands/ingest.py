"""``vitruvio ingest`` -- the whole path in one command, for when a proposer runs in-process.

`ingest run` is register → define → propose → validate → commit. It exists for the case `vitruvio task` does not
serve: a proposer that vitruvio can call itself, so there is no round trip through a file for a model to fill in.

Prefer `--dry-run` the first time against any new kind of document. It proposes and validates and commits nothing,
which is the cheap way to find out that a media type was guessed wrong or that a model is emitting floats.

The default proposer is `structure`, which uses no model at all: it reads Markdown headings and proposes one
semantic block per section, extractively. That is a real answer for well-structured documents and it is also the
honest baseline — every claim it makes is text that was in the source, so it cannot invent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from vitruvio.cli.context import current
from vitruvio.cli.render import short
from vitruvio.kernel import ExitCode

app = App(
    name="ingest",
    help="Run a source through registration, proposal, validation and commit.",
    result_action="return_value",
    exit_on_error=False,
)


@app.command(name="run")
def run(
    path: Path,
    *,
    media_type: Annotated[str | None, Parameter(name=["--media-type", "-m"])] = None,
    proposer: Annotated[str, Parameter(name=["--proposer", "-p"])] = "structure",
    allowed: Annotated[list[str] | None, Parameter(name=["--allowed", "-a"], negative=())] = None,
    normalize_with: Annotated[str | None, Parameter(name=["--normalize-with"])] = None,
    subject: str | None = None,
    origin: str | None = None,
    dry_run: Annotated[bool, Parameter(name=["--dry-run"])] = False,
) -> ExitCode:
    """Register a source, propose knowledge from it, validate and commit.

    Exit 7 means the proposal was rejected and nothing was stored — re-run with `--dry-run` to read each code.

    Parameters
    ----------
    path
        The file to ingest.
    media_type
        What the bytes are. Guessed from the extension when omitted, and the guess is recorded in the block, so
        declaring it is always better. It also decides which pipeline applies.
    proposer
        `structure` (deterministic, no model), `anthropic`, or `openai`. A model may follow a colon, as in
        `anthropic:claude-opus-5`.
    allowed
        Which memory types may be proposed. Repeatable. Defaults to episodic, semantic and procedural.
    normalize_with
        A normalization pipeline. Defaults to whichever one suits the media type; pass a name to override, or
        `""` to register the original with no view.
    subject
        A subject to tag every proposal with. Worth setting: it is what makes a later `--subject` filter select
        this document rather than everything.
    origin
        Where the source came from. Defaults to the path.
    dry_run
        Propose and validate, commit nothing. Run this first against any new kind of document.
    """
    from vitruvio.cli.commands.source import media_type_for

    console = current().console
    resolved = media_type_for(path, media_type)
    result = (
        current()
        .service()
        .ingest_run(
            path,
            media_type=resolved,
            proposer=proposer,
            allowed=allowed,
            normalize_with=normalize_with or None,
            subject=subject,
            origin=origin,
            dry_run=dry_run,
        )
    )

    validation = result["validation"]
    lines = [
        f"source      {short(result['registration']['block_id'])}  ({resolved})",
        f"pipeline    {result['pipeline'] or '(none -- no view applies)'}",
        f"proposer    {result['proposer']}",
        f"proposed    {result['proposed']} candidates",
        f"committable {validation['committable']}",
        f"already held {result['already_held']}",
    ]
    if result["committed"] is not None:
        lines += [
            f"committed   {len(result['committed']['committed'])} blocks",
            f"snapshot    {short(result['committed']['snapshot'])}",
        ]
    else:
        lines.append("committed   nothing (dry run)")

    for entry in validation["results"]:
        if entry["status"] != "validated":
            for issue in entry.get("issues", []):
                console.warn(f"{entry['status']}: {issue['code']}: {issue['detail']}")
    if result["proposed"] == 0:
        console.warn(
            "the proposer found nothing to propose. For `structure` that means the document has no Markdown "
            "headings with content under them; a model proposer would read it differently"
        )
    return console.emit("ingest.run", result, lines=lines)


@app.command(name="pipelines")
def pipelines() -> ExitCode:
    """List the normalization pipelines this build can run.

    A pipeline turns observed bytes into a deterministic view that is itself citable evidence, so the same input
    and the same pipeline version must produce the same bytes on every machine. That requirement is why there is no
    pipeline for raster images: a re-encode is not reproducible across library versions, and vision embeddings read
    the original blob instead.

    `pdf-text` is listed as unavailable rather than hidden when the `[vision]` extra is not installed.
    """
    console = current().console
    result = current().service().pipelines()
    lines = [
        f"{item['name']:<16} {item['version']:<20} {'ok ' if item['available'] else '---'}  "
        f"{', '.join(str(entry) for entry in item['accepts'])}"
        for item in result["pipelines"]
    ]
    missing = [item["name"] for item in result["pipelines"] if not item["available"]]
    if missing:
        console.warn(f"unavailable here: {', '.join(missing)} -- install the [vision] extra")
    return console.emit("ingest.pipelines", result, lines=lines)
