"""The local query runtime must not become the model used for published writes."""

from __future__ import annotations

from pathlib import Path

import pytest
from boltzmann.blocks.memory_type import MemoryType
from boltzmann.indices.base import IndexKind

from vitruvio.indices import VectorIndex
from vitruvio.indices import format as envelope
from vitruvio.ingest.evidence import Evidence
from vitruvio.kernel import EmbedderSpec, ResolvedConfig, remember_embedding_choice, resolve
from vitruvio.runtime import BrainService
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


def _canonical_vector(service: BrainService) -> VectorIndex:
    brain = service.brain(Capability.RETRIEVE)
    return next(index for index in brain.indices[MemoryType.CANONICAL] if isinstance(index, VectorIndex))


def _register(service: BrainService, name: str) -> str:
    result = service.register(
        Evidence.from_bytes(f"# {name}\n\n{name} detail.".encode(), origin=f"test://{name}", media_type="text/markdown")
    )
    return str(result["block_id"])


def test_index_build_refreshes_and_publishes_the_shared_model_after_a_local_hashing_query(
    config: ResolvedConfig, tmp_path: Path
) -> None:
    configured = _configured(config, EmbedderSpec(provider="fake", model="deterministic", dims=32))
    service = BrainService(configured)
    service.init()
    _register(service, "Fourier")
    service.use_embedder("hashing", "bow")
    assert _canonical_vector(service).embedder.tag.is_fallback

    service.index_build(force=True)

    shared_path = indices_home(config) / "canonical.vector.vidx"
    header, _ = envelope.decode(shared_path.read_bytes())
    assert header.model_tag is not None
    assert header.model_tag.startswith("fake/deterministic")
    registry = tmp_path / "registry"
    registry.mkdir()
    service.push("demo/shared", tag="v1", local=registry)
    consumer = BrainService(resolve(brain=tmp_path / "consumer", actor_id="tester@example.com", require_layout=False))
    consumer.init()
    assert consumer.plan_pull("demo/shared", tag="v1", local=registry)["vector_models"]["canonical"] == header.model_tag


def test_local_fallback_refreshes_after_a_commit_without_overwriting_shared_vectors(config: ResolvedConfig) -> None:
    configured = _configured(config, EmbedderSpec(provider="fake", model="deterministic", dims=32))
    service = BrainService(configured)
    service.init()
    _register(service, "Fourier")
    service.use_embedder("hashing", "bow")
    first = _canonical_vector(service)
    first_root = first.bound_root
    assert first.population == 1
    _register(service, "Wavelets")
    canonical_path = indices_home(config) / "canonical.vector.vidx"
    shared = canonical_path.read_bytes()

    refreshed = _canonical_vector(service)

    assert refreshed.population == 2
    assert refreshed.bound_root != first_root
    assert refreshed.bound_root == str(service.brain(Capability.INSPECT).module(MemoryType.CANONICAL).root)
    assert canonical_path.read_bytes() == shared


def test_default_hashing_query_does_not_create_a_canonical_vector_sidecar(service: BrainService) -> None:
    _register(service, "Fourier")
    canonical = indices_home(service.config) / "canonical.vector.vidx"
    canonical.unlink(missing_ok=True)
    service.session.invalidate()

    service.search("Fourier")

    assert not canonical.exists()
    assert not list((indices_home(service.config) / "local").glob("**/*.vidx"))


def test_reindex_after_a_shared_model_change_replaces_the_canonical_sidecar(config: ResolvedConfig) -> None:
    first = BrainService(_configured(config, EmbedderSpec(provider="fake", model="deterministic", dims=32)))
    first.init()
    _register(first, "Fourier")
    first.index_build()
    second = BrainService(_configured(config, EmbedderSpec(provider="fake", model="deterministic", dims=64)))

    second.index_build(force=True)

    header, _ = envelope.decode((indices_home(config) / "canonical.vector.vidx").read_bytes())
    assert header.model_tag is not None
    assert "#d64," in header.model_tag


def test_query_migrates_a_legacy_travelling_layer_without_a_local_sidecar(
    config: ResolvedConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from vitruvio.embeddings import FakeEmbedder
    from vitruvio.embeddings.registry import _REGISTRY

    class Legacy(FakeEmbedder):
        @property
        def tag(self):
            return replace(super().tag, provider="legacy", model="fake/deterministic")

    monkeypatch.setitem(_REGISTRY, "legacy-test", lambda spec: Legacy())
    producer = BrainService(_configured(config, EmbedderSpec(provider="legacy-test", model="fake/deterministic")))
    producer.init()
    _register(producer, "Fourier")
    producer.index_build()
    registry = tmp_path / "registry"
    registry.mkdir()
    producer.push("demo/legacy", tag="v1", local=registry)
    consumer_config = resolve(brain=tmp_path / "consumer", actor_id="tester@example.com", require_layout=False)
    consumer = BrainService(_configured(consumer_config, EmbedderSpec(provider="fake", model="deterministic", dims=32)))
    consumer.init()
    consumer.pull("demo/legacy", tag="v1", local=registry)
    brain = consumer.brain(Capability.INSPECT)
    reference = brain.snapshot().modules[MemoryType.CANONICAL]
    assert reference.index_digest is not None
    original = brain.store.get_bytes(reference.index_digest)
    assert not (indices_home(consumer.config) / "canonical.vector.vidx").exists()

    restored = _canonical_vector(consumer)

    assert restored.population == 1
    assert restored.queryable
    assert restored.path is not None
    assert restored.path.is_relative_to(indices_home(consumer.config) / "local")
    assert restored.path.exists()
    assert brain.store.get_bytes(reference.index_digest) == original


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
    monkeypatch.delenv("VITRUVIO_OPENAI_API_KEY", raising=False)
    shared = EmbedderSpec(provider="openai", model="text-embedding-3-small", dims=1536, revision="v1")
    configured = _configured(config, shared)

    reader = _vector(configured, Capability.RETRIEVE)

    assert reader.embedder.tag.provider == "hashing"
    assert reader.path is not None
    assert reader.path.is_relative_to(indices_home(config) / "local")
