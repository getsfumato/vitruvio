"""Repo-wide test fixtures.

At the repository root rather than under ``tests/`` because ``testpaths`` spans ``tests``, ``packages`` and
``apps``, and every one of them needs the same isolation.

The autouse fixture here is not a convenience -- it is a correctness requirement. ``vitruvio brain use``
writes to ``$XDG_STATE_HOME``, ``registry login`` writes to ``$XDG_CONFIG_HOME``, and the embedders read
``HF_HOME``. A suite that did not redirect those would read and *overwrite* the developer's real state, and
would pass or fail depending on whose laptop it ran on. Every environment variable vitruvio consults is
either redirected into ``tmp_path`` or cleared.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Everything vitruvio reads from the environment. Listed exhaustively and asserted against the kernel's own
# tables below, so that adding a variable without adding it here is a test failure rather than a leak.
REDIRECTED = ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "HF_HOME", "SENTENCE_TRANSFORMERS_HOME")
CLEARED = (
    "VITRUVIO_BRAIN",
    "VITRUVIO_CONFIG",
    "VITRUVIO_PROJECT",
    "VITRUVIO_ACTOR_ID",
    "VITRUVIO_ACTOR_KIND",
    "VITRUVIO_REGISTRY_USERNAME",
    "VITRUVIO_REGISTRY_TOKEN",
    "DOCKER_USERNAME",
    "DOCKER_TOKEN",
    "DOCKER_PASSWORD",
    "VITRUVIO_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "VITRUVIO_VOYAGE_API_KEY",
    "VOYAGE_API_KEY",
    "VITRUVIO_COHERE_API_KEY",
    "COHERE_API_KEY",
    "VITRUVIO_ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY",
    "VITRUVIO_HF_TOKEN",
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
)


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Redirect every path vitruvio writes to, and clear every credential it reads.

    Args:
        tmp_path (Path): pytest's per-test directory.
        monkeypatch (pytest.MonkeyPatch): The patcher, so changes unwind after each test.

    Returns:
        Path: The isolated home, for tests that want to inspect what was written.
    """
    home = tmp_path / "home"
    for variable in REDIRECTED:
        target = home / variable.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(variable, str(target))
    for variable in CLEARED:
        monkeypatch.delenv(variable, raising=False)
    # A vitruvio.toml anywhere above the repository would be found by the walk-up and change what the
    # discovery tests see, so every test starts from a directory with nothing above it.
    monkeypatch.chdir(tmp_path)
    return home


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A directory holding a minimal brain layout and nothing else."""
    brain = tmp_path / "brain"
    brain.mkdir()
    (brain / "oci-layout").write_text('{"imageLayoutVersion": "1.0.0"}')
    return tmp_path
