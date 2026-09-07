"""The toml declares who writes and who may assist; the runtime writes those declarations and then obeys them."""

from __future__ import annotations

from pathlib import Path

import pytest

from vitruvio.kernel import CollaboratorNotDeclaredError, Origin, resolve
from vitruvio.runtime import BrainService


def test_brain_init_writes_the_collaborators_the_flag_named(tmp_path: Path) -> None:
    brain = tmp_path / "brain"
    declaring = resolve(
        brain=brain,
        actor_id="tester@example.com",
        assisted_by=["openai/codex"],
        require_layout=False,
        declaring=True,
    )
    BrainService(declaring).init()

    later = resolve(brain=brain)
    assert later.actor().id == "tester@example.com"
    assert [party.id for party in later.collaborators()] == ["openai/codex"]
    assert later.collaborators_origin is Origin.FILE
    with pytest.raises(CollaboratorNotDeclaredError):
        resolve(brain=brain, assisted_by=["anthropic/claude-code"])


def test_project_add_declares_a_brains_own_collaborators(tmp_path: Path) -> None:
    config = tmp_path / "vitruvio.toml"
    config.write_text(
        '[project]\nname = "coursework"\n\n[actor]\nid = "tester@example.com"\n\n[[assisted_by]]\nid = "shared/agent"\n',
        encoding="utf-8",
    )
    service = BrainService(resolve(config=config, require_brain=False, require_layout=False, declaring=True))

    added = service.add_brain("optics", assisted_by=["anthropic/claude-code"])
    assert added["assisted_by"] == ["anthropic/claude-code"]
    service.add_brain("acoustics")

    optics = resolve(config=config, brain=Path("optics"))
    assert [party.id for party in optics.collaborators()] == ["anthropic/claude-code"]
    acoustics = resolve(config=config, brain=Path("acoustics"))
    assert [party.id for party in acoustics.collaborators()] == ["shared/agent"]
