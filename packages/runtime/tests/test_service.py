"""The service layer: lifecycle, registration, inspection, and the capability gate."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from vitruvio.kernel import ActorUnknownError, VitruvioError, resolve
from vitruvio.runtime import BrainService, Capability, known_codes, report_for, translate
from vitruvio.runtime.assembly import build_indices


class TestLifecycle:
    def test_init_creates_a_layout_and_a_config(self, config, tmp_path: Path) -> None:
        result = BrainService(config).init()
        assert result["created"] is True
        assert (tmp_path / "brain" / "oci-layout").is_file()
        assert Path(result["config_file"]).is_file()

    def test_init_is_idempotent_and_does_not_clobber(self, config) -> None:
        service = BrainService(config)
        service.init()
        again = service.init()
        assert again["created"] is False

    def test_init_refuses_a_non_empty_directory_that_is_not_a_brain(self, tmp_path: Path) -> None:
        occupied = tmp_path / "occupied"
        occupied.mkdir()
        (occupied / "notes.txt").write_text("someone else's data")
        config = resolve(brain=occupied, actor_id="a@b.c", require_layout=False)
        with pytest.raises(VitruvioError, match="not empty"):
            BrainService(config).init()

    def test_init_records_a_path_relative_to_the_config_file(self, config) -> None:
        """An absolute path would break the moment the project is cloned elsewhere."""
        from vitruvio.kernel import load_project

        result = BrainService(config).init()
        project = load_project(Path(result["config_file"]))
        assert project.brain.path == "./brain"

    def test_an_empty_brain_verifies(self, service: BrainService) -> None:
        """A brain with no canonical evidence is empty, not broken."""
        result = service.verify()
        assert result["verified"] is True
        assert result["block_count"] == 0

    def test_state_reports_where_the_brain_came_from(self, service: BrainService) -> None:
        result = service.state()
        assert result["brain_origin"] == "flag"
        assert result["installed"] == []
        assert result["origin"] is None

    def test_history_is_empty_before_the_first_write(self, service: BrainService) -> None:
        assert service.history()["snapshots"] == []


class TestRegistration:
    def test_register_creates_a_canonical_block_and_a_version(self, service: BrainService, source_file: Path) -> None:
        result = service.register(source_file, media_type="text/markdown")
        assert result["block_id"].startswith("sha256:")
        assert result["duplicate"] is False
        assert result["snapshot"] is not None

        state = service.state()
        assert set(state["installed"]) == {"canonical", "provenance"}

    def test_re_registering_identical_bytes_is_a_no_op(self, service: BrainService, source_file: Path) -> None:
        """Identity is derived from content, so the second call mints no version."""
        first = service.register(source_file, media_type="text/markdown")
        second = service.register(source_file, media_type="text/markdown")
        assert second["block_id"] == first["block_id"]
        assert second["duplicate"] is True
        assert second["snapshot"] is None

    def test_a_registered_block_proves_into_its_module_root(self, service: BrainService, source_file: Path) -> None:
        registered = service.register(source_file, media_type="text/markdown")
        proof = service.prove(registered["block_id"], "canonical")
        assert proof["verified"] is True
        assert proof["root"] == service.roots()["roots"]["canonical"]

    def test_a_registered_block_resolves_to_bytes_that_hash_to_its_identity(
        self, service: BrainService, source_file: Path
    ) -> None:
        registered = service.register(source_file, media_type="text/markdown")
        block = service.resolve(registered["block_id"])
        assert block["memory_type"] == "canonical"
        assert block["payload"]["media_type"] == "text/markdown"
        assert block["payload"]["size"] == source_file.stat().st_size

    def test_replace_records_a_supersession_without_removing_the_old_block(
        self, service: BrainService, source_file: Path, tmp_path: Path
    ) -> None:
        first = service.register(source_file, media_type="text/markdown")
        newer = tmp_path / "fourier-2nd.md"
        newer.write_text("# Series de Fourier, segunda edicion\n", encoding="utf-8")

        result = service.replace(newer, supersedes=first["block_id"], media_type="text/markdown")
        assert result["block_id"] != first["block_id"]
        # The old block stays in the composition and keeps proving: what changed is precedence.
        assert service.prove(first["block_id"], "canonical")["verified"] is True

    def test_writing_without_an_actor_is_refused(self, tmp_path: Path, source_file: Path) -> None:
        config = resolve(brain=tmp_path / "anon", require_layout=False)
        service = BrainService(config)
        service.init()
        with pytest.raises(ActorUnknownError):
            service.register(source_file, media_type="text/markdown")

    def test_reading_without_an_actor_is_allowed(self, tmp_path: Path) -> None:
        """Inspecting someone else's brain attributes nothing, so it must not require an identity."""
        config = resolve(brain=tmp_path / "anon", require_layout=False)
        service = BrainService(config)
        service.init()
        assert service.verify()["verified"] is True


class TestInspection:
    def test_resolvability_is_intact_for_a_freshly_written_brain(
        self, service: BrainService, source_file: Path
    ) -> None:
        service.register(source_file, media_type="text/markdown")
        report = service.resolvability()
        assert report["intact"] is True
        assert report["counts"]["resolvable"]["canonical"] == 1

    def test_module_reports_its_shape_and_truncates_its_sample(self, service: BrainService, source_file: Path) -> None:
        service.register(source_file, media_type="text/markdown")
        module = service.module("canonical", limit=0)
        assert module["block_count"] == 1
        assert module["block_ids"] == []
        assert module["truncated"] is True

    def test_an_unknown_memory_type_lists_the_valid_ones(self, service: BrainService) -> None:
        with pytest.raises(VitruvioError, match="procedural"):
            service.module("semantics")

    def test_a_malformed_block_id_is_a_usage_error_not_a_crash(self, service: BrainService) -> None:
        with pytest.raises(VitruvioError) as caught:
            service.resolve("not-a-digest")
        assert caught.value.code != "INTERNAL"


class TestSearch:
    def test_search_returns_a_verified_bundle_and_never_prose(self, service: BrainService, source_file: Path) -> None:
        service.register(source_file, media_type="text/markdown")
        bundle = service.search("fourier")
        assert "answer" not in bundle
        assert bundle["all_verified"] is True
        assert isinstance(bundle["verified_against"], dict)

    def test_no_match_is_an_answer_rather_than_an_error(self, service: BrainService) -> None:
        bundle = service.search("something this brain has never held")
        assert bundle["matches"] == []

    def test_scores_stay_strings(self, service: BrainService, source_file: Path) -> None:
        """The protocol renders a score as a decimal string; parsing it would invent precision."""
        service.register(source_file, media_type="text/markdown")
        for match in service.search("markdown", memory_types=["canonical"])["matches"]:
            assert isinstance(match["score"], str)

    def test_visual_diagnostics_are_opt_in_and_share_the_executed_plan(
        self, service: BrainService, source_file: Path
    ) -> None:
        service.register(source_file, media_type="text/markdown")
        ordinary = service.search("fourier")
        visual = service.search("fourier", diagnostics=True)
        assert "diagnostics" not in ordinary
        assert visual["plan"]["operators"], "the service carries the operators from the search that just ran"
        assert visual["diagnostics"].keys() == {"graph", "vector", "btree"}
        assert all("selected" in visual["diagnostics"][kind] for kind in ("graph", "vector", "btree"))

    def test_a_time_filter_cannot_admit_a_block_without_a_timestamp(
        self, service: BrainService, source_file: Path
    ) -> None:
        service.register(source_file, media_type="text/markdown")
        assert service.search("fourier", since="2026-01-01T00:00:00Z")["matches"] == []

    def test_time_filtering_happens_before_the_result_limit(self, service: BrainService, source_file: Path) -> None:
        """Out-of-window leaders must not spend the reserve and hide a later valid episode."""
        from boltzmann.blocks.memory_type import MemoryType
        from boltzmann.identity.digest import BlockId
        from boltzmann.ingest.proposer import Candidate, CandidateSet

        registered = service.register(source_file, media_type="text/markdown")
        brain = service.brain(Capability.WRITE)
        source = BlockId.parse(registered["block_id"])
        task = brain.define_task(source, allowed=[MemoryType.EPISODIC])
        candidates = CandidateSet(
            task_id=task.task_id,
            candidates=[
                Candidate(
                    memory_type=MemoryType.EPISODIC,
                    evidence=[source],
                    locator=f"episode:{position}",
                    payload={
                        "summary": "fourier",
                        "occurred_at": (
                            "2026-07-01T00:00:00Z" if position == 5 else f"2026-01-{position + 1:02d}T00:00:00Z"
                        ),
                    },
                )
                for position in range(6)
            ],
        )
        brain.commit(brain.validate(candidates, task))

        result = service.search(
            "fourier",
            memory_types=["episodic"],
            since="2026-06-01T00:00:00Z",
            limit=1,
        )
        assert [match["content"]["occurred_at"] for match in result["matches"]] == ["2026-07-01T00:00:00Z"]


class TestCapabilityGate:
    def test_inspect_registers_no_index(self, config) -> None:
        """The gate that keeps `brain state` from importing torch. See assembly.py."""
        assert build_indices(config, Capability.INSPECT) is None

    def test_retrieve_registers_the_configured_indices(self, config) -> None:
        assert build_indices(config, Capability.RETRIEVE) is not None

    def test_an_inspect_operation_does_not_import_an_embedder(self, service: BrainService) -> None:
        """Asserted on sys.modules rather than by timing, so it cannot pass by being fast on a good day."""
        for module in ("torch", "sentence_transformers"):
            sys.modules.pop(module, None)
        service.state()
        service.verify()
        assert "torch" not in sys.modules
        assert "sentence_transformers" not in sys.modules


class TestErrorMapping:
    def test_every_sdk_error_maps_to_something_other_than_internal(self) -> None:
        """Reaching INTERNAL means a bug in vitruvio, so no protocol failure may land there."""
        from boltzmann.exceptions import (
            BlockNotFoundError,
            DistributionError,
            QueryError,
            RetentionPolicyError,
            ValidationError,
        )

        for error in (
            RetentionPolicyError("no"),
            ValidationError("no"),
            BlockNotFoundError("no"),
            DistributionError("no"),
            QueryError("no"),
        ):
            assert report_for(error).code != "INTERNAL"

    def test_a_policy_refusal_is_not_retryable_and_a_registry_failure_is(self) -> None:
        """The column a caller acts on: an agent that retries a policy refusal is an agent in a loop."""
        from boltzmann.exceptions import DistributionError, RetentionPolicyError

        assert report_for(RetentionPolicyError("no")).retryable is False
        assert report_for(DistributionError("no")).retryable is True

    def test_translate_preserves_the_original_message(self) -> None:
        from boltzmann.exceptions import BlockNotFoundError

        translated = translate(BlockNotFoundError("sha256:abc is not held"))
        assert "sha256:abc" in translated.message
        assert translated.code == "BLOCK_NOT_FOUND"

    def test_a_vitruvio_error_passes_through_unchanged(self) -> None:
        original = VitruvioError("mine", hint="do this")
        assert translate(original) is original

    def test_codes_are_unique_and_documented(self) -> None:
        codes = list(known_codes())
        assert len(codes) == len(set(codes))
        assert "POLICY_REFUSED" in codes
