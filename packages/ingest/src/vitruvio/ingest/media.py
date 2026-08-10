"""What media type to record for a file, and the extensions ``mimetypes`` does not know.

Lives here rather than in the CLI, where it started, because a :class:`~vitruvio.ingest.sources.Source` needs it and
``ingest`` sits below ``apps/cli`` in the layering contract -- importing uphill is what ``lint-imports`` exists to
refuse. The CLI re-exports both names, so nothing that referenced them from there breaks.

Not cosmetic, and worth saying once in the place that owns it: the media type is recorded in the canonical block, it
travels with the published artifact, and it is what a normalization pipeline dispatches on. A Markdown file filed as
``application/octet-stream`` is a file no text pipeline will offer to normalise, and fixing it later means a new
block, because the media type is part of a block's identity.
"""

from __future__ import annotations

import mimetypes
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

FALLBACK_MEDIA_TYPE = "application/octet-stream"
"""What an unguessable file is. Honest, and it still round-trips: the bytes are what matter."""

EXTRA_MEDIA_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".rst": "text/x-rst",
    ".org": "text/org",
    ".tex": "text/x-tex",
    ".jsonl": "application/x-ndjson",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".toml": "application/toml",
    ".webp": "image/webp",
    ".avif": "image/avif",
}
"""Extensions ``mimetypes`` does not know but a knowledge brain meets constantly."""


def media_type_for(path: Path, declared: str | None = None) -> str:
    """
    The media type to record, guessing only when one was not declared.

    A guess is recorded in the block and travels with it, so it is worth being explicit about where it came from:
    ``mimetypes`` reads the extension and nothing else, and an extension is a claim rather than evidence. Declaring
    one is always better -- from the command line, or in a source's ``media_type``.

    Args:
        path (Path): The file.
        declared (str | None): What the caller said, if anything.

    Returns:
        str: The media type.
    """
    if declared:
        return declared
    if extra := EXTRA_MEDIA_TYPES.get(path.suffix.lower()):
        return extra
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or FALLBACK_MEDIA_TYPE
