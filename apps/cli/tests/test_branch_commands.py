"""``vitruvio branch`` at the command boundary: exit codes and envelope shape.

The behaviour is covered in `packages/runtime/tests/test_branches.py`. What this file holds is what only the CLI can
get wrong: the status a caller branches on and the keys it reads -- above all that a merge which stops to ask exits
12 rather than 0, the regression `reconcile` already had once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_reconcile_commands import add_evidence, derive, envelope, make

from vitruvio.kernel import ExitCode, resolve
from vitruvio.runtime import BrainService


@pytest.fixture
def brain(tmp_path: Path) -> tuple[Path, BrainService]:
    """One brain with one fact on main. Returns its config and a service over it."""
    config = make(tmp_path, "ana", reconcile=None)
    service = BrainService(resolve(brain=tmp_path / "ana" / "brain", config=config))
    shared = add_evidence(service, "# Fourier\n\nSenos y cosenos.\n", "fourier.md")
    derive(service, shared, "Serie de Fourier")
    return config, service


def cli(capsys: pytest.CaptureFixture[str], config: Path, *args: str) -> tuple[int, dict[str, Any]]:
    return envelope(capsys, "--config", str(config), *args)


def fresh(config: Path) -> BrainService:
    """A service opened now. One opened before a CLI `branch switch` holds the head it had then, and would commit
    onto it -- the same staleness any two processes sharing a brain have."""
    return BrainService(resolve(brain=config.parent / "brain", config=config))


class TestTheLoop:
    def test_create_switch_list_current(self, capsys: pytest.CaptureFixture[str], brain: tuple[Path, Any]) -> None:
        config, _ = brain
        code, created = cli(capsys, config, "branch", "create", "ana/nyquist", "--switch")
        assert code == ExitCode.OK
        assert created["data"]["tag"] == "br.ana.nyquist"
        assert created["data"]["current"] is True

        code, current = cli(capsys, config, "branch", "current")
        assert (code, current["data"]["branch"]) == (ExitCode.OK, "ana/nyquist")

        code, switched = cli(capsys, config, "branch", "switch", "main")
        assert (code, switched["data"]["outcome"]) == (ExitCode.OK, "switched")

        code, listed = cli(capsys, config, "branch", "list")
        assert code == ExitCode.OK
        assert [row["name"] for row in listed["data"]["branches"]] == ["main", "ana/nyquist"]

    def test_a_push_on_a_branch_goes_to_its_tag(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, brain: tuple[Path, Any]
    ) -> None:
        config, _ = brain
        registry = tmp_path / "registry"
        registry.mkdir()
        cli(capsys, config, "dist", "push", "demo/brain", "--local", str(registry))
        cli(capsys, config, "branch", "switch", "-c", "x")
        code, pushed = cli(capsys, config, "dist", "push", "demo/brain", "--local", str(registry))
        assert (code, pushed["data"]["tag"], pushed["data"]["branch"]) == (ExitCode.OK, "br.x", "x")

        code, tags = cli(capsys, config, "dist", "tags", "demo/brain", "--local", str(registry))
        assert code == ExitCode.OK
        assert {entry["tag"]: entry["kind"] for entry in tags["data"]["entries"]} == {
            "latest": "default",
            "br.x": "branch",
        }


class TestExitCodes:
    def test_an_unknown_branch_exits_4(self, capsys: pytest.CaptureFixture[str], brain: tuple[Path, Any]) -> None:
        config, _ = brain
        code, payload = cli(capsys, config, "branch", "switch", "nowhere", "--no-track")
        assert code == ExitCode.NOT_FOUND
        assert payload["error"]["code"] == "BRANCH_NOT_FOUND"

    def test_an_invalid_name_exits_2(self, capsys: pytest.CaptureFixture[str], brain: tuple[Path, Any]) -> None:
        config, _ = brain
        code, payload = cli(capsys, config, "branch", "create", "not.valid")
        assert code == ExitCode.USAGE
        assert payload["error"]["code"] == "BRANCH_NAME_INVALID"

    def test_contradictory_fast_forward_flags_exit_2(
        self, capsys: pytest.CaptureFixture[str], brain: tuple[Path, Any]
    ) -> None:
        config, _ = brain
        cli(capsys, config, "branch", "create", "x")
        code, _ = cli(capsys, config, "branch", "merge", "x", "--ff-only", "--no-ff")
        assert code == ExitCode.USAGE

    def test_a_diverged_merge_without_a_strategy_exits_2_and_with_one_reconciles(
        self, capsys: pytest.CaptureFixture[str], brain: tuple[Path, Any]
    ) -> None:
        config, _ = brain
        cli(capsys, config, "branch", "switch", "-c", "x")
        on_x = fresh(config)
        derive(on_x, add_evidence(on_x, "# Nyquist\n\nMuestreo.\n", "nyquist.md"), "Teorema de Nyquist")
        cli(capsys, config, "branch", "switch", "main")
        on_main = fresh(config)
        derive(on_main, add_evidence(on_main, "# Laplace\n\nTransformada.\n", "laplace.md"), "Laplace")

        code, refused = cli(capsys, config, "branch", "merge", "x")
        assert code == ExitCode.USAGE
        assert "--strategy" in refused["error"]["hint"]

        code, merged = cli(capsys, config, "branch", "merge", "x", "--strategy", "merge", "--reason", "join x")
        assert code == ExitCode.OK
        assert merged["data"]["outcome"] == "reconciled"

    def test_a_merge_that_stops_to_ask_exits_12_and_switching_is_then_refused(
        self, capsys: pytest.CaptureFixture[str], brain: tuple[Path, Any]
    ) -> None:
        """The shape that halts: the branch derives from evidence main then drops."""
        config, service = brain
        source = add_evidence(service, "# Dirichlet\n\nNucleo.\n", "dirichlet.md")
        cli(capsys, config, "branch", "switch", "-c", "x")
        derive(fresh(config), source, "Nucleo de Dirichlet")
        cli(capsys, config, "branch", "switch", "main")
        fresh(config).drop([source], memory_type="canonical", reason="bad scan")

        code, halted = cli(capsys, config, "branch", "merge", "x", "--strategy", "merge")
        assert code == ExitCode.RECONCILE, halted.get("error")
        assert halted["data"]["outcome"] == "halted"
        assert halted["data"]["reconciliation"]["unresolved"]

        code, refused = cli(capsys, config, "branch", "switch", "x")
        assert code == ExitCode.RECONCILE
        assert refused["error"]["code"] == "RECONCILE_OPEN"
