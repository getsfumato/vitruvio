"""The nine distributions must share one import namespace.

This is the cheapest test in the repository and it guards the most confusing failure mode. Nine wheels
install into one PEP 420 namespace package, which works only if *no* member ships a
``src/vitruvio/__init__.py`` and every member's hatchling config uses ``only-include`` plus
``sources = ["src"]``. Get it wrong and the symptom is not an obvious error: one member wins, the others
become importable under the wrong name, and ``import vitruvio.planner`` fails while ``import planner``
quietly succeeds.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

MEMBERS = (
    "vitruvio.kernel",
    "vitruvio.stats",
    "vitruvio.embeddings",
    "vitruvio.indices",
    "vitruvio.planner",
    "vitruvio.ingest",
    "vitruvio.runtime",
    "vitruvio.bench",
    "vitruvio.cli",
)

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name", MEMBERS)
def test_every_member_imports_under_the_shared_namespace(name: str) -> None:
    """Each member is reachable as ``vitruvio.<member>``, with a docstring saying what it is for."""
    module = importlib.import_module(name)
    assert module.__doc__, f"{name} has no module docstring"


def test_no_member_ships_a_namespace_init() -> None:
    """A ``src/vitruvio/__init__.py`` anywhere would shadow the other eight members."""
    offenders = [path.relative_to(REPO) for path in REPO.glob("*/*/src/vitruvio/__init__.py")]
    assert not offenders, f"PEP 420 namespace packages must have no __init__.py: {offenders}"


def test_the_namespace_spans_every_member() -> None:
    """``vitruvio.__path__`` carries one entry per installed member.

    A count rather than a set of names, and it is load-bearing in a way worth recording: a tenth portion appears the
    moment a member's package data cannot be expressed as a path pointer, because hatchling's editable build then
    copies that data into ``site-packages/vitruvio/``. Which is how the CLI's ``skills/`` came to be a symlink into
    the repository root rather than a ``force-include`` from it -- this test is what noticed.
    """
    import vitruvio

    assert len(vitruvio.__path__) == len(MEMBERS), (
        f"expected {len(MEMBERS)} namespace portions, found {list(vitruvio.__path__)}"
    )


def test_the_version_is_declared_once() -> None:
    """Every member's pyproject agrees with the kernel's ``__version__``."""
    import tomllib

    from vitruvio.kernel import __version__

    mismatched = {}
    for pyproject in sorted(REPO.glob("*/*/pyproject.toml")):
        document = tomllib.loads(pyproject.read_text())
        declared = document.get("project", {}).get("version")
        if declared != __version__:
            mismatched[str(pyproject.relative_to(REPO))] = declared
    assert not mismatched, f"versions disagree with vitruvio.kernel.__version__ ({__version__}): {mismatched}"
