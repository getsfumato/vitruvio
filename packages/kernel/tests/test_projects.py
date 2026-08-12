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
    ProjectNotKnownError,
    forget_project,
    known_projects,
    load_project,
    register_project,
    remember_brain,
    resolve,
    select_config_file,
    selected_brain,
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
        assert resolved.brain_name == "algebra", "a matching path keeps access to that brain's scoped configuration"

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


class TestTheProjectRegistry:
    """Addressing a project by name, from anywhere.

    The point of the registry is that an invocation can state its whole context -- project and brain -- without
    depending on the working directory. That is what lets three terminals hold three projects at once, so these
    tests are all written from *outside* the projects they select.
    """

    def test_a_registered_project_is_selected_by_name_from_anywhere(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = tmp_path / "facultad"
        project.mkdir()
        config = write(project, PROJECT)
        make_brain(project / "brains", "algebra")
        register_project("facultad", config)

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        resolved = resolve(project="facultad", brain=Path("algebra"))
        assert resolved.config_file == config
        assert resolved.brain == (project / "brains" / "algebra").resolve()
        assert resolved.brain_name == "algebra"
        assert resolved.project_origin is Origin.FLAG

    def test_two_projects_are_addressable_at_the_same_time(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole feature, in one test: two brains of the same *name* in two projects, told apart by project.

        This is the shape that did not work before -- a brain per subject, several projects, several terminals --
        and the failure was silent, because whichever brain a stale pointer named answered for both.
        """
        for name in ("uno", "dos"):
            directory = tmp_path / name
            directory.mkdir()
            write(directory, f'[project]\nname = "{name}"\n\n[brains.metrica-a]\npath = "./brains/metrica-a"\n')
            make_brain(directory / "brains", "metrica-a")
            register_project(name, directory / "vitruvio.toml")

        monkeypatch.chdir(tmp_path)
        first = resolve(project="uno", brain=Path("metrica-a"))
        second = resolve(project="dos", brain=Path("metrica-a"))

        assert first.brain_name == second.brain_name == "metrica-a"
        assert first.brain != second.brain
        assert (first.project_name, second.project_name) == ("uno", "dos")

    def test_the_environment_names_a_project_too(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """How one agent's session, or a container, states its project without passing a flag to every command."""
        project = tmp_path / "facultad"
        project.mkdir()
        register_project("facultad", write(project, PROJECT))
        make_brain(project / "brains", "algebra")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("VITRUVIO_PROJECT", "facultad")

        resolved = resolve(brain=Path("algebra"))
        assert resolved.brain_name == "algebra"
        assert resolved.project_origin is Origin.ENVIRONMENT

    def test_a_named_project_beats_the_walk_up_rather_than_deferring_to_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An agent that named a project and silently got the one it was standing in is the bug this flag ends."""
        named = tmp_path / "named"
        named.mkdir()
        register_project("named", write(named, '[project]\nname = "named"\n\n[brains.b]\npath = "./b"\n'))
        make_brain(named, "b")

        standing = tmp_path / "standing"
        standing.mkdir()
        write(standing, '[project]\nname = "standing"\n\n[brains.b]\npath = "./b"\n')
        make_brain(standing, "b")
        monkeypatch.chdir(standing)

        assert resolve(project="named").brain == (named / "b").resolve()

    def test_an_unregistered_name_lists_what_is_registered(self, tmp_path: Path) -> None:
        register_project("facultad", write(tmp_path, PROJECT))
        with pytest.raises(ProjectNotKnownError, match="facultad") as caught:
            resolve(project="eticompass")
        assert "project register" in (caught.value.hint or "")

    def test_a_project_whose_file_moved_says_so_rather_than_falling_back(self, tmp_path: Path) -> None:
        config = write(tmp_path, PROJECT)
        register_project("facultad", config)
        config.unlink()
        with pytest.raises(ProjectNotKnownError, match="no longer exists"):
            select_config_file(project="facultad")

    def test_forgetting_is_registry_only_and_reports_whether_it_did_anything(self, tmp_path: Path) -> None:
        config = write(tmp_path, PROJECT)
        register_project("facultad", config)
        assert forget_project("facultad") is True
        assert forget_project("facultad") is False, "a second forget is not an error, it is a no-op"
        assert known_projects() == {}
        assert config.is_file(), "forgetting a project must not touch a single file it describes"


class TestTheSavedSelectionIsPerProject:
    """``brain use`` is scoped to a project, and a project with brains never resolves from the global pointer.

    Both halves matter and the second is the bug: a pointer left by ``brain use`` in one project used to answer
    for a *different* project whose brains were all addressed by name, so a write could land in another
    subject's brain with nobody informed -- and content addressing has no undo for that.
    """

    def test_a_choice_in_one_project_does_not_reach_another(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        facultad = tmp_path / "facultad"
        facultad.mkdir()
        write(facultad, PROJECT)
        make_brain(facultad / "brains", "algebra")
        make_brain(facultad / "brains", "analisis-ii")

        other = tmp_path / "eticompass"
        other.mkdir()
        write(
            other,
            '[project]\nname = "eticompass"\n\n[brains.metrica-a]\npath = "./brains/metrica-a"\n'
            '\n[brains.metrica-b]\npath = "./brains/metrica-b"\n',
        )
        make_brain(other / "brains", "metrica-a")
        make_brain(other / "brains", "metrica-b")

        remember_brain(facultad / "brains" / "algebra", project="facultad", name="algebra")

        monkeypatch.chdir(facultad)
        chosen = resolve()
        assert chosen.brain_name == "algebra"
        assert chosen.brain_origin is Origin.STATE

        monkeypatch.chdir(other)
        with pytest.raises(BrainNotSelectedError) as caught:
            resolve()
        assert "metrica-a" in (caught.value.hint or ""), "the other project asks by its own names"

    def test_a_project_with_brains_never_resolves_from_the_machine_wide_pointer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loose = make_brain(tmp_path, "loose")
        remember_brain(loose)

        project = tmp_path / "facultad"
        project.mkdir()
        write(project, PROJECT)
        make_brain(project / "brains", "algebra")
        make_brain(project / "brains", "analisis-ii")
        monkeypatch.chdir(project)

        with pytest.raises(BrainNotSelectedError):
            resolve()

    def test_a_brain_in_no_project_still_uses_the_machine_wide_pointer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one case a single pointer was always right for, and it keeps working."""
        loose = make_brain(tmp_path, "loose")
        remember_brain(loose)
        monkeypatch.chdir(tmp_path)

        resolved = resolve()
        assert resolved.brain == loose.resolve()
        assert resolved.brain_origin is Origin.STATE

    def test_a_saved_choice_that_left_the_project_is_treated_as_no_choice(self, tmp_path: Path) -> None:
        config = write(tmp_path, PROJECT)
        remember_brain(tmp_path / "brains" / "fisica", project="facultad", name="fisica")
        assert selected_brain(load_project(config)) is None


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
