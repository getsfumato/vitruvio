"""Legacy recreation keeps the source and preserves reproducible knowledge identities."""

from __future__ import annotations

from pathlib import Path

from vitruvio.kernel import resolve
from vitruvio.runtime import BrainService


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
    assert migrated.verify()["verified"] is True


def test_migration_dry_run_creates_nothing(service: BrainService, tmp_path: Path) -> None:
    destination = tmp_path / "not-created"
    result = service.migrate(destination, governed=False, dry_run=True)
    assert result["completed"] is False
    assert not destination.exists()
