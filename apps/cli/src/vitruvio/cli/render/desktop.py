"""Handing bytes to whatever the desktop opens them with.

A terminal can draw a thumbnail of a page. It cannot show you the page. So the answer to "I need to actually
read this PDF" is not a better renderer -- it is the operating system's own handler, which already knows how to
open a PDF at full resolution, a spreadsheet in a spreadsheet program and a video in a player.

**The platform handler, not a web browser.** This started as ``webbrowser.open(uri)``, which is wrong in a way
that is easy to miss: on macOS it opens a ``file://`` URI in Chrome or Safari. For a PDF that is merely not what
anyone meant; for a ``.mp4`` or an ``.xlsx`` the browser is the wrong application entirely, and for a media type
it cannot handle it offers to download a file the user already has. ``open``, ``xdg-open`` and ``os.startfile``
consult the *registered* handler, which is the thing the user configured.

**The file is named after its origin.** A content-addressed store has no filenames in it, so the bytes have to be
written somewhere before anything can open them -- and a viewer whose title bar says ``content.pdf`` has thrown
away the one piece of context the reader needed. The registration record's origin supplies the name.

**And it must end in an extension, whatever else it is called.** That is not cosmetic: a handler is chosen *by
suffix*, so a PDF written to a file called ``sha256-b0a1...`` opens in a text editor showing ``%PDF-1.7`` and a
screenful of binary. Which is exactly what happened, on a brain whose blocks carry no origin at all -- a pulled
one, where nobody recorded the filenames -- and it is why :func:`extension_for` exists. The block always knows its
media type; that is part of its identity. Falling back to the content address must not throw it away.
"""

from __future__ import annotations

import mimetypes
import shutil
import subprocess
import sys
from pathlib import Path

EXTENSIONS = {
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "text/yaml": ".yaml",
    "application/x-yaml": ".yaml",
    "application/toml": ".toml",
    "text/toml": ".toml",
    "application/jsonl": ".jsonl",
    "application/x-ndjson": ".jsonl",
}
"""Suffixes :mod:`mimetypes` does not know, for types vitruvio handles anyway.

Short on purpose: the standard table answers ``application/pdf``, ``image/png``, ``video/mp4`` and the rest
correctly, and duplicating it here would be a second table to keep in sync. What is listed is what it answers
``None`` for -- Markdown above all, which is most of what a normalized view is.
"""

OPENERS = {
    "darwin": ("open",),
    "linux": ("xdg-open", "gio", "gnome-open", "kde-open"),
    "freebsd": ("xdg-open",),
}
"""Candidate openers per platform, in preference order.

Several for Linux because there is no single answer there: ``xdg-open`` is the convention and is absent on a
minimal system, and the desktop-specific ones are what remain. Windows is not here -- it uses ``os.startfile``,
which is a function rather than a program.
"""


class NoOpenerError(RuntimeError):
    """Raised when the platform offers no way to hand a file to another application."""


def opener() -> tuple[str, ...] | None:
    """
    The command this machine opens files with.

    Returns:
        tuple[str, ...] | None: The argv prefix, or ``None`` on Windows, where the mechanism is a function call
        rather than a command. Absent entirely -- a bare container, a stripped Linux host -- also returns
        ``None``, and :func:`open_path` reports that rather than pretending it worked.
    """
    if sys.platform == "win32":
        return None
    for candidate in OPENERS.get(sys.platform, ("xdg-open",)):
        if found := shutil.which(candidate):
            return (found,)
    return None


def open_path(path: Path) -> str:
    """
    Ask the desktop to open a file.

    Args:
        path (Path): The file. It has to exist: every handler resolves it by path, and a store addressed by
            content has no path to give, so the caller writes the bytes out first.

    Returns:
        str: What was run, for a message that says what happened rather than only that something did.

    Raises:
        NoOpenerError: If this platform has no opener, or the opener refused. Both are worth reporting: over SSH with
            no display there is nothing to open into, and silence would look like a viewer that failed to appear.
    """
    if sys.platform == "win32":
        import os

        os.startfile(str(path))  # type: ignore[attr-defined]
        return f"startfile {path}"

    command = opener()
    if command is None:
        raise NoOpenerError(
            "this machine has no way to open a file in another application"
            + ("" if sys.platform != "linux" else " -- install xdg-utils")
        )

    # Not `check=True`: a handler that starts and then exits non-zero is a handler that ran, and the file is on
    # disk either way. What is worth reporting is failing to *launch*, which is the exception below.
    try:
        # The argv form with no shell, over a path this process just wrote itself.
        subprocess.Popen(
            [*command, str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise NoOpenerError(f"{command[0]} could not be started: {error}") from error
    return f"{Path(command[0]).name} {path}"


def extension_for(media_type: str | None) -> str:
    """
    The file suffix a media type should be written with.

    Args:
        media_type (str | None): The block's media type. Parameters are ignored, so
            ``text/plain; charset=utf-8`` answers the same as ``text/plain``.

    Returns:
        str: A suffix including its dot, or ``""`` when nothing sensible is known -- ``application/octet-stream``
        being the honest case: bytes nobody described should not be dressed up as a format.
    """
    if not media_type:
        return ""
    bare = media_type.split(";")[0].strip().lower()
    if bare in EXTENSIONS:
        return EXTENSIONS[bare]
    return mimetypes.guess_extension(bare) or ""


def filename(name: str | None, digest: str, media_type: str | None = None) -> str:
    """
    What to call bytes taken out of a content-addressed store.

    The origin when the block records one, the content address when it does not -- and in both cases a suffix, if
    the name does not already carry one. A desktop handler is chosen by suffix, so an extensionless file is one no
    application can open correctly no matter what is in it.

    Args:
        name (str | None): The origin recorded when the block was registered, when there is one.
        digest (str): The content address, as a fallback name.
        media_type (str | None): The block's media type, used only to supply a missing suffix.

    Returns:
        str: A file name.
    """
    stem = Path(name).name if name else digest.replace(":", "-")
    if Path(stem).suffix:
        return stem
    return stem + extension_for(media_type)


def scratch(name: str | None, digest: str, media_type: str | None = None) -> Path:
    """
    Where to write bytes that are about to be handed to another application.

    Args:
        name (str | None): The origin recorded when the block was registered, when there is one.
        digest (str): The content address, as a fallback name and as the directory's suffix.
        media_type (str | None): The block's media type, which supplies the suffix when the name has none.

    Returns:
        Path: A path inside a fresh temporary directory. Fresh per call rather than one shared directory,
        because two blocks can legitimately share an origin filename -- a brain holding two editions of the same
        paper is the ordinary case, not the odd one -- and the second write would replace what the first opened.
    """
    import tempfile

    return Path(tempfile.mkdtemp(prefix="vitruvio-")) / filename(name, digest, media_type)


__all__ = ["EXTENSIONS", "OPENERS", "NoOpenerError", "extension_for", "filename", "open_path", "opener", "scratch"]
