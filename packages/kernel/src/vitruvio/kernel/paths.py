"""Where vitruvio is allowed to write.

Two rules hold everywhere below, and the rest of the module follows from them.

**A brain directory belongs to the SDK.** On disk a brain *is* an OCI image layout: ``oci-layout``,
``index.json``, ``blobs/sha256/``, plus the SDK's own sidecar ``boltzmann/`` (``head.json``,
``tombstones.json``). Vitruvio adds exactly one sibling directory, :data:`DERIVED_DIR`, and never writes
inside ``blobs/`` or ``boltzmann/``. That is what keeps a brain movable with ``oras cp`` and readable by
any other conforming client.

**Everything vitruvio derives is disposable.** Indices, statistics, the embedding cache and the cost
calibration all live under :data:`DERIVED_DIR`. Deleting it costs time -- a rebuild -- and never knowledge.
The protocol says indices are derived views over blocks; this is that claim made physical.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "vitruvio"
"""The directory name used under every XDG root."""

DERIVED_DIR = ".vitruvio"
"""The per-brain directory holding derived, rebuildable state.

A sibling of the SDK's ``boltzmann/`` rather than a child of it. The SDK ignores directories it does not
know, so this is safe, but writing into another component's sidecar is the kind of thing that works until
the day it does not.
"""

CONFIG_FILE = "vitruvio.toml"
"""The per-project configuration file, committed alongside the brain it describes."""

STATE_FILE = "state.toml"
"""Where ``brain use`` records the interactive default."""

CREDENTIALS_FILE = "credentials.json"
"""The fallback credential store, used only when no system keyring is available."""

PLUGIN_DIR = "sources"
"""Where a hand-written source plugin lives, under the user-level configuration directory."""


def _xdg(variable: str, default: Path) -> Path:
    """
    Resolve an XDG base directory.

    An empty or relative value is ignored rather than honoured: the specification says these variables
    hold absolute paths, and a relative one would put vitruvio's state wherever the process happened to
    be started from.

    Args:
        variable (str): The environment variable to read.
        default (Path): Where to fall back to.

    Returns:
        Path: The resolved base directory.
    """
    value = os.environ.get(variable, "").strip()
    if value:
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            return candidate
    return default


def config_home() -> Path:
    """The user-level configuration directory."""
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config") / APP_NAME


def state_home() -> Path:
    """The directory holding state that survives a reboot but is not configuration."""
    return _xdg("XDG_STATE_HOME", Path.home() / ".local" / "state") / APP_NAME


def cache_home() -> Path:
    """The directory holding regenerable caches, including downloaded model weights."""
    return _xdg("XDG_CACHE_HOME", Path.home() / ".cache") / APP_NAME


def state_file() -> Path:
    """Where the current-brain pointer and per-host registry facts are recorded."""
    return state_home() / STATE_FILE


def credentials_file() -> Path:
    """Where registry credentials land when no keyring is available."""
    return config_home() / CREDENTIALS_FILE


def plugin_dir() -> Path:
    """Where a source plugin you wrote yourself lives.

    Under ``config_home()`` and deliberately **not** under a brain or the project: importing a module from here is
    code execution, and the only trust level at which that is acceptable is "code I wrote, on my machine" -- the same
    one as a shell profile. A plugin directory inside the repository would mean that cloning a repository and running
    ``vitruvio source pull`` executes a stranger's Python, which is the whole thing this layout refuses.
    """
    return config_home() / PLUGIN_DIR


def model_cache() -> Path:
    """Where embedding model weights are cached.

    Exported into the environment by :func:`prepare_model_cache` before any embedder is imported, so that
    two machines with the same configuration resolve the same weights and ``vitruvio inspect doctor`` can
    report what the cache costs.
    """
    return cache_home() / "models"


def prepare_model_cache() -> Path:
    """
    Point the model libraries at vitruvio's cache, and return it.

    Must run before ``sentence_transformers`` or ``huggingface_hub`` is imported: both read these variables
    once, at import time. An existing value is left alone -- a user who has already pointed ``HF_HOME``
    somewhere deliberate should not have it moved.

    Returns:
        Path: The cache directory, created if absent.
    """
    target = model_cache()
    target.mkdir(parents=True, exist_ok=True)
    for variable in ("HF_HOME", "SENTENCE_TRANSFORMERS_HOME"):
        os.environ.setdefault(variable, str(target))
    return target


def derived_dir(brain: Path) -> Path:
    """
    The derived-state directory for one brain.

    Args:
        brain (Path): The brain's layout directory.

    Returns:
        Path: ``<brain>/.vitruvio``, not created.
    """
    return brain / DERIVED_DIR


def is_layout(path: Path) -> bool:
    """
    Whether a directory looks like an OCI image layout, and therefore like a brain.

    Checks for the layout marker rather than for the whole structure. A directory with ``oci-layout`` and
    nothing else is an empty brain, which is a legitimate state right after ``brain init``; a directory
    without it is something else entirely and must not be written into.

    Args:
        path (Path): The directory to test.

    Returns:
        bool: Whether the marker is present.
    """
    return (path / "oci-layout").is_file()
