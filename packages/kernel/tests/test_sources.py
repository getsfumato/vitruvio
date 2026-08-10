"""The source declaration, and the two things it refuses.

Both refusals exist because the failure they prevent is unrecoverable rather than annoying. A source that feeds a
brain the project does not declare, or one whose brain contradicts a `--brain` on the command line, Merkle-commits
one subject's material into another brain -- and content addressing has no undo for that. So they are parse-time
errors, not pull-time ones.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from vitruvio.kernel import ProjectConfig, SourceSpec, load_project

PROJECT = """
[project]
name = "facultad"

[brains.algebra]
path = "./algebra"

[brains.fisica]
path = "./fisica"

[sources.arxiv]
kind = "directory"
brain = "algebra"
path = "./papers"
options = { glob = "*.pdf" }
"""


def write(directory: Path, body: str) -> Path:
    """Write a vitruvio.toml and return its path."""
    path = directory / "vitruvio.toml"
    path.write_text(body, encoding="utf-8")
    return path


class TestSchema:
    def test_a_source_is_parsed_with_its_options_untouched(self, tmp_path: Path) -> None:
        config = load_project(write(tmp_path, PROJECT))
        source = config.sources["arxiv"]
        assert source.kind == "directory"
        assert source.brain == "algebra"
        assert source.options == {"glob": "*.pdf"}

    def test_an_unknown_key_is_refused(self) -> None:
        """`extra="forbid"` is what makes a typo'd key an error rather than a setting that silently does nothing."""
        with pytest.raises(ValidationError, match="command"):
            SourceSpec(kind="directory", command="curl example.com")  # type: ignore[call-arg]

    def test_there_is_nowhere_to_put_an_argv(self) -> None:
        """The property the whole design rests on, asserted rather than assumed.

        If a future field made a command line expressible here, cloning a repository and running `source pull`
        would execute a stranger's argv. A test is the cheapest guard against that being added by accident.
        """
        fields = set(SourceSpec.model_fields)
        assert not fields & {"command", "argv", "list", "fetch", "shell", "exec", "script"}

    def test_an_empty_kind_is_refused(self) -> None:
        """Otherwise it resolves to nothing and reports as "unknown kind ''", which reads like a bug in vitruvio."""
        with pytest.raises(ValidationError, match="kind is required"):
            SourceSpec(kind="  ")

    def test_a_source_name_that_would_not_survive_a_command_line_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="Algebra Aula"):
            ProjectConfig(sources={"Algebra Aula": SourceSpec(kind="directory")})

    def test_the_timeout_is_generous_and_bounded(self) -> None:
        """A source is allowed to do real work; the bound exists to kill a hung fetch, not a slow one."""
        assert SourceSpec(kind="directory").timeout == 300
        with pytest.raises(ValidationError):
            SourceSpec(kind="directory", timeout=0)


class TestBrainCrossCheck:
    def test_a_source_feeding_an_undeclared_brain_is_refused(self, tmp_path: Path) -> None:
        body = PROJECT.replace('brain = "algebra"', 'brain = "algbera"')
        with pytest.raises(Exception, match="algbera"):
            load_project(write(tmp_path, body))

    def test_the_error_names_the_brains_that_do_exist(self, tmp_path: Path) -> None:
        """"unknown brain" without the list costs a turn to discover; with it, the typo is visible."""
        body = PROJECT.replace('brain = "algebra"', 'brain = "algbera"')
        with pytest.raises(Exception, match="algebra, fisica"):
            load_project(write(tmp_path, body))

    def test_a_single_brain_project_has_nothing_to_cross_check(self) -> None:
        """`[brain]` names no keys, so a source's `brain` there cannot be validated and must not be rejected."""
        config = ProjectConfig(sources={"papers": SourceSpec(kind="directory", brain="whatever")})
        assert config.sources["papers"].brain == "whatever"


class TestResolution:
    def test_a_path_resolves_against_the_configuration_file(self, tmp_path: Path) -> None:
        """Not against the working directory: a committed config that means something different depending on which
        subdirectory you ran the command from would not be a reproducibility artifact."""
        nested = tmp_path / "repo"
        nested.mkdir()
        config = load_project(write(nested, PROJECT))
        assert config.source_root("arxiv") == (nested / "papers").resolve()

    def test_a_tilde_is_expanded(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            sources={"downloads": SourceSpec(kind="directory", path="~/Downloads/arxiv")},
            source=tmp_path / "vitruvio.toml",
        )
        assert config.source_root("downloads") == (Path.home() / "Downloads" / "arxiv").resolve()

    def test_a_source_without_a_path_has_no_root(self) -> None:
        config = ProjectConfig(sources={"aula": SourceSpec(kind="aulasvirtuales")})
        assert config.source_root("aula") is None
        assert config.source_root("nonexistent") is None

    def test_the_declared_brain_wins_when_nothing_is_requested(self, tmp_path: Path) -> None:
        config = load_project(write(tmp_path, PROJECT))
        assert config.brain_for_source("arxiv") == "algebra"

    def test_a_conflicting_request_is_an_error_and_not_an_override(self, tmp_path: Path) -> None:
        """The decision worth pinning: neither side silently wins. Registering algebra PDFs into the fisica brain
        is the worst available outcome, and a coin flip between two readings is not a resolution."""
        config = load_project(write(tmp_path, PROJECT))
        with pytest.raises(ValueError, match="feeds brain 'algebra'"):
            config.brain_for_source("arxiv", requested="fisica")

    def test_agreeing_with_the_declaration_is_not_a_conflict(self, tmp_path: Path) -> None:
        config = load_project(write(tmp_path, PROJECT))
        assert config.brain_for_source("arxiv", requested="algebra") == "algebra"

    def test_a_request_stands_when_the_source_declares_nothing(self) -> None:
        config = ProjectConfig(sources={"aula": SourceSpec(kind="aulasvirtuales")})
        assert config.brain_for_source("aula", requested="fisica") == "fisica"
