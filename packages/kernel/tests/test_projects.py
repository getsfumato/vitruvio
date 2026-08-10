"""Projects: several brains under one configuration, and where each one publishes.

The two things worth pinning here are the ones that would fail silently: selecting the wrong brain when a name and
a directory collide, and deriving a repository that two projects would share.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vitruvio.kernel import (
    BrainNotSelectedError,
    ConfigError,
    Origin,
    ProjectConfig,
    load_project,
    resolve,
)


def write(directory: Path, body: str) -> Path:
    """Write a vitruvio.toml and return its path."""
    path = directory / "vitruvio.toml"
    path.write_text(body, encoding="utf-8")
    return path


def make_brain(directory: Path, name: str) -> Path:
    """A minimal OCI layout, which is all `is_layout` looks for."""
    path = directory / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "oci-layout").write_text('{"imageLayoutVersion": "1.0.0"}', encoding="utf-8")
    (path / "index.json").write_text('{"schemaVersion": 2, "manifests": []}', encoding="utf-8")
    return path


PROJECT = """
[project]
name = "facultad"

[registry]
namespace = "docker.io/alex"

[brains.algebra]
path = "./brains/algebra"

[brains.analisis-ii]
path = "./brains/analisis-ii"
description = "apuntes"
"""


class TestSchema:
    def test_a_project_holds_several_named_brains(self, tmp_path: Path) -> None:
        project = load_project(write(tmp_path, PROJECT))
        assert set(project.brains) == {"algebra", "analisis-ii"}
        assert project.brains["analisis-ii"].description == "apuntes"

    def test_a_brain_path_resolves_against_the_config_file(self, tmp_path: Path) -> None:
        """Not against the working directory, for the same reason the single-brain path does not: a project that
        means something different depending on where you stood is not a reproducibility artifact."""
        project = load_project(write(tmp_path, PROJECT))
        assert project.brain_path("algebra") == (tmp_path / "brains" / "algebra").resolve()

    def test_a_name_that_cannot_be_a_repository_is_refused_at_load(self, tmp_path: Path) -> None:
        """Refused here rather than at push time: a registry rejecting `Álgebra II` after the artifact is packed
        gives an error that says nothing about which of the two names was wrong."""
        with pytest.raises(ConfigError, match="repository name"):
            load_project(write(tmp_path, '[brains."Álgebra II"]\npath = "./x"\n'))

    def test_a_project_name_that_cannot_be_a_repository_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="repository"):
            load_project(write(tmp_path, '[project]\nname = "Facultad 2026"\n'))

    def test_a_namespace_that_is_really_a_repository_is_refused(self, tmp_path: Path) -> None:
        """Three segments means a repository was pasted where an account belongs, and every derived repository
        would then carry a stray path component."""
        with pytest.raises(ConfigError, match="looks like a repository"):
            load_project(write(tmp_path, '[registry]\nnamespace = "docker.io/alex/brains"\n'))


class TestDerivedRepository:
    def test_the_project_name_prefixes_every_repository(self, tmp_path: Path) -> None:
        """Without the prefix, two projects that each hold a brain called `notes` publish to one repository and
        overwrite each other -- and the second one finds out when a pull returns the wrong subject."""
        project = load_project(write(tmp_path, PROJECT))
        assert project.repository_for("algebra") == "docker.io/alex/facultad-algebra"
        assert project.repository_for("analisis-ii") == "docker.io/alex/facultad-analisis-ii"

    def test_an_explicit_reference_wins_over_the_derived_one(self, tmp_path: Path) -> None:
        body = PROJECT + '\n[brains.fisica-i]\npath = "./b"\nreference = "ghcr.io/alex/fisica"\n'
        project = load_project(write(tmp_path, body))
        assert project.repository_for("fisica-i") == "ghcr.io/alex/fisica"

    def test_the_account_supplies_a_namespace_when_none_is_configured(self, tmp_path: Path) -> None:
        """This is what makes `registry login --from-docker` enough for a whole project."""
        body = '[project]\nname = "facultad"\n\n[brains.algebra]\npath = "./a"\n'
        project = load_project(write(tmp_path, body))
        assert project.repository_for("algebra") is None
        assert project.repository_for("algebra", account="alex") == "docker.io/alex/facultad-algebra"

    def test_a_project_with_no_name_derives_from_the_brain_alone(self, tmp_path: Path) -> None:
        body = '[registry]\nnamespace = "docker.io/alex"\n\n[brains.notes]\npath = "./a"\n'
        project = load_project(write(tmp_path, body))
        assert project.repository_for("notes") == "docker.io/alex/notes"

    def test_an_unknown_brain_derives_nothing_rather_than_guessing(self, tmp_path: Path) -> None:
        project = load_project(write(tmp_path, PROJECT))
        assert project.brain_path("telepatia") is None


class TestSelection:
    def test_a_brain_is_selected_by_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        write(tmp_path, PROJECT)
        make_brain(tmp_path / "brains", "algebra")
        monkeypatch.chdir(tmp_path)

        resolved = resolve(brain=Path("algebra"))
        assert resolved.brain == (tmp_path / "brains" / "algebra").resolve()
        assert resolved.brain_name == "algebra"
        assert resolved.brain_origin is Origin.FLAG

    def test_a_name_beats_a_directory_of_the_same_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Within a project the names are the vocabulary. A stray directory in the working tree that happens to
        share a name must not shadow the member -- silently operating on the wrong brain is the failure this
        ordering exists to prevent."""
        write(tmp_path, PROJECT)
        member = make_brain(tmp_path / "brains", "algebra")
        decoy = make_brain(tmp_path, "algebra")
        monkeypatch.chdir(tmp_path)

        resolved = resolve(brain=Path("algebra"))
        assert resolved.brain == member.resolve()
        assert resolved.brain != decoy.resolve()

    def test_a_path_still_works(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        write(tmp_path, PROJECT)
        make_brain(tmp_path / "brains", "algebra")
        monkeypatch.chdir(tmp_path)

        resolved = resolve(brain=tmp_path / "brains" / "algebra")
        assert resolved.brain_name is None, "a path selects a brain without claiming it is a project member"

    def test_the_environment_selects_by_name_too(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """How a container or a CI job picks a subject without editing a file."""
        write(tmp_path, PROJECT)
        make_brain(tmp_path / "brains", "algebra")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("VITRUVIO_BRAIN", "algebra")

        assert resolve().brain_name == "algebra"

    def test_a_project_with_one_brain_needs_no_flag(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """There is no ambiguity to resolve, so requiring --brain would be ceremony."""
        write(tmp_path, '[project]\nname = "p"\n\n[brains.only]\npath = "./brains/only"\n')
        make_brain(tmp_path / "brains", "only")
        monkeypatch.chdir(tmp_path)

        resolved = resolve()
        assert resolved.brain_name == "only"

    def test_several_brains_and_no_flag_asks_by_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """With two or more it is a real question, so the error asks it rather than picking one."""
        write(tmp_path, PROJECT)
        make_brain(tmp_path / "brains", "algebra")
        make_brain(tmp_path / "brains", "analisis-ii")
        monkeypatch.chdir(tmp_path)

        with pytest.raises(BrainNotSelectedError) as caught:
            resolve()
        assert "algebra" in (caught.value.hint or "")
        assert "analisis-ii" in (caught.value.hint or "")

    def test_project_commands_may_resolve_with_no_brain_at_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A project that holds no brains yet is the state `project show` most needs to report."""
        write(tmp_path, '[project]\nname = "empty"\n')
        monkeypatch.chdir(tmp_path)

        resolved = resolve(require_brain=False)
        assert resolved.brain_name is None
        assert resolved.project.project.name == "empty"


class TestResolvedRepository:
    def test_a_named_brain_reports_its_derived_repository(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write(tmp_path, PROJECT)
        make_brain(tmp_path / "brains", "algebra")
        monkeypatch.chdir(tmp_path)

        assert resolve(brain=Path("algebra")).repository() == "docker.io/alex/facultad-algebra"

    def test_an_unnamed_brain_falls_back_to_the_single_reference(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The single-brain form still works, and is what a project of one should keep using."""
        write(tmp_path, '[brain]\npath = "./b"\n\n[registry]\nreference = "docker.io/alex/one"\n')
        make_brain(tmp_path, "b")
        monkeypatch.chdir(tmp_path)

        resolved = resolve()
        assert resolved.brain_name is None
        assert resolved.repository() == "docker.io/alex/one"


class TestDefaults:
    def test_a_configuration_with_no_project_section_still_loads(self) -> None:
        """Every section is optional, and a brain with no configuration at all must still open."""
        project = ProjectConfig()
        assert project.project.name is None
        assert project.brains == {}


class TestPublishProhibition:
    """`publish = false` on a brain, which exists to stop one specific silent mistake.

    A pulled brain is a working copy like any other -- nothing in the protocol distinguishes a brain you authored
    from one you installed -- so a stray `dist push` publishes a fork of somebody else's brain under whichever
    repository this project derives, and the two lineages diverge with nobody told.
    """

    def test_a_brain_is_publishable_unless_it_says_otherwise(self, tmp_path: Path) -> None:
        config = load_project(write(tmp_path, PROJECT))
        assert config.brains["algebra"].publish is True

    def test_a_brain_can_declare_itself_unpublishable(self, tmp_path: Path) -> None:
        body = PROJECT.replace("[brains.algebra]", "[brains.algebra]\npublish = false")
        config = load_project(write(tmp_path, body))
        assert config.brains["algebra"].publish is False

    def test_the_resolved_config_answers_for_the_selected_brain(self, tmp_path: Path) -> None:
        """Read through `publish_allowed`, because that is what the service consults -- asserting on the spec alone
        would leave the wiring between them untested."""
        make_brain(tmp_path, "brains/algebra")
        make_brain(tmp_path, "brains/analisis-ii")
        body = PROJECT.replace("[brains.algebra]", "[brains.algebra]\npublish = false")
        config_file = write(tmp_path, body)

        algebra = resolve(brain=Path("algebra"), config=config_file)
        assert algebra.publish_allowed is False

        other = resolve(brain=Path("analisis-ii"), config=config_file)
        assert other.publish_allowed is True

    def test_a_single_brain_project_can_say_it_too(self, tmp_path: Path) -> None:
        """Otherwise whether you can protect a brain would depend on whether the project happens to have a second
        one, which is not a distinction anybody would predict."""
        make_brain(tmp_path, "brain")
        config_file = write(tmp_path, '[actor]\nid = "t@e.c"\n\n[brain]\npath = "./brain"\npublish = false\n')
        assert resolve(config=config_file).publish_allowed is False
