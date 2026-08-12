"""Source declarations belong to brains, so acquisition configuration cannot leak across subjects."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from vitruvio.kernel import BrainSpec, NamedBrainSpec, ProjectConfig, SourceSpec, load_project

PROJECT = """
[project]
name = "facultad"

[brains.algebra]
path = "./algebra"

[brains.algebra.sources.arxiv]
kind = "directory"
path = "./papers/algebra"
options = { glob = "*.pdf" }

[brains.fisica]
path = "./fisica"

[brains.fisica.sources.arxiv]
kind = "directory"
path = "./papers/fisica"
options = { glob = "*.md" }
"""


def write(directory: Path, body: str) -> Path:
    """Write a vitruvio.toml and return its path."""
    path = directory / "vitruvio.toml"
    path.write_text(body, encoding="utf-8")
    return path


class TestSchema:
    def test_each_brain_parses_its_own_source_options(self, tmp_path: Path) -> None:
        config = load_project(write(tmp_path, PROJECT))
        assert config.sources_for("algebra")["arxiv"].options == {"glob": "*.pdf"}
        assert config.sources_for("fisica")["arxiv"].options == {"glob": "*.md"}

    def test_project_scoped_sources_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="sources"):
            ProjectConfig.model_validate({"sources": {"papers": {"kind": "directory"}}})

    def test_a_source_cannot_name_a_brain(self) -> None:
        """Containment in the brain table replaces the old, contradictory destination field."""
        with pytest.raises(ValidationError, match="brain"):
            SourceSpec.model_validate({"kind": "directory", "brain": "algebra"})

    def test_an_unknown_key_is_refused(self) -> None:
        """A typo is an error rather than a setting that silently does nothing."""
        with pytest.raises(ValidationError, match="command"):
            SourceSpec(kind="directory", command="curl example.com")  # type: ignore[call-arg]

    def test_there_is_nowhere_to_put_an_argv(self) -> None:
        """Cloning a repository and pulling a source must not execute an argv declared by that repository."""
        fields = set(SourceSpec.model_fields)
        assert not fields & {"command", "argv", "list", "fetch", "shell", "exec", "script"}

    def test_an_empty_kind_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="kind is required"):
            SourceSpec(kind="  ")

    def test_a_single_brain_source_name_that_would_not_survive_a_command_line_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="Algebra Aula"):
            BrainSpec(sources={"Algebra Aula": SourceSpec(kind="directory")})

    def test_a_named_brain_source_name_that_would_not_survive_a_command_line_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="Algebra Aula"):
            NamedBrainSpec(path="./algebra", sources={"Algebra Aula": SourceSpec(kind="directory")})

    def test_the_timeout_is_generous_and_bounded(self) -> None:
        assert SourceSpec(kind="directory").timeout == 300
        with pytest.raises(ValidationError):
            SourceSpec(kind="directory", timeout=0)


class TestResolution:
    def test_paths_resolve_per_brain_against_the_configuration_file(self, tmp_path: Path) -> None:
        nested = tmp_path / "repo"
        nested.mkdir()
        config = load_project(write(nested, PROJECT))
        assert config.source_root("arxiv", brain="algebra") == (nested / "papers" / "algebra").resolve()
        assert config.source_root("arxiv", brain="fisica") == (nested / "papers" / "fisica").resolve()

    def test_a_tilde_is_expanded(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            brain=BrainSpec(sources={"downloads": SourceSpec(kind="directory", path="~/Downloads/arxiv")}),
            source=tmp_path / "vitruvio.toml",
        )
        assert config.source_root("downloads", brain=None) == (Path.home() / "Downloads" / "arxiv").resolve()

    def test_a_source_without_a_path_has_no_root(self) -> None:
        config = ProjectConfig(brain=BrainSpec(sources={"aula": SourceSpec(kind="aulasvirtuales")}))
        assert config.source_root("aula", brain=None) is None
        assert config.source_root("nonexistent", brain=None) is None

    def test_the_same_source_name_may_have_different_options_per_brain(self, tmp_path: Path) -> None:
        config = load_project(write(tmp_path, PROJECT))
        assert config.sources_for("algebra")["arxiv"] != config.sources_for("fisica")["arxiv"]
