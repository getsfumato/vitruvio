"""Is there a newer vitruvio, and how would it be installed.

Asked of the **GitHub releases API**, because that is where ``install.sh`` gets its answer, and the two must
agree: a check against PyPI would report a version the installer cannot fetch, since a release publishes a
wheel bundle as an asset and the distributions are not on PyPI under these names. One source of truth for
"what is the latest", used by the thing that tells you and the thing that installs it.

Three properties this owes an ordinary run, in the order they matter.

**It must not slow anything down.** The answer is cached under :func:`~vitruvio.kernel.paths.cache_home` with
a TTL, so at most one run a day pays a request and the rest read a small file. The request itself is bounded
by a short timeout, because a hung DNS lookup would otherwise hang a command that had already finished its
work.

**It must not fail anything.** Every failure -- offline, rate-limited, a proxy returning HTML, a malformed
tag -- resolves to "no newer version known". A tool that refused to run because it could not check for
updates would be worse than one that never checked.

**It must be refusable.** ``VITRUVIO_NO_UPDATE_CHECK=1`` turns it off entirely, and nothing here writes to
the network when it is set. A machine in CI, or on a metered link, or behind a policy that forbids calling
GitHub, is a machine that should be able to say so once.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vitruvio.kernel.version import __version__

RELEASES_API = "https://api.github.com/repos/getsfumato/vitruvio/releases/latest"
"""Where the latest release is resolved from. The URL ``install.sh`` reads, deliberately.

``releases/latest`` never resolves to a draft or a prerelease, which is what keeps an unfinished release from
being offered to everybody the moment it is cut.
"""

INSTALLER_URL = "https://vitruvio.sh/install.sh"
"""The documented installer, which is what an update re-runs rather than reimplementing."""

OPT_OUT = "VITRUVIO_NO_UPDATE_CHECK"
"""Set to a truthy value to disable the check. Honoured before anything is read or fetched."""

CHECK_TTL = 24 * 60 * 60
"""Seconds between checks. A day: releases are not more frequent than that, and neither is anyone's patience."""

TIMEOUT = 2.0
"""Seconds to wait on the request. Short on purpose -- this runs after a command has already done its work,
and a slow answer to a question nobody asked is indistinguishable from a hang."""

CACHE_FILE = "update-check.json"
"""Under ``cache_home()``. A cache rather than state: deleting it costs one request."""


@dataclass(frozen=True)
class Update:
    """
    What is installed, what is published, and whether those differ.

    Attributes:
        current (str): The running version.
        latest (str | None): The newest release, or ``None`` when it could not be resolved -- offline, opted
            out, or the answer was unusable. Not an error, and not the same as "you are up to date".
        available (bool): Whether ``latest`` is newer than ``current``. False whenever ``latest`` is ``None``,
            so a caller that only branches on this cannot mistake "could not tell" for "nothing new".
        checked (bool): Whether this answer came from asking rather than from the cache. For a caller that
            wants to report *when* it last looked.
    """

    current: str
    latest: str | None = None
    available: bool = False
    checked: bool = False

    @property
    def known(self) -> bool:
        """Whether a latest version was resolved at all."""
        return self.latest is not None


def opted_out() -> bool:
    """
    Whether this machine has asked not to be checked.

    Returns:
        bool: True when :data:`OPT_OUT` is set to anything other than an explicit falsehood.
    """
    value = os.environ.get(OPT_OUT, "").strip().lower()
    return bool(value) and value not in {"0", "false", "no"}


def is_newer(candidate: str, than: str) -> bool:
    """
    Whether one version supersedes another, under PEP 440 ordering.

    The case that makes this worth a real implementation rather than a tuple comparison is the prerelease: a
    machine on ``0.6.0`` must not be told to "update" to ``0.6.0b1``, and both spellings of that -- ``0.6.0b1``
    and ``0.6.0-b.1`` -- have to sort below the release. A hand-rolled comparator got the first one wrong by
    reading ``0b1`` as ``0``, which is exactly the kind of near-miss that makes a second implementation of
    somebody else's spec a bad trade.

    Args:
        candidate (str): The version that might be newer.
        than (str): The version in hand.

    Returns:
        bool: Whether ``candidate`` supersedes ``than``. False for anything unparseable, because a tag nobody
        can order is not a tag anybody should be offered.
    """
    from packaging.version import InvalidVersion, Version

    try:
        return Version(candidate) > Version(than)
    except InvalidVersion:
        return False


def normalize_version(candidate: str) -> str | None:
    """Return a canonical PEP 440 version, or ``None`` when the input is not a version."""
    from packaging.version import InvalidVersion, Version

    try:
        return str(Version(candidate))
    except InvalidVersion:
        return None


def _resolved(latest: object, *, checked: bool = False) -> Update:
    """
    An :class:`Update` from whatever a cache or a response yielded.

    Built in one place because all three callers want the same thing and the interesting half is the guard:
    ``available`` must be False whenever ``latest`` is absent or unusable, so that "could not tell" can never
    be read as "there is something new".

    Args:
        latest (object): The version, if one was resolved. Anything else is treated as absent.
        checked (bool): Whether this came from asking rather than from the cache.

    Returns:
        Update: The answer.
    """
    version = latest if isinstance(latest, str) and latest else None
    return Update(
        current=__version__,
        latest=version,
        available=version is not None and is_newer(version, __version__),
        checked=checked,
    )


def _cache_path() -> Path:
    """Where the last answer is kept."""
    from vitruvio.kernel.paths import cache_home

    return cache_home() / CACHE_FILE


def _read_cache() -> dict[str, Any] | None:
    """
    The last answer, if it is still fresh.

    Returns:
        dict[str, Any] | None: The cached record, or ``None`` when it is absent, stale or unreadable. An
        unreadable cache is a cache miss rather than a failure: the file is ours, it costs one request, and
        refusing to run because a cache file was corrupt would be absurd.
    """
    try:
        record = json.loads(_cache_path().read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            return None
        if time.time() - float(record.get("checked_at", 0)) > CHECK_TTL:
            return None
        return record
    except (OSError, ValueError):
        return None


def _write_cache(latest: str | None) -> None:
    """
    Remember the answer, including that there was not one.

    A failed lookup is cached too, and deliberately: otherwise a machine that is offline pays the timeout on
    every single command until it comes back.

    Args:
        latest (str | None): What was resolved, if anything.
    """
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"checked_at": time.time(), "latest": latest}), encoding="utf-8")
    except OSError:
        # A cache that cannot be written is a check that runs more often than it should. Not worth a word.
        pass


def fetch_latest(timeout: float = TIMEOUT) -> str | None:
    """
    Ask GitHub for the newest release, ignoring every way that can go wrong.

    Args:
        timeout (float): Seconds to wait.

    Returns:
        str | None: The version, without its leading ``v``, or ``None`` when it could not be resolved.
    """
    import urllib.request

    request = urllib.request.Request(
        RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"vitruvio/{__version__}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        # Deliberately everything: offline, DNS, TLS, a 403 from rate limiting, a proxy returning HTML, a
        # body that is not JSON. None of them is a reason for the command the user ran to behave differently.
        return None
    tag = payload.get("tag_name") if isinstance(payload, dict) else None
    if not isinstance(tag, str) or not tag.strip():
        return None
    return tag.strip().lstrip("v") or None


def check(*, force: bool = False, timeout: float = TIMEOUT) -> Update:
    """
    Whether a newer vitruvio is published.

    Args:
        force (bool): Ask even when the cached answer is fresh, and even when the check is opted out of. For
            ``vitruvio update``, where the user asked the question directly -- the opt-out silences the
            *ambient* check, and overriding an explicit command would be reading it as a prohibition.
        timeout (float): Seconds to wait on the request.

    Returns:
        Update: What is installed, what is published, and whether those differ.
    """
    if opted_out() and not force:
        return Update(current=__version__)

    if not force:
        cached = _read_cache()
        if cached is not None:
            return _resolved(cached.get("latest"))

    latest = fetch_latest(timeout)
    _write_cache(latest)
    return _resolved(latest, checked=True)


def cached_update() -> Update:
    """
    What the last check found, asking nothing.

    The ambient notice reads through this so that printing it costs a file read and never a request, no
    matter how the TTL happens to fall.

    Returns:
        Update: The cached answer, or an empty one when there is none.
    """
    if opted_out():
        return Update(current=__version__)
    cached = _read_cache()
    if cached is None:
        return Update(current=__version__)
    return _resolved(cached.get("latest"))


def is_due() -> bool:
    """
    Whether the cached answer has expired and a request would be made.

    Returns:
        bool: True when there is no fresh cache entry.
    """
    return not opted_out() and _read_cache() is None


def installed_from_source() -> bool:
    """
    Whether this vitruvio is a checkout rather than an installed release.

    An update re-runs the installer, which replaces the environment the command is served from. Doing that to
    somebody's working copy would delete the thing they were editing, so it is refused -- and refused on the
    honest signal: an installed distribution lives under ``site-packages``, a checkout and an editable install
    do not.

    Returns:
        bool: Whether the running code is a source checkout or an editable install.
    """
    here = Path(__file__).resolve()
    return not any(part in {"site-packages", "dist-packages"} for part in here.parts)


__all__ = [
    "CHECK_TTL",
    "INSTALLER_URL",
    "OPT_OUT",
    "RELEASES_API",
    "Update",
    "cached_update",
    "check",
    "fetch_latest",
    "installed_from_source",
    "is_due",
    "is_newer",
    "opted_out",
]
