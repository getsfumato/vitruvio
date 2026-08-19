"""The five removal mechanisms, and the refusals that make them trustworthy.

The refusals are the point of this file. A removal path that quietly does something adjacent to what was asked --
dropping episodic memory, redacting under a policy that forbids it, destroying bytes another block still needs -- is
worse than one that fails, because nothing downstream can tell.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from vitruvio.cli.main import main
from vitruvio.kernel import ExitCode

DOCUMENT = """# Serie de Fourier

Descompone una funcion periodica en una suma de senos y cosenos.

# Ortogonalidad

Las funciones seno y coseno forman un sistema ortogonal en el intervalo.
"""


def envelope(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    """Invoke the CLI in JSON mode and parse the single object it printed."""
    code = main(["--json", *args])
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def populated(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> tuple[Path, str, list[str]]:
    """A brain holding one canonical source and the two semantic blocks derived from it."""
    path = tmp_path / "brain"
    assert envelope(capsys, "brain", "init", str(path), "--actor", "tester@example.com")[0] == ExitCode.OK

    document = tmp_path / "fourier.md"
    document.write_text(DOCUMENT, encoding="utf-8")
    code, ingested = envelope(capsys, "--brain", str(path), "ingest", "run", str(document))
    assert code == ExitCode.OK

    source = str(ingested["data"]["registration"]["block_id"])
    derived = [str(item) for item in ingested["data"]["committed"]["committed"]]
    assert len(derived) == 2
    return path, source, derived


class TestPolicy:
    def test_the_profile_and_what_it_permits_are_reported(
        self, capsys: pytest.CaptureFixture[str], populated: tuple[Path, str, list[str]]
    ) -> None:
        """Worth reading before anything else here: the policy decides whether a drop is even expressible."""
        brain, _, _ = populated
        code, payload = envelope(capsys, "--brain", str(brain), "retain", "policy")
        assert code == ExitCode.OK
        assert payload["data"]["profile"] == "conservative"
        assert payload["data"]["policy"]["canonical_drop_allowed"] is False


class TestPlanDrop:
    def test_dropping_a_derived_block_takes_nothing_with_it(
        self, capsys: pytest.CaptureFixture[str], populated: tuple[Path, str, list[str]]
    ) -> None:
        brain, _, derived = populated
        code, payload = envelope(
            capsys, "--brain", str(brain), "retain", "plan-drop", derived[0], "--memory-type", "semantic"
        )
        assert code == ExitCode.OK
        assert payload["data"]["size"] == 0

    def test_dropping_the_evidence_takes_everything_derived_from_it(
        self, capsys: pytest.CaptureFixture[str], populated: tuple[Path, str, list[str]]
    ) -> None:
        """The number that makes a drop a decision rather than a keystroke."""
        brain, source, derived = populated
        code, payload = envelope(
            capsys, "--brain", str(brain), "retain", "plan-drop", source, "--memory-type", "canonical"
        )
        assert code == ExitCode.OK
        assert payload["data"]["size"] == len(derived)


class TestDrop:
    def test_a_drop_changes_the_composition_and_the_root(
        self, capsys: pytest.CaptureFixture[str], populated: tuple[Path, str, list[str]]
    ) -> None:
        """Blocks are not mutated -- a new Merkle DAG over the survivors is, so a consumer of the old root is
        unaffected until it pulls."""
        brain, _, derived = populated
        _, before = envelope(capsys, "--brain", str(brain), "inspect", "module", "semantic")

        code, _ = envelope(
            capsys,
            "--brain",
            str(brain),
            "retain",
            "drop",
            derived[0],
            "--memory-type",
            "semantic",
            "--reason",
            "wrong extraction",
            "--yes",
        )
        assert code == ExitCode.OK

        _, after = envelope(capsys, "--brain", str(brain), "inspect", "module", "semantic")
        assert after["data"]["block_count"] == before["data"]["block_count"] - 1
        assert after["data"]["root"] != before["data"]["root"]

    def test_the_brain_still_verifies_after_a_drop(
        self, capsys: pytest.CaptureFixture[str], populated: tuple[Path, str, list[str]]
    ) -> None:
        brain, _, derived = populated
        envelope(capsys, "--brain", str(brain), "retain", "drop", derived[0], "--memory-type", "semantic", "--yes")
        code, payload = envelope(capsys, "--brain", str(brain), "brain", "verify")
        assert code == ExitCode.OK
        assert payload["data"]["verified"] is True

    def test_a_canonical_drop_is_refused_by_the_conservative_policy(
        self, capsys: pytest.CaptureFixture[str], populated: tuple[Path, str, list[str]]
    ) -> None:
        """Exit 6, and it means "the protocol says no" -- not "try again"."""
        brain, source, _ = populated
        code, payload = envelope(
            capsys, "--brain", str(brain), "retain", "drop", source, "--memory-type", "canonical", "--yes"
        )
        assert code == ExitCode.POLICY
        assert payload["ok"] is False

    def test_confirmation_is_required_and_json_mode_has_no_one_to_ask(
        self, capsys: pytest.CaptureFixture[str], populated: tuple[Path, str, list[str]]
    ) -> None:
        """Prompting into a pipe that will never answer, or reading consent from the absence of a terminal, are both
        worse than refusing."""
        brain, _, derived = populated
        code, payload = envelope(
            capsys, "--brain", str(brain), "retain", "drop", derived[0], "--memory-type", "semantic"
        )
        assert code != ExitCode.OK
        assert "--yes" in (payload["error"]["hint"] or "")

        _, module = envelope(capsys, "--brain", str(brain), "inspect", "module", "semantic")
        assert module["data"]["block_count"] == 2, "a refused confirmation must change nothing"

    def test_a_non_interactive_run_without_json_is_a_usage_error_not_a_crash(
        self,
        capsys: pytest.CaptureFixture[str],
        populated: tuple[Path, str, list[str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The guard checked `--json` only, so cron reached `input()` and took an EOFError.

        main's last-resort handler then reported a missing `--yes` as "internal error: EOFError" plus "this is a bug
        in vitruvio -- please report it", and exited 1 -- which this CLI documents as always being our bug.
        """
        brain, _, derived = populated

        class NotATerminal:
            def isatty(self) -> bool:
                return False

            def readline(self) -> str:  # pragma: no cover - reaching this means the guard did not fire
                raise AssertionError("stdin was read despite there being no terminal")

        monkeypatch.setattr(sys, "stdin", NotATerminal())
        code = main(["--brain", str(brain), "retain", "drop", derived[0], "--memory-type", "semantic"])
        streams = capsys.readouterr()

        assert code == ExitCode.USAGE, "a missing --yes is something the caller can fix, not a bug in vitruvio"
        assert "not a terminal" in streams.err
        assert "--yes" in streams.err
        assert "please report it" not in streams.err

    def test_ctrl_d_at_the_prompt_cancels_rather_than_crashing(
        self,
        capsys: pytest.CaptureFixture[str],
        populated: tuple[Path, str, list[str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A person declining and a person pressing Ctrl-D are the same answer."""
        brain, _, derived = populated

        class Terminal:
            def isatty(self) -> bool:
                return True

        monkeypatch.setattr(sys, "stdin", Terminal())
        monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError()))
        code = main(["--brain", str(brain), "retain", "drop", derived[0], "--memory-type", "semantic"])

        assert code != ExitCode.OK
        assert "cancelled" in capsys.readouterr().err


class TestDropProducer:
    def test_everything_one_producer_derived_can_be_dropped_at_once(
        self, capsys: pytest.CaptureFixture[str], populated: tuple[Path, str, list[str]]
    ) -> None:
        """The operation a bad model version needs, and it works only because the producer was recorded at commit
        time rather than inferred afterwards."""
        brain, _, derived = populated
        code, payload = envelope(
            capsys,
            "--brain",
            str(brain),
            "retain",
            "drop-producer",
            "vitruvio-structure",
            "--kind",
            "pipeline",
            "--yes",
        )
        assert code == ExitCode.OK
        assert sum(len(items) for items in payload["data"]["dropped"].values()) == len(derived)

    def test_an_unmatched_producer_warns_rather_than_looking_clean(
        self, capsys: pytest.CaptureFixture[str], populated: tuple[Path, str, list[str]]
    ) -> None:
        """A typo in a producer id looks exactly like a brain that never used it."""
        brain, _, _ = populated
        code, payload = envelope(
            capsys, "--brain", str(brain), "retain", "drop-producer", "typo", "--kind", "model", "--yes"
        )
        assert code == ExitCode.OK
        assert any("typo" in warning for warning in payload["warnings"])


class TestSupersedeAndDemote:
    def test_a_superseded_block_stays_a_member_but_leaves_the_results(
        self, capsys: pytest.CaptureFixture[str], populated: tuple[Path, str, list[str]]
    ) -> None:
        """Membership and accessibility are different questions, and this is the command that separates them."""
        brain, _, derived = populated
        envelope(capsys, "--brain", str(brain), "index", "build")
        _, before = envelope(capsys, "--brain", str(brain), "search", "fourier ortogonal")
        found_before = {match["block_id"] for match in before["data"]["matches"]}
        assert derived[1] in found_before

        code, _ = envelope(
            capsys,
            "--brain",
            str(brain),
            "retain",
            "supersede",
            derived[0],
            "--supersedes",
            derived[1],
            "--memory-type",
            "semantic",
        )
        assert code == ExitCode.OK

        _, module = envelope(capsys, "--brain", str(brain), "inspect", "module", "semantic")
        assert derived[1] in module["data"]["block_ids"], "superseding must not change membership"

        _, after = envelope(capsys, "--brain", str(brain), "search", "fourier ortogonal")
        assert derived[1] not in {match["block_id"] for match in after["data"]["matches"]}

    def test_a_block_cannot_supersede_itself(
        self, capsys: pytest.CaptureFixture[str], populated: tuple[Path, str, list[str]]
    ) -> None:
        brain, _, derived = populated
        code, _ = envelope(
            capsys,
            "--brain",
            str(brain),
            "retain",
            "supersede",
            derived[0],
            "--supersedes",
            derived[0],
            "--memory-type",
            "semantic",
        )
        assert code != ExitCode.OK

    def test_demoting_records_in_the_ledger_and_leaves_the_block_alone(
        self, capsys: pytest.CaptureFixture[str], populated: tuple[Path, str, list[str]]
    ) -> None:
        """Accessibility as a *field* would change the block id, making a demoted block a different block."""
        brain, _, derived = populated
        code, payload = envelope(
            capsys, "--brain", str(brain), "retain", "demote", derived[0], "--memory-type", "semantic"
        )
        assert code == ExitCode.OK
        assert payload["data"]["provenance"], "a demotion nobody recorded is a demotion nobody can audit"

        _, module = envelope(capsys, "--brain", str(brain), "inspect", "module", "semantic")
        assert derived[0] in module["data"]["block_ids"]


class TestPrune:
    def test_prune_is_a_dry_run_by_default(
        self, capsys: pytest.CaptureFixture[str], populated: tuple[Path, str, list[str]]
    ) -> None:
        """The safe direction is the one you can repeat."""
        brain, _, _ = populated
        code, payload = envelope(capsys, "--brain", str(brain), "retain", "prune")
        assert code == ExitCode.OK
        assert payload["data"]["applied"] is False


class TestRedact:
    def test_redaction_is_refused_when_the_policy_names_no_redactable_media_types(
        self, capsys: pytest.CaptureFixture[str], populated: tuple[Path, str, list[str]]
    ) -> None:
        """Refused by default, and the message names the alternatives rather than the flag that would force it.
        Wrong knowledge is dropped; redaction is for data that must not exist."""
        brain, _, derived = populated
        code, payload = envelope(
            capsys,
            "--brain",
            str(brain),
            "retain",
            "redact",
            derived[0],
            "--memory-type",
            "semantic",
            "--reason",
            "personal data",
            "--yes",
        )
        assert code == ExitCode.POLICY
        assert "supersede" in (payload["error"]["hint"] or "")

    def test_a_redacted_block_is_tombstoned_rather_than_missing_and_still_verifies(
        self, capsys: pytest.CaptureFixture[str], populated: tuple[Path, str, list[str]]
    ) -> None:
        """The whole design of redaction: the Merkle DAG references identities, not bytes, so membership still
        verifies while that one block becomes unreconstructable -- and a lawful erasure must never be mistaken for a
        corrupt store."""
        brain, _, derived = populated
        assert (
            envelope(
                capsys,
                "--brain",
                str(brain),
                "config",
                "set",
                "policy.redactable_media_types",
                '["text/markdown"]',
            )[0]
            == ExitCode.OK
        )

        code, _ = envelope(
            capsys,
            "--brain",
            str(brain),
            "retain",
            "redact",
            derived[0],
            "--memory-type",
            "semantic",
            "--reason",
            "personal data",
            "--yes",
        )
        assert code == ExitCode.OK

        _, report = envelope(capsys, "--brain", str(brain), "inspect", "resolvability")
        assert report["data"]["counts"]["tombstoned"]["semantic"] == 1
        assert report["data"]["counts"]["missing"].get("semantic", 0) == 0

        code, verified = envelope(capsys, "--brain", str(brain), "brain", "verify")
        assert code == ExitCode.OK
        assert verified["data"]["verified"] is True


class TestConfigTarget:
    def test_config_set_writes_beside_the_named_brain(
        self, capsys: pytest.CaptureFixture[str], populated: tuple[Path, str, list[str]], tmp_path: Path
    ) -> None:
        """It used to create a new vitruvio.toml in the working directory while `config show` for the same brain read
        a different file. Two commands disagreeing about which file is "the configuration" is its own kind of bug."""
        brain, _, _ = populated
        code, payload = envelope(capsys, "--brain", str(brain), "config", "set", "registry.tag", "v9")
        assert code == ExitCode.OK
        assert Path(payload["data"]["config_file"]) == brain.parent / "vitruvio.toml"

        _, read_back = envelope(capsys, "--brain", str(brain), "config", "get", "registry.tag")
        assert read_back["data"]["value"] == "v9"
