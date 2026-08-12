"""Fixtures for the service layer's tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from vitruvio.kernel import ResolvedConfig, resolve
from vitruvio.runtime import BrainService


@pytest.fixture
def config(tmp_path: Path) -> ResolvedConfig:
    """A resolved configuration over a brain that does not exist yet, ready for ``init``."""
    return resolve(brain=tmp_path / "brain", actor_id="tester@example.com", require_layout=False)


@pytest.fixture
def service(config: ResolvedConfig) -> BrainService:
    """A service over an initialised, empty brain."""
    built = BrainService(config)
    built.init()
    return built


@pytest.fixture
def source_file(tmp_path: Path) -> Path:
    """A small Markdown source, in Spanish and English, since both are indexed."""
    path = tmp_path / "fourier.md"
    path.write_text(
        "# Series de Fourier\n\n"
        "Una serie de Fourier descompone una funcion periodica en senos y cosenos.\n"
        "A Fourier series decomposes a periodic function into sines and cosines.\n",
        encoding="utf-8",
    )
    return path
