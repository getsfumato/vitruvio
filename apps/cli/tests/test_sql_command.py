"""``vitruvio sql``, driven the way an agent drives it: assertions on the JSON envelope, a smoke test for the rest."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from vitruvio.cli.main import main
from vitruvio.kernel import ExitCode


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def envelope(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    code, out, _ = run(capsys, "--json", *args)
    return code, json.loads(out)


@pytest.fixture
def brain(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> Path:
    """A brain holding two registered sources, one markdown and one plain text."""
    path = tmp_path / "brain"
    code, _ = envelope(capsys, "brain", "init", str(path), "--actor", "tester@example.com")
    assert code == ExitCode.OK
    for name, media_type in (("fourier.md", "text/markdown"), ("notes.txt", "text/plain")):
        source = tmp_path / name
        source.write_text(f"# {name}\n\nUna serie de Fourier descompone una funcion periodica.\n", encoding="utf-8")
        code, payload = envelope(
            capsys, "--brain", str(path), "source", "register", str(source), "--media-type", media_type
        )
        assert code == ExitCode.OK, payload
    return path


class TestSqlCommand:
    def test_a_count_comes_back_in_the_envelope(self, capsys: pytest.CaptureFixture[str], brain: Path) -> None:
        code, payload = envelope(
            capsys, "--brain", str(brain), "sql", "SELECT media_type, count(*) AS n FROM canonical GROUP BY ALL"
        )
        assert code == ExitCode.OK, payload
        assert payload["command"] == "sql"
        assert payload["data"]["rows"] == [["text/markdown", 1], ["text/plain", 1]]
        assert set(payload["data"]["verified_against"]) == {"canonical", "provenance"}

    def test_the_query_can_come_from_stdin(
        self, capsys: pytest.CaptureFixture[str], brain: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("SELECT count(*) FROM canonical"))
        code, payload = envelope(capsys, "--brain", str(brain), "sql", "--file", "-")
        assert code == ExitCode.OK, payload
        assert payload["data"]["rows"] == [[2]]

    def test_a_write_is_a_usage_error(self, capsys: pytest.CaptureFixture[str], brain: Path) -> None:
        code, payload = envelope(capsys, "--brain", str(brain), "sql", "DELETE FROM canonical")
        assert code == ExitCode.USAGE
        assert "DELETE" in payload["error"]["message"]

    def test_truncation_is_warned_in_the_envelope(self, capsys: pytest.CaptureFixture[str], brain: Path) -> None:
        code, payload = envelope(capsys, "--brain", str(brain), "sql", "SELECT id FROM canonical", "--limit", "1")
        assert code == ExitCode.OK
        assert payload["data"]["truncated"]
        assert any("truncated" in warning for warning in payload["warnings"])

    def test_explain_returns_the_plan(self, capsys: pytest.CaptureFixture[str], brain: Path) -> None:
        code, payload = envelope(capsys, "--brain", str(brain), "sql", "SELECT count(*) FROM canonical", "--explain")
        assert code == ExitCode.OK
        assert payload["data"]["plan"]
        assert payload["data"]["rows"] == []

    def test_schema_lists_the_tables(self, capsys: pytest.CaptureFixture[str], brain: Path) -> None:
        code, payload = envelope(capsys, "--brain", str(brain), "sql", "--schema")
        assert code == ExitCode.OK
        assert "semantic" in [table["name"] for table in payload["data"]["tables"]]

    def test_no_query_is_a_usage_error_pointing_at_the_schema(
        self, capsys: pytest.CaptureFixture[str], brain: Path
    ) -> None:
        code, payload = envelope(capsys, "--brain", str(brain), "sql")
        assert code == ExitCode.USAGE
        assert "--schema" in payload["error"]["hint"]

    def test_a_query_and_a_file_together_are_refused(
        self, capsys: pytest.CaptureFixture[str], brain: Path, tmp_path: Path
    ) -> None:
        query = tmp_path / "q.sql"
        query.write_text("SELECT 1", encoding="utf-8")
        code, _ = envelope(capsys, "--brain", str(brain), "sql", "SELECT 1", "--file", str(query))
        assert code == ExitCode.USAGE

    def test_the_human_view_prints_the_rows_and_the_roots(
        self, capsys: pytest.CaptureFixture[str], brain: Path
    ) -> None:
        code, out, _ = run(capsys, "--brain", str(brain), "sql", "SELECT media_type FROM canonical")
        assert code == ExitCode.OK
        assert "text/markdown" in out
        assert "2 rows" in out
        assert "canonical" in out

    def test_the_human_schema_and_explain_render(self, capsys: pytest.CaptureFixture[str], brain: Path) -> None:
        code, out, _ = run(capsys, "--brain", str(brain), "sql", "--schema")
        assert code == ExitCode.OK
        assert "occurred_at" in out
        code, out, _ = run(capsys, "--brain", str(brain), "sql", "SELECT count(*) FROM canonical", "--explain")
        assert code == ExitCode.OK
        assert "executed" in out
