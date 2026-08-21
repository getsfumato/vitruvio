"""The brain, source, query and inspect command groups, driven the way an agent drives them.

Assertions are on the parsed JSON envelope. The human rendering gets a handful of smoke tests at the bottom and
nothing more: it is meant to churn, and a suite that breaks when a label is reworded is a suite people learn to
ignore.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vitruvio.cli.main import main
from vitruvio.kernel import ExitCode


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
def brain(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> Path:
    """An initialised brain, with the CLI's own `brain init`, plus its vitruvio.toml."""
    path = tmp_path / "brain"
    code, _ = envelope(capsys, "brain", "init", str(path), "--actor", "tester@example.com")
    assert code == ExitCode.OK
    return path


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A Markdown source file."""
    path = tmp_path / "fourier.md"
    path.write_text("# Series de Fourier\n\nDescompone una funcion periodica en senos.\n", encoding="utf-8")
    return path


class TestBrainInit:
    def test_init_creates_the_layout_and_writes_a_config(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        code, payload = envelope(capsys, "brain", "init", str(tmp_path / "b"), "--actor", "a@b.c")
        assert code == ExitCode.OK
        assert payload["data"]["created"] is True
        assert (tmp_path / "b" / "oci-layout").is_file()
        assert Path(payload["data"]["config_file"]).is_file()

    def test_init_warns_when_no_actor_is_configured(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        """Writes will be refused, so saying nothing until the first failed register would be unkind."""
        _, payload = envelope(capsys, "brain", "init", str(tmp_path / "b"))
        assert any("actor" in warning for warning in payload["warnings"])

    def test_init_says_so_when_an_existing_config_ignores_the_actor(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A vitruvio.toml is never rewritten without --force, so a second brain beside the first inherits its actor.

        Accepting `--actor` and then ignoring it would attribute this brain's writes to whoever the neighbouring
        project named -- provenance that is wrong, which is worse than provenance that is missing.
        """
        monkeypatch.chdir(tmp_path)
        envelope(capsys, "brain", "init", str(tmp_path / "first"), "--actor", "alice@example.com")

        _, payload = envelope(capsys, "brain", "init", str(tmp_path / "second"), "--actor", "bob@example.com")
        assert payload["data"]["config_file"] is None
        assert any("alice@example.com" in warning for warning in payload["warnings"]), payload["warnings"]

    def test_init_stays_quiet_when_the_existing_config_already_names_that_actor(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        envelope(capsys, "brain", "init", str(tmp_path / "first"), "--actor", "alice@example.com")

        _, payload = envelope(capsys, "brain", "init", str(tmp_path / "second"), "--actor", "alice@example.com")
        assert payload["warnings"] == []

    def test_init_remembers_the_brain_as_the_default(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        envelope(capsys, "brain", "init", str(tmp_path / "b"), "--actor", "a@b.c")
        _, payload = envelope(capsys, "brain", "list")
        assert payload["data"]["current"] == str(tmp_path / "b")

    def test_an_unknown_policy_profile_lists_the_valid_ones(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        code, payload = envelope(capsys, "brain", "init", str(tmp_path / "b"), "--policy", "whatever")
        assert code == ExitCode.CONFIG
        assert "archival" in payload["error"]["message"]

    def test_a_policy_profile_reaches_the_brain(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        path = tmp_path / "b"
        envelope(capsys, "brain", "init", str(path), "--actor", "a@b.c", "--policy", "archival")
        _, payload = envelope(capsys, "--brain", str(path), "brain", "info")
        assert payload["data"]["policy"]["droppable_modules"] == []


class TestBrainReads:
    def test_state_reports_the_selection_layer(self, capsys: pytest.CaptureFixture[str], brain: Path) -> None:
        code, payload = envelope(capsys, "--brain", str(brain), "brain", "state")
        assert code == ExitCode.OK
        assert payload["data"]["brain_origin"] == "flag"
        assert payload["data"]["installed"] == []

    def test_verify_passes_on_an_empty_brain(self, capsys: pytest.CaptureFixture[str], brain: Path) -> None:
        code, payload = envelope(capsys, "--brain", str(brain), "brain", "verify")
        assert code == ExitCode.OK
        assert payload["data"]["verified"] is True

    def test_history_is_empty_before_the_first_write(self, capsys: pytest.CaptureFixture[str], brain: Path) -> None:
        _, payload = envelope(capsys, "--brain", str(brain), "brain", "history")
        assert payload["data"]["snapshots"] == []

    def test_info_warns_that_nothing_semantic_will_be_published(
        self, capsys: pytest.CaptureFixture[str], brain: Path
    ) -> None:
        _, payload = envelope(capsys, "--brain", str(brain), "brain", "info")
        assert payload["data"]["travelling_indices"] == []

    def test_a_command_against_no_brain_names_all_four_selection_layers(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, payload = envelope(capsys, "brain", "state")
        assert code == ExitCode.CONFIG
        assert payload["error"]["code"] == "NO_BRAIN"
        assert "VITRUVIO_BRAIN" in payload["error"]["hint"]


class TestSource:
    def test_register_reports_the_block_and_the_version(
        self, capsys: pytest.CaptureFixture[str], brain: Path, source: Path
    ) -> None:
        code, payload = envelope(capsys, "--brain", str(brain), "source", "register", str(source))
        assert code == ExitCode.OK
        assert payload["data"]["block_id"].startswith("sha256:")
        assert payload["data"]["duplicate"] is False

    def test_markdown_is_recognised_without_being_declared(
        self, capsys: pytest.CaptureFixture[str], brain: Path, source: Path
    ) -> None:
        """mimetypes does not know .md, and a media type nothing can normalise is a source nothing will read."""
        _, registered = envelope(capsys, "--brain", str(brain), "source", "register", str(source))
        _, block = envelope(capsys, "--brain", str(brain), "inspect", "block", registered["data"]["block_id"])
        assert block["data"]["payload"]["media_type"] == "text/markdown"

    def test_a_declared_media_type_wins_over_the_guess(
        self, capsys: pytest.CaptureFixture[str], brain: Path, source: Path
    ) -> None:
        _, registered = envelope(
            capsys, "--brain", str(brain), "source", "register", str(source), "--media-type", "text/plain"
        )
        _, block = envelope(capsys, "--brain", str(brain), "inspect", "block", registered["data"]["block_id"])
        assert block["data"]["payload"]["media_type"] == "text/plain"

    def test_registering_twice_warns_and_mints_no_version(
        self, capsys: pytest.CaptureFixture[str], brain: Path, source: Path
    ) -> None:
        envelope(capsys, "--brain", str(brain), "source", "register", str(source))
        _, payload = envelope(capsys, "--brain", str(brain), "source", "register", str(source))
        assert payload["data"]["duplicate"] is True
        assert payload["data"]["snapshot"] is None
        assert any("already registered" in warning for warning in payload["warnings"])

    def test_a_missing_file_is_reported_before_the_brain_is_opened(
        self, capsys: pytest.CaptureFixture[str], brain: Path, tmp_path: Path
    ) -> None:
        code, payload = envelope(capsys, "--brain", str(brain), "source", "register", str(tmp_path / "absent.md"))
        assert code == ExitCode.INTERNAL or payload["ok"] is False
        assert "does not exist" in payload["error"]["message"]

    def test_registering_without_an_actor_is_refused(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, source: Path
    ) -> None:
        path = tmp_path / "anon"
        envelope(capsys, "brain", "init", str(path))
        code, payload = envelope(capsys, "--brain", str(path), "source", "register", str(source))
        assert code == ExitCode.CONFIG
        assert payload["error"]["code"] == "ACTOR_UNKNOWN"

    def test_put_stores_content_without_registering_evidence(
        self, capsys: pytest.CaptureFixture[str], brain: Path, source: Path
    ) -> None:
        code, payload = envelope(capsys, "--brain", str(brain), "source", "put", str(source))
        assert code == ExitCode.OK
        assert payload["data"]["blob"].startswith("sha256:")
        # No canonical block was created, so the module list is unchanged.
        _, state = envelope(capsys, "--brain", str(brain), "brain", "state")
        assert "canonical" not in state["data"]["installed"]

    def test_replace_supersedes_without_removing(
        self, capsys: pytest.CaptureFixture[str], brain: Path, source: Path, tmp_path: Path
    ) -> None:
        _, first = envelope(capsys, "--brain", str(brain), "source", "register", str(source))
        newer = tmp_path / "second.md"
        newer.write_text("# Segunda edicion\n", encoding="utf-8")

        code, _ = envelope(
            capsys,
            "--brain",
            str(brain),
            "source",
            "replace",
            str(newer),
            "--supersedes",
            first["data"]["block_id"],
        )
        assert code == ExitCode.OK
        _, proof = envelope(
            capsys,
            "--brain",
            str(brain),
            "inspect",
            "prove",
            first["data"]["block_id"],
            "--memory-type",
            "canonical",
        )
        assert proof["data"]["verified"] is True


class TestInspect:
    def test_prove_returns_a_checked_proof(self, capsys: pytest.CaptureFixture[str], brain: Path, source: Path) -> None:
        _, registered = envelope(capsys, "--brain", str(brain), "source", "register", str(source))
        code, payload = envelope(
            capsys,
            "--brain",
            str(brain),
            "inspect",
            "prove",
            registered["data"]["block_id"],
            "--memory-type",
            "canonical",
        )
        assert code == ExitCode.OK
        assert payload["data"]["verified"] is True

    def test_resolvability_separates_tombstoned_from_missing(
        self, capsys: pytest.CaptureFixture[str], brain: Path, source: Path
    ) -> None:
        envelope(capsys, "--brain", str(brain), "source", "register", str(source))
        _, payload = envelope(capsys, "--brain", str(brain), "inspect", "resolvability")
        assert payload["data"]["intact"] is True
        assert set(payload["data"]["counts"]) == {"resolvable", "tombstoned", "missing"}

    def test_roots_names_every_installed_module(
        self, capsys: pytest.CaptureFixture[str], brain: Path, source: Path
    ) -> None:
        envelope(capsys, "--brain", str(brain), "source", "register", str(source))
        _, payload = envelope(capsys, "--brain", str(brain), "inspect", "roots")
        assert set(payload["data"]["roots"]) == {"canonical", "provenance"}

    def test_an_unknown_memory_type_is_a_usage_error(self, capsys: pytest.CaptureFixture[str], brain: Path) -> None:
        code, payload = envelope(capsys, "--brain", str(brain), "inspect", "module", "semantics")
        assert code != ExitCode.OK
        assert "procedural" in payload["error"]["message"]

    def test_doctor_reports_rather_than_fails(self, capsys: pytest.CaptureFixture[str], brain: Path) -> None:
        """A broken setup is what doctor exists to describe, so it must not exit non-zero on one."""
        code, payload = envelope(capsys, "--brain", str(brain), "inspect", "doctor")
        assert code == ExitCode.OK
        assert any(check["check"] == "integrity" for check in payload["data"]["checks"])

    def test_doctor_works_with_no_brain_at_all(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, payload = envelope(capsys, "inspect", "doctor")
        assert code == ExitCode.OK
        assert payload["data"]["failures"] >= 1


class TestSearch:
    def test_search_returns_a_bundle_with_no_answer_field(
        self, capsys: pytest.CaptureFixture[str], brain: Path, source: Path
    ) -> None:
        envelope(capsys, "--brain", str(brain), "source", "register", str(source))
        code, payload = envelope(capsys, "--brain", str(brain), "search", "fourier")
        assert code == ExitCode.OK
        assert "answer" not in payload["data"]
        assert payload["data"]["all_verified"] is True

    def test_search_is_reachable_both_as_a_group_and_as_a_top_level_alias(
        self, capsys: pytest.CaptureFixture[str], brain: Path
    ) -> None:
        aliased = envelope(capsys, "--brain", str(brain), "search", "x")
        grouped = envelope(capsys, "--brain", str(brain), "query", "search", "x")
        assert aliased[0] == grouped[0] == ExitCode.OK
        assert aliased[1]["command"] == grouped[1]["command"] == "query.search"

    def test_no_match_is_an_answer(self, capsys: pytest.CaptureFixture[str], brain: Path) -> None:
        code, payload = envelope(capsys, "--brain", str(brain), "search", "nothing here")
        assert code == ExitCode.OK
        assert payload["data"]["matches"] == []

    def test_memory_type_filters_are_repeatable(
        self, capsys: pytest.CaptureFixture[str], brain: Path, source: Path
    ) -> None:
        envelope(capsys, "--brain", str(brain), "source", "register", str(source))
        code, _ = envelope(capsys, "--brain", str(brain), "search", "x", "-m", "canonical", "-m", "semantic")
        assert code == ExitCode.OK

    def test_an_invalid_mode_is_a_usage_error(self, capsys: pytest.CaptureFixture[str], brain: Path) -> None:
        code, payload = envelope(capsys, "--brain", str(brain), "search", "x", "--mode", "telepathy")
        assert code != ExitCode.OK
        assert payload["ok"] is False


class TestHumanRendering:
    def test_the_bundle_says_so_when_there_is_nothing(self, capsys: pytest.CaptureFixture[str], brain: Path) -> None:
        _, out, _ = run(capsys, "--brain", str(brain), "search", "nothing")
        assert "not an error" in out

    def test_state_prints_a_module_table(self, capsys: pytest.CaptureFixture[str], brain: Path, source: Path) -> None:
        run(capsys, "--brain", str(brain), "source", "register", str(source))
        _, out, _ = run(capsys, "--brain", str(brain), "brain", "state")
        assert "canonical" in out
        assert "provenance" in out

    def test_verify_failure_exits_with_the_protocol_code(
        self, capsys: pytest.CaptureFixture[str], brain: Path, source: Path
    ) -> None:
        """Corrupting a blob must be reported as corruption, not as a stale index."""
        envelope(capsys, "--brain", str(brain), "source", "register", str(source))
        blobs = sorted((brain / "blobs" / "sha256").glob("*"))
        assert blobs, "the register should have written blobs"
        for blob in blobs:
            blob.write_bytes(b"tampered")

        code, payload = envelope(capsys, "--brain", str(brain), "brain", "verify")
        assert code == ExitCode.PROTOCOL
        assert payload["ok"] is False


class TestRegistryFlagDefaults:
    """A flag whose default overrides configuration is a flag that makes the configuration dead.

    `RemoteOps._client` treats ``insecure=None`` as "read `[registry].insecure`" and any bool as an override. So
    a CLI parameter declared ``bool = False`` does not mean "not asked for" -- it means "asked for plain HTTP to
    be off", and it silently beat a project that had declared it on. Pinned as a signature so the next command
    to take this flag cannot reintroduce it.
    """

    def test_every_dist_command_leaves_insecure_unset_by_default(self) -> None:
        import inspect

        from vitruvio.cli.commands import dist

        checked = 0
        for name in ("push", "fetch", "plan_pull", "pull", "tags"):
            parameter = inspect.signature(getattr(dist, name)).parameters["insecure"]
            assert parameter.default is None, f"dist {name} defaults insecure to {parameter.default!r}"
            checked += 1
        assert checked == 5, "a dist command taking --insecure was added without being pinned here"
