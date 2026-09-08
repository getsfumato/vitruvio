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


def test_project_add_declares_a_brains_own_actor_only_when_it_differs(tmp_path: Path) -> None:
    """The project's actor restated is not a declaration; a different person is."""
    from vitruvio.kernel import load_project

    config = tmp_path / "vitruvio.toml"
    config.write_text('[project]\nname = "coursework"\n\n[actor]\nid = "tester@example.com"\n', encoding="utf-8")
    service = BrainService(resolve(config=config, require_brain=False, require_layout=False, declaring=True))

    own = service.add_brain("optics", actor="colleague@example.com")
    assert own["actor"] == "colleague@example.com"
    same = service.add_brain("acoustics", actor="tester@example.com")
    assert same["actor"] is None

    assert resolve(config=config, brain=Path("optics")).actor().id == "colleague@example.com"
    assert resolve(config=config, brain=Path("acoustics")).actor().id == "tester@example.com"
    assert load_project(config).brains["acoustics"].actor is None


def _candidates(source: str) -> dict[str, object]:
    return {
        "candidates": [
            {
                "memory_type": "semantic",
                "payload": {"kind": "concept", "label": "Fourier", "statement": "Periodic decomposition."},
                "evidence": [source],
                "locator": "lines:1-3",
            }
        ]
    }


def test_ingest_refuses_to_run_without_saying_who_assisted(tmp_path: Path, source_file: Path) -> None:
    """A model's knowledge records the model; leaving the assistant unsaid is refused, saying nobody is allowed."""
    from vitruvio.kernel import CollaboratorRequiredError

    brain = tmp_path / "brain"
    BrainService(
        resolve(
            brain=brain,
            actor_id="tester@example.com",
            assisted_by=["openai/codex"],
            require_layout=False,
            declaring=True,
        )
    ).init()

    unsaid = BrainService(resolve(brain=brain))
    source = unsaid.register(source_file, media_type="text/markdown")["block_id"]
    task = unsaid.define_task(source, allowed=["semantic"])
    with pytest.raises(CollaboratorRequiredError) as caught:
        unsaid.commit_candidates(_candidates(source), task)
    assert caught.value.code == "COLLABORATOR_REQUIRED"
    assert "openai/codex" in (caught.value.hint or "")

    assisted = BrainService(resolve(brain=brain, assisted_by=["openai/codex"]))
    committed = assisted.commit_candidates(_candidates(source), task)
    (block,) = committed["committed"]
    row = next(item for item in assisted.blocks("semantic")["rows"] if item["block_id"] == str(block))
    (claim,) = row["authorship"]["claims"]
    assert [party["id"] for party in claim["assisted_by"]] == ["openai/codex"]

    alone = BrainService(resolve(brain=brain, assisted_by=[]))
    other = alone.define_task(source, allowed=["semantic"])
    payload = {"kind": "fact", "label": "Alone", "statement": "Written by a person."}
    result = alone.commit_candidates(
        {"candidates": [{"memory_type": "semantic", "payload": payload, "evidence": [source]}]}, other
    )
    assert result["committed"]


def test_ingest_refuses_an_actor_the_file_does_not_declare(tmp_path: Path, source_file: Path) -> None:
    from vitruvio.kernel import ActorNotDeclaredError

    brain = tmp_path / "brain"
    BrainService(resolve(brain=brain, actor_id="tester@example.com", require_layout=False, declaring=True)).init()

    impostor = BrainService(
        resolve(brain=brain, actor_id="other@example.com", assisted_by=[], require_layout=False, declaring=True)
    )
    source = impostor.register(source_file, media_type="text/markdown")["block_id"]
    task = impostor.define_task(source, allowed=["semantic"])
    with pytest.raises(ActorNotDeclaredError) as caught:
        impostor.commit_candidates(_candidates(source), task)
    assert caught.value.code == "ACTOR_NOT_DECLARED"
    assert "tester@example.com" in str(caught.value)
