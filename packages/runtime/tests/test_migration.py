"""Legacy recreation keeps the source and preserves reproducible knowledge identities."""

from __future__ import annotations

from pathlib import Path

import pytest

from vitruvio.kernel import UsageError, resolve
from vitruvio.runtime import BrainService


def add_semantic(service: BrainService, source: str) -> str:
    """Commit one derived block and return the identity migration must preserve."""
    task = service.define_task(source, allowed=["semantic"])
    result = service.commit_candidates(
        {
            "candidates": [
                {
                    "memory_type": "semantic",
                    "payload": {"kind": "concept", "label": "Fourier", "statement": "Periodic decomposition."},
                    "evidence": [source],
                    "locator": "lines:1-3",
                }
            ]
        },
        task,
    )
    return str(result["committed"][0])


def test_migration_recreates_current_canonical_state_without_touching_source(
    service: BrainService, source_file: Path, tmp_path: Path
) -> None:
    registered = service.register(source_file, media_type="text/markdown")
    source_snapshot = service.state()["snapshot"]["digest"]
    destination = tmp_path / "migrated"

    result = service.migrate(destination, governed=False)

    assert result["completed"] is True
    assert result["source_preserved"] is True
    assert result["source_snapshot"] == source_snapshot
    assert registered["block_id"] in result["preserved_ids"]
    assert service.state()["snapshot"]["digest"] == source_snapshot

    migrated = BrainService(resolve(brain=destination, actor_id="tester@example.com"))
    assert registered["block_id"] in migrated.module("canonical", limit=100)["block_ids"]
    assert migrated.state()["snapshot"]["labels"]["vitruvio.migrated-from"] == source_snapshot
    assert migrated.verify()["verified"] is True


def test_migration_dry_run_creates_nothing(service: BrainService, tmp_path: Path) -> None:
    destination = tmp_path / "not-created"
    result = service.migrate(destination, governed=False, dry_run=True)
    assert result["completed"] is False
    assert not destination.exists()


def test_migration_preserves_derived_identities(service: BrainService, source_file: Path, tmp_path: Path) -> None:
    source = service.register(source_file, media_type="text/markdown")["block_id"]
    semantic = add_semantic(service, source)

    result = service.migrate(tmp_path / "with-semantic", governed=False)

    assert semantic in result["preserved_ids"]


def test_dry_run_reports_an_open_evidence_chain_before_real_migration_refuses(
    service: BrainService, source_file: Path, tmp_path: Path
) -> None:
    source = service.register(source_file, media_type="text/markdown")["block_id"]
    semantic = add_semantic(service, source)
    replacement = tmp_path / "replacement.md"
    replacement.write_text("# Replacement\n", encoding="utf-8")
    service.replace(replacement, supersedes=source, media_type="text/markdown")
    destination = tmp_path / "incomplete"

    report = service.migrate(destination, governed=False, dry_run=True)

    assert report["completed"] is False
    assert any(item["block"] == semantic for item in report["problems"])
    assert any(item["block"] == source for item in report["excluded"])
    assert not destination.exists()
    with pytest.raises(UsageError, match="non-reproducible"):
        service.migrate(destination, governed=False)

    partial = service.migrate(destination, governed=False, allow_partial=True)
    assert any(item["block"] == semantic for item in partial["skipped"])
    assert any(item["block"] == source for item in partial["skipped"])
    assert partial["partial"] is True


def test_catalog_placements_whose_source_is_not_migrated_are_reported(
    service: BrainService, source_file: Path, tmp_path: Path
) -> None:
    source = service.register(source_file, media_type="text/markdown")["block_id"]
    service.catalog_apply(
        {
            "schema": "vitruvio.catalog/v1",
            "schemes": [{"name": "topic"}],
            "classes": [{"scheme": "topic", "label": "Science"}],
            "placements": [{"source": source, "classes": ["topic/Science"]}],
        }
    )
    replacement = tmp_path / "replacement.md"
    replacement.write_text("# Replacement\n", encoding="utf-8")
    service.replace(replacement, supersedes=source, media_type="text/markdown")

    destination = tmp_path / "catalog-migration"
    plan = service.migrate(destination, governed=False, dry_run=True)

    assert any("catalog placement source" in item["reason"] for item in plan["problems"])
    with pytest.raises(UsageError, match="non-reproducible"):
        service.migrate(destination, governed=False)

    result = service.migrate(destination, governed=False, allow_partial=True)

    assert any("catalog placement source" in item["reason"] for item in result["skipped"])
    assert result["partial"] is True


def test_partial_migration_preflights_canonical_identity_before_installing(
    service: BrainService,
    source_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = service.register(source_file, media_type="text/markdown")["block_id"]

    from boltzmann.brain import Brain
    from boltzmann.identity.digest import BlockId
    from boltzmann.ingest.register import RegistrationResult

    recreated = BlockId.parse(f"sha256:{'0' * 64}")
    calls = 0

    def drifted_register(_brain: Brain, _data: bytes, _request: object) -> RegistrationResult:
        nonlocal calls
        calls += 1
        return RegistrationResult(block_id=recreated)

    monkeypatch.setattr(Brain, "register", drifted_register)
    destination = tmp_path / "identity-drift"

    result = service.migrate(destination, governed=False, allow_partial=True)

    assert calls == 1, "identity drift must be detected in the disposable preview before target.register"
    assert any(item["block"] == source and "would be recreated" in item["reason"] for item in result["skipped"])
    migrated = BrainService(resolve(brain=destination, actor_id="tester@example.com"))
    assert "canonical" not in migrated.state()["installed"]
