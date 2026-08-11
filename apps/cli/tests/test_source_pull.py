"""The ``source`` group's declarative half, driven the way a user drives it.

The service layer's own tests cover the dedup layers. What is under test here is the part only the CLI can get
wrong: which exit code comes out, and whether a plugin written into a config directory is found at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vitruvio.cli.main import main
from vitruvio.kernel import ExitCode


def envelope(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    """Invoke the CLI in JSON mode and parse the single object it printed."""
    code = main(["--json", *args])
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def project(capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A brain with an `incoming` directory holding two notes, and nothing declared yet."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert envelope(capsys, "--actor", "a@b.c", "brain", "init", "./brain")[0] == ExitCode.OK

    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / "fourier.md").write_text("# Fourier\n\nSenos y cosenos.\n", encoding="utf-8")
    (incoming / "laplace.md").write_text("# Laplace\n\nDe lo diferencial a lo algebraico.\n", encoding="utf-8")
    return tmp_path


class TestTheHappyPath:
    def test_declare_pull_and_pull_again(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        code, added = envelope(
            capsys, "source", "add", "papers", "--kind", "directory", "--path", "./incoming", "--option", "glob=*.md"
        )
        assert code == ExitCode.OK
        assert added["data"]["warning"] is None, "a path inside the project needs no warning"

        code, first = envelope(capsys, "source", "pull", "papers")
        assert code == ExitCode.OK
        assert first["data"]["registered"] == 2

        code, again = envelope(capsys, "source", "pull", "papers")
        assert code == ExitCode.OK
        assert again["data"]["counts"] == {"skipped": 2}

    def test_a_dry_run_registers_nothing(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        envelope(capsys, "source", "add", "papers", "--kind", "directory", "--path", "./incoming")
        code, payload = envelope(capsys, "source", "pull", "papers", "--dry-run")
        assert code == ExitCode.OK
        assert payload["data"]["registered"] == 0

        code, state = envelope(capsys, "brain", "state")
        assert state["data"]["block_count"] == 0

    def test_status_reports_a_declared_source(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        envelope(capsys, "source", "add", "papers", "--kind", "directory", "--path", "./incoming")
        code, payload = envelope(capsys, "source", "status")
        assert code == ExitCode.OK
        row = payload["data"]["sources"][0]
        assert row["name"] == "papers"
        assert row["available"] is True

    def test_kinds_names_the_builtin_and_where_to_write_one(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        code, payload = envelope(capsys, "source", "kinds")
        assert code == ExitCode.OK
        assert [row["kind"] for row in payload["data"]["kinds"]] == ["directory"]
        assert "xdg" in payload["data"]["plugin_dir"]

    def test_remove_undeclares_it(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        envelope(capsys, "source", "add", "papers", "--kind", "directory", "--path", "./incoming")
        assert envelope(capsys, "source", "remove", "papers")[0] == ExitCode.OK
        assert envelope(capsys, "source", "status")[1]["data"]["sources"] == []


class TestOptionParsing:
    def test_a_boolean_option_is_a_boolean(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        """`recursive=false` written through as the string "false" is *true* to `bool()`, and the resulting recursive
        glob nobody asked for is both surprising and expensive."""
        envelope(
            capsys,
            "source",
            "add",
            "papers",
            "--kind",
            "directory",
            "--path",
            "./incoming",
            "--option",
            "recursive=false",
        )
        from vitruvio.kernel import load_project

        assert load_project(project / "vitruvio.toml").sources["papers"].options == {"recursive": False}

    def test_an_option_without_an_equals_sign_is_a_usage_error(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        code = main(["--json", "source", "add", "x", "--kind", "directory", "--option", "glob"])
        assert code == ExitCode.USAGE

    def test_pull_options_override_the_declaration_for_one_invocation(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        envelope(
            capsys,
            "source",
            "add",
            "papers",
            "--kind",
            "directory",
            "--path",
            "./incoming",
            "--option",
            "glob=*.txt",
        )

        code, payload = envelope(capsys, "source", "pull", "papers", "--dry-run", "--option", "glob=*.md")
        assert code == ExitCode.OK
        assert payload["data"]["listed"] == 2
        assert payload["data"]["option_overrides"] == ["glob"]

        from vitruvio.kernel import load_project

        assert load_project(project / "vitruvio.toml").sources["papers"].options == {"glob": "*.txt"}

    def test_a_pull_option_without_an_equals_sign_is_a_usage_error(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        assert main(["--json", "source", "pull", "papers", "--option", "glob"]) == ExitCode.USAGE


class TestPlugins:
    def test_a_scaffolded_plugin_is_found_and_pulled_from(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        """The whole "write your own kind" path through the CLI: scaffold, edit, declare, pull."""
        code, written = envelope(capsys, "source", "scaffold", "aulasvirtuales")
        assert code == ExitCode.OK

        path = Path(written["data"]["path"])
        body = path.read_text(encoding="utf-8")
        body = body.replace(
            'raise NotImplementedError("list the items this source offers")',
            'return [Item(id="4821", origin="aula://77/4821", title="Practica 1", media_type="text/markdown")]',
        )
        body = body.replace(
            'raise NotImplementedError("fetch one item")',
            'return b"# Practica 1\\n\\nEnunciados.\\n"',
        )
        path.write_text(body, encoding="utf-8")

        code, kinds = envelope(capsys, "source", "kinds")
        assert "aulasvirtuales" in {row["kind"] for row in kinds["data"]["kinds"]}

        envelope(capsys, "source", "add", "aula", "--kind", "aulasvirtuales", "--option", "materia=77")
        code, pulled = envelope(capsys, "source", "pull", "aula")
        assert code == ExitCode.OK
        assert pulled["data"]["registered"] == 1

    def test_scaffolding_over_an_existing_file_is_refused(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        """That file is hand-written code, and the one thing here no content address can recover."""
        assert envelope(capsys, "source", "scaffold", "youtube")[0] == ExitCode.OK
        assert main(["--json", "source", "scaffold", "youtube"]) == ExitCode.USAGE
        capsys.readouterr()
        assert envelope(capsys, "source", "scaffold", "youtube", "--force")[0] == ExitCode.OK


class TestExitCodes:
    def test_a_source_that_cannot_be_reached_exits_eleven(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        envelope(capsys, "source", "add", "absent", "--kind", "directory", "--path", "./not-there")
        assert main(["--json", "source", "pull", "absent"]) == ExitCode.SOURCE

    def test_an_undeclared_source_exits_two_and_not_one(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        """Exit 1 is "always a bug in vitruvio", and a typo'd name is not one."""
        assert main(["--json", "source", "pull", "nonexistent"]) == ExitCode.USAGE

    def test_a_name_together_with_all_is_a_usage_error(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        assert main(["--json", "source", "pull", "papers", "--all"]) == ExitCode.USAGE

    def test_an_option_together_with_all_is_a_usage_error(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        assert main(["--json", "source", "pull", "--all", "--option", "course_id=77"]) == ExitCode.USAGE

    def test_neither_a_name_nor_all_is_a_usage_error(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        assert main(["--json", "source", "pull"]) == ExitCode.USAGE

    def test_pull_all_keeps_going_past_a_failure_and_still_exits_eleven(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        envelope(capsys, "source", "add", "papers", "--kind", "directory", "--path", "./incoming")
        envelope(capsys, "source", "add", "absent", "--kind", "directory", "--path", "./not-there")

        code = main(["--json", "source", "pull", "--all"])
        payload = json.loads(capsys.readouterr().out)
        assert code == ExitCode.SOURCE
        assert payload["data"]["registered"] == 2, "the healthy source still registered everything it had"

    def test_pull_all_with_nothing_declared_is_a_configuration_error(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        assert main(["--json", "source", "pull", "--all"]) == ExitCode.CONFIG


class TestTheWarning:
    def test_adding_a_source_outside_the_project_warns(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        """A directory source composes with `dist push` into a way to publish something nobody meant to."""
        code, payload = envelope(capsys, "source", "add", "elsewhere", "--kind", "directory", "--path", "/etc")
        assert code == ExitCode.OK
        assert "outside the project" in payload["data"]["warning"]
