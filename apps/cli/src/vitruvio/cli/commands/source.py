"""``vitruvio source`` -- canonical registration.

Registering a source does not declare it *true*. The canonical module asserts that evidence was incorporated
and preserved; every interpretation of it is a separate block that cites it through provenance. That
distinction is what makes a brain re-interpretable when the models improve, and it is why these three commands
are separate from ``vitruvio task``, which is where interpretation happens.

There is no in-place edit of evidence. A newer edition is a new block plus a supersession edge, which is what
``replace`` does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from vitruvio.cli.context import current
from vitruvio.cli.render import short
from vitruvio.ingest.media import EXTRA_MEDIA_TYPES, FALLBACK_MEDIA_TYPE, media_type_for
from vitruvio.kernel import ExitCode, VitruvioError

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
    lines = [
        f"block      {result['block_id']}",
        f"snapshot   {short(result['snapshot'])}",
    ]
    return console.emit("source.register", result, lines=lines)


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
    lines = [
        f"block      {result['block_id']}",
        f"supersedes {result['supersedes']}",
        f"snapshot   {short(result['snapshot'])}",
    ]
    return console.emit("source.replace", result, lines=lines)


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
        lines=[f"blob   {result['blob']}", f"size   {result['size']} bytes", f"type   {result['media_type']}"],
    )
