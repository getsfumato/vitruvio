"""``vitruvio update`` -- install the newest release.

**It re-runs the documented installer rather than reimplementing it.** vitruvio is nine pure-Python
distributions installed by ``install.sh``, which resolves a release, downloads that release's wheel bundle,
bootstraps ``uv`` if the host has none, and hands the bundle to ``uv tool install --force --reinstall``. The
``--reinstall`` in there is load-bearing for exactly this case: only ``vitruvio`` is pinned, so an upgrade that
did not force it would find the eight sibling libraries "already satisfied" and leave a new CLI sitting on old
packages -- which then reports the old version, because the version is read from the kernel.

Reproducing that in Python would be a second installer, and the two would drift in precisely the way that
produces a half-upgraded environment nobody can diagnose. So this resolves the version, asks, and shells out
to the same script the website serves.

**A source checkout is refused.** The installer replaces the environment the running command is served from.
Aimed at a working copy that is somebody's editor buffer, so it is refused on the honest signal rather than
guarded by a flag: an installed distribution lives under ``site-packages`` and a checkout does not.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Annotated, Any

from cyclopts import App, Parameter
from rich.text import Text

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.kernel import ExitCode, UsageError, VitruvioError, updates

app = App(
    name="update",
    help="Check for a newer vitruvio, and install it.",
    result_action="return_value",
    exit_on_error=False,
    # The root app owns `vitruvio --version`; inside this command the same spelling names the release to install.
    # Without clearing the inherited eager flag, Cyclopts prints the running version and never dispatches here.
    version_flags=[],
)


def _installer_command() -> list[str]:
    """
    The shell command that installs one version, as the website documents it.

    Returns:
        list[str]: The argv to run.

    Raises:
        VitruvioError: If neither curl nor wget is available to fetch the installer.
    """
    if shutil.which("curl"):
        fetch = f"curl -fsSL {updates.INSTALLER_URL}"
    elif shutil.which("wget"):
        fetch = f"wget -qO- {updates.INSTALLER_URL}"
    else:
        raise VitruvioError(
            "neither curl nor wget is available, so the installer cannot be fetched",
            hint=f"install one, or run it yourself: {updates.INSTALLER_URL}",
        )
    return ["sh", "-c", f"{fetch} | sh"]


def _report(update: updates.Update, *, extra: list[tuple[str, Any]] | None = None) -> Any:
    """The two-line summary every path here prints."""
    pairs: list[tuple[str, Any]] = [
        ("installed", render.count(update.current)),
        (
            "latest",
            Text(update.latest, style="ok" if update.available else "muted")
            if update.latest
            else Text("unknown -- could not reach GitHub", style="warn"),
        ),
    ]
    pairs.extend(extra or [])
    return render.fields(pairs)


@app.default
def update(
    *,
    check: Annotated[bool, Parameter(name=["--check"])] = False,
    yes: Annotated[bool, Parameter(name=["--yes", "-y"])] = False,
    version: str | None = None,
) -> ExitCode:
    """Check for a newer vitruvio, and install it.

    Asks GitHub directly rather than reading the cached answer the post-command notice uses, because you asked:
    a check that reported yesterday's result to somebody who typed `update` would be answering a different
    question. That also means `VITRUVIO_NO_UPDATE_CHECK` does not silence this — it silences the *ambient*
    check, and reading it as a prohibition on an explicit command would be reading it wrong.

    Installing re-runs the official installer, pinned to the version you were shown. It replaces the
    environment this command is served from, which is why it asks first.

    Parameters
    ----------
    check
        Report what is available and install nothing. Exits 0 whether or not there is an update; read
        `available` in `--json`.
    yes
        Install without asking. For a script that has already decided.
    version
        Install this version instead of the latest. Downgrades are allowed and are not warned about twice —
        naming a version is already deliberate.
    """
    console = current().console
    found = updates.check(force=True)

    if version is None and not found.known:
        # Nothing resolved and nothing named: there is no version to install, and guessing at one is how a
        # tool reinstalls the release it is already running.
        return console.emit(
            "update",
            {**vars(found), "installed": False, "reason": "could not resolve the latest release"},
            view=_report(found),
        )

    if version is not None:
        target = updates.normalize_version(version)
        if target is None:
            raise UsageError(
                f"{version!r} is not a valid version",
                hint="pass a PEP 440 version such as 1.2.3, 1.2.3rc1 or v1.2.3",
            )
    else:
        target = found.latest
    assert target is not None

    if check:
        return console.emit("update", {**vars(found), "installed": False, "reason": "check only"}, view=_report(found))

    if version is None and not found.available:
        return console.emit(
            "update",
            {**vars(found), "installed": False, "reason": "already current"},
            view=render.stack(_report(found), "", render.empty("already on the newest release")),
        )

    if updates.installed_from_source():
        raise UsageError(
            "this vitruvio is a source checkout, not an installed release, so there is nothing to update",
            hint="the installer would replace the environment this is served from; use git in the checkout instead",
        )

    argv = _installer_command()
    console.note(f"installing {target} with the official installer")
    if not yes:
        if not sys.stdin.isatty():
            raise UsageError(
                "updating replaces the installed vitruvio and needs confirmation, and stdin is not a terminal",
                hint="pass --yes when a script has already decided",
            )
        answer = input(f"replace vitruvio {found.current} with {target}? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            return console.emit(
                "update",
                {**vars(found), "installed": False, "reason": "declined"},
                view=render.empty("nothing was changed"),
            )

    # Inherited streams, not captured: the installer reports its own progress on stderr, and swallowing it
    # would turn a multi-second download into a silent pause. `check=False` because its exit code is the
    # answer rather than an exception -- a failed install has a message worth reporting as itself.
    # The version is data, never shell source. Keeping it in the environment prevents an explicit `--version`
    # from turning shell metacharacters into a second command while still pinning exactly what the user approved.
    completed = subprocess.run(argv, env={**os.environ, "VITRUVIO_VERSION": target}, check=False)
    if completed.returncode != 0:
        raise VitruvioError(
            f"the installer exited {completed.returncode}; vitruvio was not updated",
            hint=f"run it yourself to see why: {argv[-1]}",
        )

    return console.emit(
        "update",
        {**vars(found), "installed": True, "target": target, "reason": "installed"},
        view=render.fields(
            [
                ("installed", Text(target, style="ok")),
                ("was", render.count(found.current)),
                ("verify", "vitruvio --version"),
            ]
        ),
    )


__all__ = ["app"]
