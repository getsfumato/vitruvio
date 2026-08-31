"""Brain selection, configuration loading, and the precedence between them."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from boltzmann.blocks.provenance import ActorKind

from vitruvio.kernel import (
    ActorUnknownError,
    BrainNotFoundError,
    BrainNotSelectedError,
    ConfigError,
    Origin,
    find_config_file,
    load_project,
    read_state,
    remember_brain,
    resolve,
    update_config,
)


def make_brain(root: Path, name: str = "brain") -> Path:
    """Create a minimal OCI layout so that ``is_layout`` recognises it."""
    brain = root / name
    brain.mkdir(parents=True, exist_ok=True)
    (brain / "oci-layout").write_text('{"imageLayoutVersion": "1.0.0"}')
    return brain


def write_config(root: Path, body: str) -> Path:
    """Write a ``vitruvio.toml`` and return its path."""
    path = root / "vitruvio.toml"
    path.write_text(body)
    return path


class TestConfigDiscovery:
    def test_assisting_parties_resolve_from_json_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        brain = make_brain(tmp_path)
        monkeypatch.setenv(
            "VITRUVIO_ASSISTED_BY",
            '[{"id":"anthropic/claude-code","kind":"agent","model":"openai/gpt-5"}]',
        )
        resolved = resolve(brain=brain)
        assert resolved.collaborators()[0].model == "openai/gpt-5"

    def test_assisting_party_flags_override_the_file(self, tmp_path: Path) -> None:
        brain = make_brain(tmp_path)
        config = write_config(
            tmp_path,
            f'brain.path = "{brain}"\n[[assisted_by]]\nid = "old/assistant"\nkind = "agent"\n',
        )
        resolved = resolve(config=config, assisted_by=["new/assistant"])
        assert [item.id for item in resolved.collaborators()] == ["new/assistant"]

    def test_walks_up_to_the_nearest_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        write_config(tmp_path, "")
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)
        assert find_config_file() == tmp_path / "vitruvio.toml"

    def test_stops_at_the_first_hit_rather_than_merging(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        write_config(tmp_path, '[actor]\nid = "outer"\n')
        inner = tmp_path / "inner"
        inner.mkdir()
        write_config(inner, '[actor]\nid = "nearest"\n')
        monkeypatch.chdir(inner)
        assert load_project(find_config_file()).actor.id == "nearest"

    def test_env_override_must_exist(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VITRUVIO_CONFIG", str(tmp_path / "absent.toml"))
        with pytest.raises(ConfigError, match="not a file"):
            find_config_file()

    def test_malformed_toml_names_the_file(self, tmp_path: Path) -> None:
        path = write_config(tmp_path, "this is not = = toml")
        with pytest.raises(ConfigError, match=re.escape(path.name)):
            load_project(path)

    def test_unknown_key_is_rejected_with_its_location(self, tmp_path: Path) -> None:
        path = write_config(tmp_path, '[actor]\nidd = "typo"\n')
        with pytest.raises(ConfigError, match=r"actor\.idd"):
            load_project(path)

    def test_no_file_means_defaults(self) -> None:
        project = load_project(None)
        assert project.actor.id is None
        assert project.text_embedder.provider == "hashing"


class TestBrainPrecedence:
    def test_flag_wins_over_everything(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        chosen = make_brain(tmp_path, "chosen")
        other = make_brain(tmp_path, "other")
        monkeypatch.setenv("VITRUVIO_BRAIN", str(other))
        write_config(tmp_path, f'[brain]\npath = "{other}"\n')
        monkeypatch.chdir(tmp_path)

        resolved = resolve(brain=chosen)
        assert resolved.brain == chosen
        assert resolved.brain_origin is Origin.FLAG

    def test_environment_beats_the_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from_env = make_brain(tmp_path, "from-env")
        from_file = make_brain(tmp_path, "from-file")
        write_config(tmp_path, f'[brain]\npath = "{from_file}"\n')
        monkeypatch.setenv("VITRUVIO_BRAIN", str(from_env))
        monkeypatch.chdir(tmp_path)

        resolved = resolve()
        assert resolved.brain == from_env
        assert resolved.brain_origin is Origin.ENVIRONMENT

    def test_file_path_resolves_against_the_file_not_the_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point of the walk-up: the same config must mean the same brain from any subdirectory."""
        make_brain(tmp_path, "brain")
        write_config(tmp_path, '[brain]\npath = "./brain"\n')
        deep = tmp_path / "notes" / "chapter-3"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)

        resolved = resolve()
        assert resolved.brain == tmp_path / "brain"
        assert resolved.brain_origin is Origin.FILE

    def test_state_is_the_last_resort(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        remembered = make_brain(tmp_path, "remembered")
        remember_brain(remembered)
        monkeypatch.chdir(tmp_path)

        resolved = resolve()
        assert resolved.brain == remembered
        assert resolved.brain_origin is Origin.STATE

    def test_nothing_selected_names_all_four_layers(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(BrainNotSelectedError) as caught:
            resolve()
        hint = caught.value.hint or ""
        assert "--brain" in hint
        assert "VITRUVIO_BRAIN" in hint
        assert "vitruvio.toml" in hint
        assert "brain use" in hint

    def test_a_path_that_is_not_a_layout_is_refused(self, tmp_path: Path) -> None:
        plain = tmp_path / "just-a-directory"
        plain.mkdir()
        with pytest.raises(BrainNotFoundError, match="not an OCI layout"):
            resolve(brain=plain)

    def test_init_may_name_a_path_that_does_not_exist_yet(self, tmp_path: Path) -> None:
        resolved = resolve(brain=tmp_path / "new", require_layout=False)
        assert resolved.brain == tmp_path / "new"

    def test_an_explicit_brain_finds_the_configuration_beside_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`brain init` writes vitruvio.toml next to the brain, so `--brain` from elsewhere must still read it.

        Without this the next command against that brain reads no configuration at all, and the first symptom is a
        write refused for want of an actor that was configured all along.
        """
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        make_brain(project)
        write_config(project, '[brain]\npath = "./brain"\n\n[actor]\nid = "beside@example.com"\n')
        monkeypatch.chdir(elsewhere)

        resolved = resolve(brain=project / "brain")
        assert resolved.config_file == project / "vitruvio.toml"
        assert resolved.project.actor.id == "beside@example.com"

    def test_the_working_directory_still_wins_over_the_brain_s_neighbour(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The second look is a fallback, not a new precedence: cwd is the layer the user is standing in."""
        here = tmp_path / "here"
        here.mkdir()
        write_config(here, '[actor]\nid = "cwd@example.com"\n')
        project = tmp_path / "project"
        project.mkdir()
        make_brain(project)
        write_config(project, '[actor]\nid = "beside@example.com"\n')
        monkeypatch.chdir(here)

        assert resolve(brain=project / "brain").project.actor.id == "cwd@example.com"


class TestActorResolution:
    def test_flag_beats_environment_beats_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        make_brain(tmp_path)
        write_config(tmp_path, '[brain]\npath = "./brain"\n\n[actor]\nid = "file@example.com"\n')
        monkeypatch.chdir(tmp_path)

        assert resolve().project.actor.id == "file@example.com"

        monkeypatch.setenv("VITRUVIO_ACTOR_ID", "env@example.com")
        resolved = resolve()
        assert resolved.project.actor.id == "env@example.com"
        assert resolved.actor_origin is Origin.ENVIRONMENT

        resolved = resolve(actor_id="flag@example.com")
        assert resolved.project.actor.id == "flag@example.com"
        assert resolved.actor_origin is Origin.FLAG

    def test_actor_kind_comes_through_and_defaults_to_human(self, tmp_path: Path) -> None:
        make_brain(tmp_path)
        resolved = resolve(brain=tmp_path / "brain", actor_id="a@b.c")
        assert resolved.actor().kind is ActorKind.HUMAN

        resolved = resolve(brain=tmp_path / "brain", actor_id="a@b.c", actor_kind=ActorKind.AGENT)
        assert resolved.actor().kind is ActorKind.AGENT

    def test_a_bad_actor_kind_in_the_environment_lists_the_valid_ones(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        make_brain(tmp_path)
        monkeypatch.setenv("VITRUVIO_ACTOR_KIND", "robot")
        with pytest.raises(ConfigError, match="pipeline"):
            resolve(brain=tmp_path / "brain")

    def test_writing_without_an_actor_is_refused_rather_than_invented(self, tmp_path: Path) -> None:
        """An unattributed write is a provenance record that lies, which is worse than a failed command."""
        make_brain(tmp_path)
        resolved = resolve(brain=tmp_path / "brain")
        with pytest.raises(ActorUnknownError, match="attributed in provenance"):
            resolved.actor()


class TestState:
    def test_remember_puts_the_newest_first_without_duplicating(self, tmp_path: Path) -> None:
        first = make_brain(tmp_path, "one")
        second = make_brain(tmp_path, "two")
        remember_brain(first)
        remember_brain(second)
        remember_brain(first)

        state = read_state()
        assert state["current"] == str(first)
        assert state["known"] == [str(first), str(second)]

    def test_a_corrupt_state_file_is_ignored_rather_than_fatal(self, tmp_path: Path) -> None:
        from vitruvio.kernel import state_file

        path = state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not [ valid toml")
        assert read_state() == {}


class TestUpdateConfig:
    def test_sets_a_nested_key_and_creates_the_file(self, tmp_path: Path) -> None:
        target = tmp_path / "vitruvio.toml"
        update_config(target, "actor.id", "alex@example.com")
        assert load_project(target).actor.id == "alex@example.com"

    def test_validates_before_writing(self, tmp_path: Path) -> None:
        """A rejected `config set` beats a file the next command refuses to parse."""
        target = write_config(tmp_path, '[actor]\nid = "keep@example.com"\n')
        with pytest.raises(ConfigError, match="would make"):
            update_config(target, "planner.rrf_k", "not-a-number")
        assert load_project(target).actor.id == "keep@example.com"

    def test_none_removes_a_key(self, tmp_path: Path) -> None:
        target = write_config(tmp_path, '[actor]\nid = "gone@example.com"\n')
        update_config(target, "actor.id", None)
        assert load_project(target).actor.id is None

    def test_refuses_to_treat_a_value_as_a_table(self, tmp_path: Path) -> None:
        target = write_config(tmp_path, '[actor]\nid = "a@b.c"\n')
        with pytest.raises(ConfigError, match="not a table"):
            update_config(target, "actor.id.deeper", 1)
