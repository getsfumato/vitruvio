"""The `compound` group: one query, several brains of one project, driven the way an agent drives it.

Assertions are on the parsed JSON envelope; the human rendering gets a few smoke tests. The project holds three
subjects: two share a document, one of them holds a second document, and the third is empty -- the ordinary shape
of a project somebody is still filling in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vitruvio.cli.main import main
from vitruvio.kernel import ExitCode

SHARED = """# Espacio vectorial

Un espacio vectorial sobre un cuerpo K es un conjunto con suma y producto por escalar.

# Base y dimension

Una base es un conjunto linealmente independiente que genera todo el espacio.
"""

ONLY_ALGEBRA = """# Transformacion lineal

Una transformacion lineal preserva la suma y el producto por escalar entre espacios vectoriales.
"""


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    """Invoke the CLI in-process and return its status and streams."""
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def envelope(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    """Invoke the CLI in JSON mode and parse the single object it printed."""
    code, out, _ = run(capsys, "--json", *args)
    return code, json.loads(out)


@pytest.fixture
def project(capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`facultad`: `algebra` and `analisis-ii` share a document, `algebra` holds one more, `fisica-i` is empty."""
    monkeypatch.chdir(tmp_path)
    actor = ("--actor", "a@b.c")
    assert envelope(capsys, *actor, "project", "init", "facultad")[0] == ExitCode.OK
    for name in ("algebra", "analisis-ii", "fisica-i"):
        assert envelope(capsys, *actor, "project", "add", name)[0] == ExitCode.OK

    shared = tmp_path / "shared.md"
    shared.write_text(SHARED, encoding="utf-8")
    only = tmp_path / "only.md"
    only.write_text(ONLY_ALGEBRA, encoding="utf-8")
    for brain, document in (("algebra", shared), ("analisis-ii", shared), ("algebra", only)):
        assert envelope(capsys, *actor, "--brain", brain, "ingest", "run", str(document))[0] == ExitCode.OK
    return tmp_path


class TestGrouped:
    def test_the_default_keeps_each_brains_ranking_and_names_the_brain_on_every_match(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        code, payload = envelope(
            capsys, "compound", "search", "base del espacio", "--brains", "algebra", "--brains", "analisis-ii"
        )
        assert code == ExitCode.OK
        data = payload["data"]
        assert payload["command"] == "compound.search"
        assert data["fused"] is False
        assert data["brains"] == ["algebra", "analisis-ii"]
        assert [member["brain"] for member in data["members"]] == ["algebra", "analisis-ii"]
        assert all(len(match["brains"]) == 1 for match in data["matches"])
        assert all(isinstance(match["score"], str) for match in data["matches"])
        # Brain by brain, in the order given: every algebra match precedes every analisis-ii match.
        origins = [match["brains"][0]["brain"] for match in data["matches"]]
        assert origins == sorted(origins, key=["algebra", "analisis-ii"].index)
        assert "answer" not in data

    def test_roots_stay_per_brain(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        _, payload = envelope(capsys, "compound", "search", "base", "--brains", "algebra,analisis-ii")
        data = payload["data"]
        assert "verified_against" not in data
        roots = [member["verified_against"]["semantic"] for member in data["members"]]
        assert len(roots) == 2
        assert all(root.startswith("sha256:") for root in roots)

    def test_a_shared_block_appears_once_per_brain(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        _, payload = envelope(capsys, "compound", "search", "base del espacio", "--brains", "algebra,analisis-ii")
        blocks = [match["block_id"] for match in payload["data"]["matches"]]
        assert len(blocks) > len(set(blocks)), "the same document was ingested into both brains"


class TestFused:
    def test_a_block_both_brains_hold_is_one_match_and_ranks_first(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        """A block is the hash of its content, so the same paragraph is the same block in both brains -- and rank 1
        in two brains beats rank 1 in one."""
        code, payload = envelope(
            capsys,
            "compound",
            "search",
            "base del espacio",
            "--brains",
            "algebra",
            "--brains",
            "analisis-ii",
            "--fuse",
        )
        assert code == ExitCode.OK
        data = payload["data"]
        assert data["fused"] is True
        blocks = [match["block_id"] for match in data["matches"]]
        assert len(blocks) == len(set(blocks)), "fused output has one match per block"
        top = data["matches"][0]
        assert top["score"] == "1.00"
        assert [origin["brain"] for origin in top["brains"]] == ["algebra", "analisis-ii"]
        assert all(origin["rank"] == 1 for origin in top["brains"])

    def test_a_block_only_one_brain_holds_names_only_that_brain(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        _, payload = envelope(capsys, "compound", "search", "transformacion lineal", "--all", "--fuse")
        exclusive = [match for match in payload["data"]["matches"] if len(match["brains"]) == 1]
        assert exclusive, "the second document went into algebra only"
        assert {match["brains"][0]["brain"] for match in exclusive} == {"algebra"}


class TestSelection:
    def test_a_comma_separated_list_is_the_repeated_flag(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        _, repeated = envelope(capsys, "compound", "search", "base", "--brains", "algebra", "--brains", "analisis-ii")
        _, joined = envelope(capsys, "compound", "search", "base", "--brains", "algebra,analisis-ii")
        assert repeated["data"] == joined["data"]

    def test_all_consults_every_brain_including_an_empty_one(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        code, payload = envelope(capsys, "compound", "search", "base", "--all")
        assert code == ExitCode.OK
        counts = {member["brain"]: member["count"] for member in payload["data"]["members"]}
        assert set(counts) == {"algebra", "analisis-ii", "fisica-i"}
        assert counts["fisica-i"] == 0
        assert payload["data"]["skipped"] == []

    def test_one_brain_is_a_usage_error_pointing_at_search(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        code, payload = envelope(capsys, "compound", "search", "base", "--brains", "algebra")
        assert code == ExitCode.USAGE
        assert payload["error"]["code"] == "USAGE"
        assert "vitruvio search" in payload["error"]["hint"]

    def test_a_path_is_refused_and_the_hint_names_the_project_brains(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        code, payload = envelope(
            capsys, "compound", "search", "base", "--brains", "./brains/algebra", "--brains", "analisis-ii"
        )
        assert code == ExitCode.USAGE
        assert "algebra, analisis-ii, fisica-i" in payload["error"]["hint"]

    def test_a_list_and_all_together_are_refused(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        code, payload = envelope(capsys, "compound", "search", "base", "--brains", "algebra", "--all")
        assert code == ExitCode.USAGE
        assert payload["ok"] is False

    def test_no_selection_at_all_is_refused_with_the_names(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        code, payload = envelope(capsys, "compound", "search", "base")
        assert code == ExitCode.USAGE
        assert "algebra" in payload["error"]["hint"]

    def test_the_global_brain_option_is_ignored_because_a_compound_is_about_the_project(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        code, payload = envelope(capsys, "--brain", "nope", "compound", "search", "base", "--all")
        assert code == ExitCode.OK
        assert payload["data"]["brains"] == ["algebra", "analisis-ii", "fisica-i"]


class TestExplain:
    def test_one_explanation_per_brain(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        code, payload = envelope(capsys, "compound", "explain", "base del espacio", "--all")
        assert code == ExitCode.OK
        assert payload["command"] == "compound.explain"
        members = payload["data"]["members"]
        assert [member["brain"] for member in members] == ["algebra", "analisis-ii", "fisica-i"]
        for member in members:
            assert "chosen" in member["explanation"]
            assert "considered" in member["explanation"]

    def test_explain_refuses_the_same_selections_search_does(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        code, _ = envelope(capsys, "compound", "explain", "base", "--brains", "algebra")
        assert code == ExitCode.USAGE


class TestHumanRendering:
    def test_grouped_output_has_a_section_per_brain(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        code, out, _ = run(capsys, "--no-color", "compound", "search", "base del espacio", "--all")
        assert code == ExitCode.OK
        assert "across" in out
        for name in ("algebra", "analisis-ii", "fisica-i"):
            assert name in out
        assert "not an error" in out, "the empty brain says so rather than vanishing"

    def test_fused_output_names_the_brains_on_each_row(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        code, out, _ = run(capsys, "--no-color", "compound", "search", "base del espacio", "--all", "--fuse")
        assert code == ExitCode.OK
        assert "algebra#1" in out
        assert "analisis-ii#1" in out

    def test_explain_prints_a_tree_per_brain(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        code, out, _ = run(capsys, "--no-color", "compound", "explain", "base", "--brains", "algebra,analisis-ii")
        assert code == ExitCode.OK
        assert out.count("Bundle") == 2


def rendered(parts: list[Any]) -> str:
    """Draw renderables into plain text, the way a terminal would without colour."""
    from rich.console import Console

    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    for part in parts:
        console.print(part)
    return console.export_text()


class TestTheFusedViewIsAsHonestAsTheGroupedOne:
    """Unreachable through the CLI -- a conforming planner drops what it cannot verify -- so the renderer is driven
    directly. The fused view once omitted the warning the grouped view printed."""

    def test_the_fused_view_warns_when_a_member_returned_something_unverified(self) -> None:
        from vitruvio.cli import render

        origin = {"brain": "a", "rank": 1, "score": "1.00", "resolvable": True, "superseded_by": None, "sources": []}
        match = {
            "block_id": "sha256:x",
            "memory_type": "semantic",
            "content": {"label": "x"},
            "score": "1.00",
            "sources": [],
            "verified": False,
            "resolvable": True,
            "superseded_by": None,
            "brains": [origin],
        }
        member = {
            "brain": "a",
            "count": 1,
            "truncated": False,
            "all_verified": False,
            "verified_against": {},
            "plan": None,
        }
        data = {
            "project": "p",
            "brains": ["a", "b"],
            "skipped": [],
            "fused": True,
            "members": [member, {**member, "brain": "b", "count": 0, "all_verified": True}],
            "matches": [match],
            "truncated": False,
            "all_verified": False,
        }
        text = rendered(render.compound(data))
        assert "WARNING: not every match verified" in text
        assert "a#1" in text

    def test_the_grouped_view_still_warns_too(self) -> None:
        from vitruvio.cli import render

        member = {
            "brain": "a",
            "count": 1,
            "truncated": False,
            "all_verified": False,
            "verified_against": {},
            "plan": None,
        }
        match = {
            "block_id": "sha256:x",
            "memory_type": "semantic",
            "content": {},
            "score": "1.00",
            "brains": [{"brain": "a", "rank": 1}],
        }
        data = {"fused": False, "members": [member], "matches": [match], "brains": ["a"], "skipped": []}
        assert "WARNING: not every match verified" in rendered(render.compound(data))
