"""The CLI's contract: one JSON envelope, stable exit codes, and nothing on the wrong stream.

These tests assert on the *parsed envelope*, never on human text. Human rendering is going to churn -- it is
meant to -- and a suite that breaks when a label is reworded is a suite people stop trusting. What must not
churn is the machine-readable shape, because that is what an agent is driving.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from vitruvio.cli.main import main
from vitruvio.kernel import ExitCode, __version__


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    """Invoke the CLI in-process and return its status and streams."""
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def envelope(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    """Invoke the CLI in JSON mode and parse the single object it printed."""
    code, out, _ = run(capsys, "--json", *args)
    return code, json.loads(out)


def make_brain(root: Path, name: str = "brain") -> Path:
    brain = root / name
    brain.mkdir(parents=True, exist_ok=True)
    (brain / "oci-layout").write_text('{"imageLayoutVersion": "1.0.0"}')
    return brain


class TestEnvelope:
    def test_the_top_level_is_the_same_for_every_command(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A caller branches on `ok` then `error.code` without knowing which command it ran."""
        expected: Sequence[str] = ("vitruvio", "command", "ok", "data", "warnings", "error")
        for args in (("config", "show"), ("config", "path"), ("brain", "list")):
            _, payload = envelope(capsys, *args)
            assert list(payload) == list(expected), f"{args} produced {list(payload)}"

    def test_success_carries_the_version_and_the_operation_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, payload = envelope(capsys, "config", "show")
        assert code == ExitCode.OK
        assert payload["vitruvio"] == __version__
        assert payload["command"] == "config.show"
        assert payload["ok"] is True
        assert payload["error"] is None

    def test_json_mode_prints_exactly_one_object_on_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, out, _ = run(capsys, "--json", "config", "show")
        assert json.loads(out)  # a second object, or a stray line, would make this raise

    def test_warnings_reach_the_envelope_rather_than_stderr_in_json_mode(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A machine reading the envelope must see a degradation it would otherwise miss on stderr."""
        code, out, err = run(capsys, "--json", "brain", "list")
        payload = json.loads(out)
        assert code == ExitCode.OK
        assert payload["warnings"] == ["no brains recorded yet, and this project declares none"]
        assert err == ""

    def test_human_mode_puts_notes_on_stderr_so_stdout_stays_pipeable(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, out, err = run(capsys, "brain", "list")
        assert "warning: no brains recorded yet" in err
        assert out.strip() == ""


class TestFailureWithMeasurements:
    """A failure whose numbers are the point still gets exactly one envelope.

    `bench --gate` is the case: it emitted a success envelope and then raised, so main's handler printed a second
    JSON document. Two objects on stdout, only on failure, for the one flag combination CI runs.
    """

    def _console(self) -> Any:
        from vitruvio.cli.output import Console

        return Console(json_mode=True)

    def test_one_object_carries_both_the_error_and_the_data(self, capsys: pytest.CaptureFixture[str]) -> None:
        from vitruvio.kernel import VitruvioError

        measurements = {"verdict": {"passed": False}, "recall_at_10": 0.61}
        self._console().fail("bench", VitruvioError("gates not cleared", hint="see the rows"), data=measurements)

        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["command"] == "bench", "the failing command names itself rather than deferring to 'cli'"
        assert payload["error"]["message"] == "gates not cleared"
        assert payload["data"] == measurements, "a gate that hides what it measured makes CI re-run to find out"

    def test_a_failure_without_data_still_carries_none(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The existing callers pass no data, and their envelopes must not change shape."""
        from vitruvio.kernel import VitruvioError

        self._console().fail("cli", VitruvioError("nope"))
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"] is None
        assert list(payload) == ["vitruvio", "command", "ok", "data", "warnings", "error"]

    def test_the_human_path_puts_the_view_on_stdout_and_the_error_on_stderr(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Same split as `emit`: the measurements are the result, the error is an aside."""
        from vitruvio.cli.output import Console
        from vitruvio.kernel import VitruvioError

        Console(json_mode=False).fail("bench", VitruvioError("gates not cleared"), view="recall 0.61 vs 0.74")
        streams = capsys.readouterr()
        assert "recall 0.61" in streams.out
        assert "gates not cleared" in streams.err


class TestTheLauncherAlwaysEmitsAnEnvelope:
    """`--json` promises exactly one object. It was promising zero for two whole classes of failure.

    A caller is told to branch on `ok` and then on `error.code` without knowing which command it ran. An empty
    stream is the one shape that contract cannot survive: it cannot be told apart from a command that printed
    nothing, and `json.loads` raises on it rather than reporting a failure.
    """

    def test_a_mistyped_flag_is_an_envelope_in_json_mode(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, payload = envelope(capsys, "brain", "state", "--flag-that-does-not-exist")
        assert code == ExitCode.USAGE
        assert payload["ok"] is False
        assert payload["error"]["code"] == "USAGE"

    def test_even_a_flag_mistyped_before_the_context_exists(self, capsys: pytest.CaptureFixture[str]) -> None:
        """`vitruvio --json --typo` fails while parsing the meta app, before a context is installed -- so the
        console has to be reconstructed from the tokens or the caller gets nothing."""
        code, payload = envelope(capsys, "--typo-that-is-not-a-flag")
        assert code == ExitCode.USAGE
        assert payload["error"]["code"] == "USAGE"

    def test_an_internal_error_is_an_envelope_in_json_mode(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The last-resort handler wrote to stderr and left stdout empty, so a crash and a silent success looked
        identical to whatever was parsing. Forced with a real unexpected exception, not a simulated one."""
        from vitruvio.cli import main as launcher

        def explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError("something nobody predicted")

        monkeypatch.setattr(type(launcher.app.meta), "__call__", explode)
        code, payload = envelope(capsys, "config", "show")

        assert code == ExitCode.INTERNAL
        assert payload["ok"] is False
        assert "something nobody predicted" in payload["error"]["message"]
        assert "please report it" in (payload["error"]["hint"] or "")

    def test_an_internal_error_in_human_mode_still_goes_to_stderr(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """stdout is the result and stderr is everything else, which a crash does not get to change."""
        from vitruvio.cli import main as launcher

        def explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError("something nobody predicted")

        monkeypatch.setattr(type(launcher.app.meta), "__call__", explode)
        code, out, err = run(capsys, "config", "show")

        assert code == ExitCode.INTERNAL
        assert out.strip() == ""
        assert "internal error: RuntimeError" in err

    def test_human_mode_still_does_not_print_the_usage_error_twice(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cyclopts already wrote a better message to stderr; stdout stays empty."""
        code, out, _ = run(capsys, "brain", "state", "--flag-that-does-not-exist")
        assert code == ExitCode.USAGE
        assert out.strip() == ""


class TestFailures:
    def test_an_expected_failure_carries_a_code_and_a_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, payload = envelope(capsys, "brain", "use", "./absent")
        assert code == ExitCode.NOT_FOUND
        assert payload["ok"] is False
        assert payload["error"]["code"] == "BRAIN_NOT_FOUND"
        assert "brain init" in payload["error"]["hint"]
        assert payload["data"] is None

    def test_a_failure_in_human_mode_writes_to_stderr_only(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, err = run(capsys, "brain", "use", "./absent")
        assert code == ExitCode.NOT_FOUND
        assert out == ""
        assert "error:" in err
        assert "hint:" in err

    def test_a_bad_config_reports_config_not_internal(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        broken = tmp_path / "broken.toml"
        broken.write_text("[actor]\nidd = 3\n")
        code, payload = envelope(capsys, "--config", str(broken), "config", "validate")
        assert code == ExitCode.CONFIG
        assert payload["error"]["code"] == "CONFIG_INVALID"

    def test_catalog_rejection_exits_with_validation_status(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        brain = tmp_path / "brain"
        envelope(capsys, "--brain", str(brain), "--actor", "tester@example.com", "brain", "init")
        manifest = tmp_path / "catalog.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": "vitruvio.catalog/v1",
                    "classes": [{"scheme": "missing", "label": "Class"}],
                }
            ),
            encoding="utf-8",
        )

        code, payload = envelope(
            capsys,
            "--brain",
            str(brain),
            "--actor",
            "tester@example.com",
            "catalog",
            "apply",
            str(manifest),
        )
        assert code == ExitCode.VALIDATION
        assert payload["error"]["code"] == "CANDIDATES_REJECTED"
        assert payload["data"]["clean"] is False

    def test_existing_migration_report_is_refused_before_destination_is_created(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        brain = tmp_path / "source"
        destination = tmp_path / "destination"
        report = tmp_path / "report.json"
        report.write_text("keep me", encoding="utf-8")
        envelope(capsys, "--brain", str(brain), "--actor", "tester@example.com", "brain", "init")

        code, payload = envelope(
            capsys,
            "--brain",
            str(brain),
            "--actor",
            "tester@example.com",
            "brain",
            "migrate",
            "--to",
            str(destination),
            "--no-governed",
            "--report",
            str(report),
        )
        assert code == ExitCode.CONFIG
        assert payload["error"]["code"] == "CONFIG_INVALID"
        assert report.read_text(encoding="utf-8") == "keep me"
        assert not destination.exists()

    def test_an_unknown_flag_is_a_usage_error_not_a_crash(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["config", "show", "--nonsense"])
        assert code == ExitCode.USAGE

    def test_exit_codes_are_distinct_values(self) -> None:
        """The numbers are a contract with whatever drives the CLI: append-only, never reassigned."""
        values = [member.value for member in ExitCode]
        assert len(values) == len(set(values))
        assert int(ExitCode.OK) == 0
        assert int(ExitCode.USAGE) == 2
        assert int(ExitCode.CONFIG) == 3

    def test_a_failure_before_the_command_ran_still_names_the_operation(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """`command` is the stable operation name on failure as on success.

        A brain that does not exist fails in the launcher, before the command body could name itself, and used to
        report as `cli` -- which told a caller correlating failures with the requests that produced them nothing.
        """
        absent = str(tmp_path / "absent")
        code, payload = envelope(capsys, "--brain", absent, "brain", "state")
        assert code == ExitCode.NOT_FOUND
        assert payload["error"]["code"] == "BRAIN_NOT_FOUND"
        assert payload["command"] == "brain.state"

        _, payload = envelope(capsys, "--brain", absent, "query", "search", "anything")
        assert payload["command"] == "query.search"

    def test_a_top_level_alias_reports_the_operation_it_stands_for(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """`vitruvio search` *is* `query search` and says so on failure too -- with meta options after the command."""
        _, payload = envelope(capsys, "search", "anything", "--brain", str(tmp_path / "absent"))
        assert payload["command"] == "query.search"

    def test_a_line_that_names_no_command_still_reports_cli(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, payload = envelope(capsys, "--typo")
        assert code == ExitCode.USAGE
        assert payload["command"] == "cli"

    def test_an_unknown_subcommand_reports_the_group_it_was_looked_up_in(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """As much of the operation as was recognised: a usage error on `brain`, rather than on nothing."""
        code, payload = envelope(capsys, "brain", "nope")
        assert code == ExitCode.USAGE
        assert payload["command"] == "brain"

    def test_a_failure_says_whether_it_is_retryable(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        """The column a caller acts on, in the envelope: a missing brain fails identically next time."""
        _, payload = envelope(capsys, "--brain", str(tmp_path / "absent"), "brain", "state")
        assert payload["error"]["retryable"] is False
        assert list(payload["error"]) == ["code", "kind", "message", "hint", "retryable"]

    def test_a_registry_failure_reaches_the_envelope_as_retryable(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End to end through a registry operation.

        The SDK's `DistributionError` is retryable in the mapping table, and that verdict was lost the moment it
        crossed `translate()` -- every registry hiccup came out of the runtime as permanent. The filesystem registry
        is the real transport with its `resolve` made to fail the way a network does.
        """
        from boltzmann.distribution.local import LocalLayoutRegistry
        from boltzmann.exceptions import DistributionError

        async def refuse(self: object, reference: str, tag: str) -> object:
            raise DistributionError("connection refused")

        monkeypatch.setattr(LocalLayoutRegistry, "resolve", refuse)
        brain = tmp_path / "brain"
        registry = tmp_path / "registry"
        registry.mkdir()
        envelope(capsys, "--brain", str(brain), "--actor", "tester@example.com", "brain", "init")

        code, payload = envelope(
            capsys, "--brain", str(brain), "dist", "plan-pull", "demo/brain", "--local", str(registry)
        )
        assert code == ExitCode.REGISTRY
        assert payload["command"] == "dist.plan-pull"
        assert payload["error"]["code"] == "REGISTRY_FAILED"
        assert payload["error"]["retryable"] is True

class TestOperationNames:
    """The name a failure reports is derived from the command line, and the alias table behind it must not rot.

    An alias naming a command that no longer exists would fall back to a wrong name without a sound; an alias
    pointing at a name no command emits would make success and failure disagree, which is the one thing the
    stable-operation contract forbids.
    """

    @staticmethod
    def _emitted_names() -> tuple[set[str], set[str]]:
        """Every string literal in the command modules, and every literal prefix of an f-string.

        The success envelope's name is a literal in the command body -- `console.emit("brain.state", ...)` -- except
        where one body serves several commands and builds it, as `f"reconcile.{strategy}"` does. The prefix is what
        that construction spells out, and is what an argv-derived name can be checked against.
        """
        import ast

        from vitruvio.cli import commands

        literals: set[str] = set()
        prefixes: set[str] = set()
        for path in Path(commands.__file__).parent.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    literals.add(node.value)
                elif isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
                    head = node.values[0].value
                    if isinstance(head, str) and head.endswith("."):
                        prefixes.add(head)
        return literals, prefixes

    @staticmethod
    def _leaves() -> list[tuple[str, ...]]:
        from vitruvio.cli.main import app

        def walk(node: object, path: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
            children = getattr(node, "_commands", None) or {}
            real = [(name, child) for name, child in children.items() if not name.startswith("-")]
            if not real:
                return [path]
            found: list[tuple[str, ...]] = []
            for name, child in real:
                found.extend(walk(child, (*path, name)))
            return found

        return [leaf for leaf in walk(app) if leaf]

    def test_every_alias_names_a_real_command(self) -> None:
        from vitruvio.cli.main import _OPERATION_ALIASES, app

        for chain in _OPERATION_ALIASES:
            found, _, unused = app.meta.parse_commands(list(chain))
            assert tuple(found) == chain, f"{chain} is not a command path"
            assert not unused, f"{chain} left {unused} unparsed, so it is a prefix rather than a command"

    def test_every_leaf_derives_a_name_some_command_emits(self) -> None:
        """The derivation is the dotted join unless the alias table says otherwise, and either way the result must
        be a name that appears in a command body -- so a new command whose emit name is not its path forces an
        alias here rather than a quiet disagreement between its success and its failure envelopes."""
        from vitruvio.cli.main import _operation

        literals, prefixes = self._emitted_names()
        unexplained = []
        for leaf in self._leaves():
            name = _operation(list(leaf))
            if name not in literals and not any(name.startswith(prefix) for prefix in prefixes):
                unexplained.append(f"{' '.join(leaf)} -> {name}")
        assert not unexplained, "no command emits these names:\n  " + "\n  ".join(unexplained)


class TestConfigCommands:
    def test_show_works_with_no_configuration_at_all(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, payload = envelope(capsys, "config", "show")
        assert code == ExitCode.OK
        assert payload["data"]["config_file"] is None
        assert payload["data"]["indices_are_defaults"] is True

    def test_show_masks_a_token_someone_put_in_the_file_anyway(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """There is no field for a secret, so this can only arrive via `extra` -- and it still must not print."""
        path = tmp_path / "vitruvio.toml"
        path.write_text('[registry]\nreference = "docker.io/ns/brain"\n')
        code, payload = envelope(capsys, "--config", str(path), "config", "show")
        assert code == ExitCode.OK
        assert "dckr_pat" not in json.dumps(payload)

    def test_set_then_get_round_trips_through_the_file(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        path = tmp_path / "vitruvio.toml"
        code, _ = envelope(capsys, "--config", str(path), "config", "set", "actor.id", "alex@example.com")
        assert code == ExitCode.OK

        code, payload = envelope(capsys, "--config", str(path), "config", "get", "actor.id")
        assert code == ExitCode.OK
        assert payload["data"]["value"] == "alex@example.com"

    def test_set_parses_json_shaped_values_as_their_types(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        path = tmp_path / "vitruvio.toml"
        envelope(capsys, "--config", str(path), "config", "set", "planner.rrf_k", "42")
        envelope(capsys, "--config", str(path), "config", "set", "policy.canonical_drop_allowed", "true")

        _, payload = envelope(capsys, "--config", str(path), "config", "get", "planner.rrf_k")
        assert payload["data"]["value"] == 42
        _, payload = envelope(capsys, "--config", str(path), "config", "get", "policy.canonical_drop_allowed")
        assert payload["data"]["value"] is True

    def test_set_warns_that_comments_were_lost(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        path = tmp_path / "vitruvio.toml"
        path.write_text('# a comment worth keeping\n[actor]\nid = "a@b.c"\n')
        _, payload = envelope(capsys, "--config", str(path), "config", "set", "actor.name", "Alex")
        assert any("comments" in warning for warning in payload["warnings"])

    def test_get_on_an_unset_key_says_how_to_list_them(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, payload = envelope(capsys, "config", "get", "actor.nonexistent")
        assert code == ExitCode.CONFIG
        assert "config show --effective" in payload["error"]["hint"]


class TestBrainCommands:
    def test_init_passes_global_assisted_by_to_the_service_config(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        class Service:
            def __init__(self, config: object) -> None:
                captured["config"] = config

            def init(self, **_options: object) -> dict[str, object]:
                return {"created": True, "brain": str(tmp_path / "brain"), "config_file": None}

        monkeypatch.setattr("vitruvio.runtime.BrainService", Service)
        brain = tmp_path / "brain"
        code, _payload = envelope(
            capsys,
            "--brain",
            str(brain),
            "--actor",
            "tester@example.com",
            "--assisted-by",
            "openai/codex",
            "brain",
            "init",
        )

        assert code == ExitCode.OK
        config = captured["config"]
        assert [item.id for item in config.project.assisted_by] == ["openai/codex"]

    def test_use_records_the_brain_and_later_commands_find_it(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        brain = make_brain(tmp_path)
        code, payload = envelope(capsys, "brain", "use", str(brain))
        assert code == ExitCode.OK
        assert payload["data"]["brain"] == str(brain)

        code, payload = envelope(capsys, "brain", "list")
        assert payload["data"]["current"] == str(brain)
        assert payload["data"]["brains"][0] == {"brain": str(brain), "current": True, "present": True}

    def test_a_remembered_brain_that_moved_is_reported_not_hidden(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """Silently dropping it would make the next --brain failure look like it came from nowhere."""
        import shutil

        brain = make_brain(tmp_path)
        envelope(capsys, "brain", "use", str(brain))
        shutil.rmtree(brain)

        _, payload = envelope(capsys, "brain", "list")
        assert payload["data"]["brains"][0]["present"] is False


class TestVersion:
    def test_version_flag_prints_the_shared_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = run(capsys, "--version")
        assert code == ExitCode.OK
        assert __version__ in out
