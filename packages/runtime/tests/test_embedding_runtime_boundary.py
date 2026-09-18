"""The local query runtime must not become the model used for published writes."""

from __future__ import annotations

from pathlib import Path

import pytest
from boltzmann.blocks.memory_type import MemoryType
from boltzmann.indices.base import IndexKind

from vitruvio.indices import VectorIndex
from vitruvio.kernel import EmbedderSpec, ResolvedConfig, remember_embedding_choice
from vitruvio.runtime.assembly import build_indices
from vitruvio.runtime.capability import Capability
from vitruvio.runtime.indexset import indices_home


def _configured(config: ResolvedConfig, spec: EmbedderSpec) -> ResolvedConfig:
    project = config.project.model_copy(update={"embedding": {"text": spec}})
    return config.model_copy(update={"project": project})


def _vector(config: ResolvedConfig, capability: Capability) -> VectorIndex:
    indices = build_indices(config, capability)
    assert indices is not None
    index = next(item for item in indices[MemoryType.SEMANTIC] if item.kind is IndexKind.VECTOR)
    assert isinstance(index, VectorIndex)
    return index


def test_local_hashing_choice_is_query_only(
    config: ResolvedConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    shared = EmbedderSpec(provider="fake", model="semantic-model", dims=32, revision="v1")
    configured = _configured(config, shared)
    remember_embedding_choice(config.brain, EmbedderSpec(provider="hashing", model="bow", dims=256))

    reader = _vector(configured, Capability.RETRIEVE)
    writer = _vector(configured, Capability.WRITE)

    assert reader.embedder.tag.provider == "hashing"
    assert reader.path is not None
    assert reader.path.is_relative_to(indices_home(config) / "local")
    assert writer.embedder.tag.provider == "fake"
    assert writer.path == indices_home(config) / "semantic.vector.vidx"


def test_unavailable_shared_model_gets_local_hashing_for_queries(
    config: ResolvedConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    shared = EmbedderSpec(provider="openai", model="text-embedding-3-small", dims=1536, revision="v1")
    configured = _configured(config, shared)

    reader = _vector(configured, Capability.RETRIEVE)

    assert reader.embedder.tag.provider == "hashing"
    assert reader.path is not None
    assert reader.path.is_relative_to(indices_home(config) / "local")
