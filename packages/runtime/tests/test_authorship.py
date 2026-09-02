"""Authorship audit degradation must never manufacture certainty from unreadable history."""

from __future__ import annotations

from pathlib import Path

import pytest

from vitruvio.runtime import BrainService
from vitruvio.runtime.authorship import AuthorshipAudit, Membership


def test_an_unreadable_current_provenance_composition_is_an_evidence_gap(
    service: BrainService, source_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.register(source_file, media_type="text/markdown")
    brain = service.brain()
    snapshot = brain.snapshot()
    audit = AuthorshipAudit(brain, policy=service.config.project.authenticity.build())
    monkeypatch.setattr(
        audit,
        "_membership",
        lambda _snapshot: Membership(frozenset(), ("current provenance composition is unreadable",)),
    )

    result = audit.participants(snapshot)
    assert result["complete"] is False
    assert result["evidence_gaps"] == ["current provenance composition is unreadable"]


def test_an_unreadable_parent_does_not_make_every_current_record_look_new(
    service: BrainService, source_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.register(source_file, media_type="text/markdown")
    second = source_file.parent / "second.md"
    second.write_text("second", encoding="utf-8")
    service.register(second, media_type="text/markdown")
    brain = service.brain()
    snapshot = brain.snapshot()
    assert snapshot.first_parent is not None
    audit = AuthorshipAudit(brain, policy=service.config.project.authenticity.build())
    monkeypatch.setattr(audit, "snapshots", lambda: {str(snapshot.digest): snapshot})

    result = audit.participants(snapshot)
    assert result["actors"] == []
    assert result["complete"] is False
    assert str(snapshot.first_parent) in result["evidence_gaps"][0]
