"""Policy profiles, index defaults, and the schema's refusals."""

from __future__ import annotations

from pathlib import Path

import pytest
from boltzmann.blocks.memory_type import MemoryType
from boltzmann.blocks.provenance import ActorKind
from boltzmann.indices.base import IndexKind
from boltzmann.retention.policy import RemovalMechanism
from pydantic import ValidationError

from vitruvio.kernel import (
    DEFAULT_TEXT_EMBEDDER,
    ActorSpec,
    CollaboratorSpec,
    EmbedderSpec,
    IndexSpec,
    PolicyProfile,
    PolicySpec,
    ProjectConfig,
    RegistrySpec,
    default_indices,
)


class TestPolicyProfiles:
    def test_conservative_refuses_canonical_drops_and_reviews_cascades(self) -> None:
        policy = PolicySpec().build()
        assert policy.canonical_drop_allowed is False
        assert policy.cascade_review_threshold == 25
        with pytest.raises(Exception, match="privileged"):
            policy.authorize(RemovalMechanism.DROP, MemoryType.CANONICAL)

    def test_permissive_allows_canonical_drops(self) -> None:
        policy = PolicySpec(profile=PolicyProfile.PERMISSIVE).build()
        assert policy.canonical_drop_allowed is True
        policy.authorize(RemovalMechanism.DROP, MemoryType.CANONICAL)

    def test_archival_permits_no_drop_at_all(self) -> None:
        """Archival is not `keep the canonical`; it is `keep everything`, expressed as an empty drop set."""
        policy = PolicySpec(profile=PolicyProfile.ARCHIVAL).build()
        assert policy.droppable_modules == []
        for memory in (MemoryType.SEMANTIC, MemoryType.PROCEDURAL, MemoryType.PROVENANCE):
            with pytest.raises(Exception, match="does not permit dropping"):
                policy.authorize(RemovalMechanism.DROP, memory)

    def test_explicit_fields_override_the_profile(self) -> None:
        policy = PolicySpec(profile=PolicyProfile.CONSERVATIVE, canonical_drop_allowed=True).build()
        assert policy.canonical_drop_allowed is True

    def test_episodic_is_append_only_regardless_of_profile(self) -> None:
        """No policy can permit dropping an episode: it is the protocol's rule, not the deployment's."""
        policy = PolicySpec(profile=PolicyProfile.PERMISSIVE).build()
        with pytest.raises(Exception, match="append-only by protocol"):
            policy.authorize(RemovalMechanism.DROP, MemoryType.EPISODIC)

    def test_redaction_is_forbidden_until_media_types_are_declared(self) -> None:
        policy = PolicySpec(profile=PolicyProfile.PERMISSIVE).build()
        with pytest.raises(Exception, match="redactable media types"):
            policy.authorize(RemovalMechanism.TOMBSTONE, MemoryType.CANONICAL)

        allowed = PolicySpec(redactable_media_types=["application/pdf"]).build()
        allowed.authorize(RemovalMechanism.TOMBSTONE, MemoryType.CANONICAL)


class TestIndexDefaults:
    def test_every_memory_type_gets_the_structural_indices(self) -> None:
        specs = default_indices()
        for memory in MemoryType:
            kinds = {spec.kind for spec in specs if spec.memory_type is memory}
            assert {IndexKind.HASH_MAP, IndexKind.BTREE, IndexKind.BITMAP} <= kinds

    def test_provenance_is_not_embedded_or_tokenized(self) -> None:
        """Registration-record text competing in a similarity ranking is pure noise."""
        kinds = {spec.kind for spec in default_indices() if spec.memory_type is MemoryType.PROVENANCE}
        assert IndexKind.VECTOR not in kinds
        assert IndexKind.INVERTED not in kinds

    def test_graph_is_registered_where_edges_exist(self) -> None:
        graphed = {spec.memory_type for spec in default_indices() if spec.kind is IndexKind.GRAPH}
        assert graphed == {MemoryType.SEMANTIC, MemoryType.PROCEDURAL, MemoryType.PROVENANCE}

    def test_vector_indices_name_the_embedder_they_use(self) -> None:
        for spec in default_indices():
            if spec.kind is IndexKind.VECTOR:
                assert spec.embedder == "text"
            else:
                assert spec.embedder is None

    def test_declared_indices_replace_the_defaults_entirely(self) -> None:
        project = ProjectConfig(index=[IndexSpec(memory_type=MemoryType.SEMANTIC, kind=IndexKind.INVERTED)])
        assert len(project.indices) == 1


class TestSchema:
    def test_actor_ids_are_tolerant_on_read_and_strict_on_write(self, tmp_path: Path) -> None:
        project = ProjectConfig(actor=ActorSpec(id="Legacy Actor"))
        from vitruvio.kernel import Origin, ResolvedConfig

        resolved = ResolvedConfig(
            brain=tmp_path,
            brain_origin=Origin.FLAG,
            project=project,
        )
        with pytest.raises(Exception, match="not usable"):
            resolved.actor()

    def test_collaborators_use_the_provenance_v2_shape(self) -> None:
        collaborator = CollaboratorSpec(
            id="anthropic/claude-code",
            kind=ActorKind.AGENT,
            model="openai/gpt-5",
        ).build()
        assert collaborator.id == "anthropic/claude-code"
        assert collaborator.model == "openai/gpt-5"

    def test_a_bare_config_needs_no_extras_to_embed(self) -> None:
        """A default install must still be able to build a vector index, tagged so nobody is fooled."""
        assert ProjectConfig().text_embedder == DEFAULT_TEXT_EMBEDDER
        assert DEFAULT_TEXT_EMBEDDER.provider == "hashing"
        assert ProjectConfig().vision_embedder is None

    def test_embedder_uri_is_provider_colon_model(self) -> None:
        spec = EmbedderSpec(provider="local-st", model="intfloat/multilingual-e5-base", dims=768)
        assert spec.uri == "local-st:intfloat/multilingual-e5-base"

    def test_a_reference_carrying_a_tag_is_rejected(self) -> None:
        """`repo:tag` in the reference field produces `repo:tag:tag` at push time."""
        with pytest.raises(ValidationError, match="carries a tag"):
            RegistrySpec(reference="docker.io/ns/brain:v1")

    def test_a_reference_with_a_port_is_not_mistaken_for_a_tag(self) -> None:
        assert RegistrySpec(reference="localhost:5000/ns/brain").reference == "localhost:5000/ns/brain"

    def test_unknown_keys_are_refused_everywhere(self) -> None:
        # model_validate rather than a keyword call: the point is a *runtime* assertion about
        # extra="forbid", and a keyword mypy can see is a call mypy rejects before it ever runs.
        with pytest.raises(ValidationError):
            ProjectConfig.model_validate({"unexpected": {"a": 1}})

    def test_there_is_nowhere_to_put_a_secret(self) -> None:
        """Credentials come from the environment. The schema having no field for one is the guarantee."""
        fields = set(RegistrySpec.model_fields)
        assert not fields & {"token", "password", "username", "secret", "auth"}
