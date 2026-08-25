"""``vitruvio update``, and the notice that follows every other command.

The notice is the part with teeth. It runs after a command has already succeeded, so every bug it can have is
a bug that spoils something that worked: a line in the middle of a JSON stream, a prompt in a script, a
network timeout attributed to whatever the user actually ran. Most of the tests here are about it staying
quiet.

Nothing reaches the network: `updates.fetch_latest` is replaced wherever an answer is needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vitruvio.cli.main import main
from vitruvio.kernel import ExitCode, updates


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    """Invoke the CLI in-process and return its status and streams."""
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def envelope(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    """Invoke the CLI in JSON mode and parse the single object it printed."""
    code, out, _ = run(capsys, "--json", *args)
    return code, json.loads(out)


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cache of its own, and no opt-out inherited from whoever is running the suite."""
    monkeypatch.delenv(updates.OPT_OUT, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


@pytest.fixture
def newer(monkeypatch: pytest.MonkeyPatch) -> str:
    """A release that is always newer than whatever this checkout is at."""
    monkeypatch.setattr(updates, "fetch_latest", lambda timeout=0: "99.0.0")
    return "99.0.0"


class TestCheck:
    def test_it_reports_an_available_release_without_installing(
        self, capsys: pytest.CaptureFixture[str], newer: str
    ) -> None:
        code, payload = envelope(capsys, "update", "--check")

        assert code == ExitCode.OK, "a check reports; it does not fail because an update exists"
        assert payload["data"]["available"] is True
        assert payload["data"]["latest"] == newer
        assert payload["data"]["installed"] is False
        assert payload["data"]["reason"] == "check only"

    def test_it_reports_being_current(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from vitruvio.kernel.version import __version__

        monkeypatch.setattr(updates, "fetch_latest", lambda timeout=0: __version__)

        code, payload = envelope(capsys, "update", "--check")

        assert code == ExitCode.OK
        assert payload["data"]["available"] is False
        assert payload["data"]["latest"] == __version__

    def test_being_unable_to_reach_github_is_reported_not_raised(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(updates, "fetch_latest", lambda timeout=0: None)

        code, payload = envelope(capsys, "update", "--check")

        assert code == ExitCode.OK
        assert payload["data"]["latest"] is None
        assert payload["data"]["available"] is False

    def test_the_opt_out_does_not_silence_an_explicit_check(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, newer: str
    ) -> None:
        """It silences the ambient notice. Somebody who typed `update` asked the question directly."""
        monkeypatch.setenv(updates.OPT_OUT, "1")

        _, payload = envelope(capsys, "update", "--check")

        assert payload["data"]["available"] is True


class TestInstalling:
    def test_it_refuses_a_source_checkout(self, capsys: pytest.CaptureFixture[str], newer: str) -> None:
        """The suite runs from a checkout, which is exactly the environment the installer would replace."""
        code, payload = envelope(capsys, "update", "--yes")

        assert code == ExitCode.USAGE
        assert "source checkout" in payload["error"]["message"]
        assert payload["error"]["hint"], "a refusal has to say what to do instead"

    def test_being_current_installs_nothing(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Checked before the source guard: there is nothing to do, so there is nothing to refuse."""
        from vitruvio.kernel.version import __version__

        monkeypatch.setattr(updates, "fetch_latest", lambda timeout=0: __version__)

        code, payload = envelope(capsys, "update", "--yes")

        assert code == ExitCode.OK
        assert payload["data"]["installed"] is False
        assert payload["data"]["reason"] == "already current"

    def test_the_installer_command_contains_no_version_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from vitruvio.cli.commands.update import _installer_command

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/curl" if name == "curl" else None)

        argv = _installer_command()

        assert argv[0] == "sh"
        assert updates.INSTALLER_URL in argv[-1]
        assert "VITRUVIO_VERSION" not in argv[-1]

    def test_it_pins_a_normalized_version_through_the_environment(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, newer: str
    ) -> None:
        import subprocess

        captured: dict[str, Any] = {}

        def installed(argv: list[str], **kwargs: Any) -> Any:
            captured["argv"] = argv
            captured.update(kwargs)
            return subprocess.CompletedProcess(argv, 0)

        monkeypatch.setattr(updates, "installed_from_source", lambda: False)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/curl" if name == "curl" else None)
        monkeypatch.setattr("subprocess.run", installed)

        code, payload = envelope(capsys, "update", "--version", "v1.2.3", "--yes")

        assert code == ExitCode.OK
        assert payload["data"]["target"] == "1.2.3"
        assert captured["env"]["VITRUVIO_VERSION"] == "1.2.3"
        assert "1.2.3" not in " ".join(captured["argv"])

    def test_shell_metacharacters_are_rejected_as_a_version(
        self, capsys: pytest.CaptureFixture[str], newer: str
    ) -> None:
        code, payload = envelope(capsys, "update", "--version", "0.6.0; printf INJECTED", "--yes")

        assert code == ExitCode.USAGE
        assert "not a valid version" in payload["error"]["message"]

    def test_it_says_so_when_nothing_can_fetch_the_installer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from vitruvio.cli.commands.update import _installer_command
        from vitruvio.kernel import VitruvioError

        monkeypatch.setattr("shutil.which", lambda name: None)

        with pytest.raises(VitruvioError, match="curl nor wget"):
            _installer_command()


class TestTheNotice:
    """Everything here is about a line that must not appear."""

    def test_it_appears_after_an_ordinary_command(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, newer: str
    ) -> None:
        monkeypatch.setattr("sys.stderr.isatty", lambda: True)

        code, out, err = run(capsys, "config", "show")

        assert code == ExitCode.OK
        assert "99.0.0" in err, "the notice goes to stderr"
        assert "vitruvio update" in err, "and names the command that acts on it"
        assert "99.0.0" not in out, "never on stdout, which is the command's own answer"

    def test_it_is_silent_in_json_mode(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, newer: str
    ) -> None:
        """A caller is parsing one envelope; prose beside it is at best noise."""
        monkeypatch.setattr("sys.stderr.isatty", lambda: True)

        code, out, err = run(capsys, "--json", "config", "show")

        assert code == ExitCode.OK
        json.loads(out)
        assert "99.0.0" not in err

    def test_it_is_silent_when_quiet(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, newer: str
    ) -> None:
        monkeypatch.setattr("sys.stderr.isatty", lambda: True)

        _, _, err = run(capsys, "--quiet", "config", "show")

        assert "99.0.0" not in err

    def test_it_is_silent_without_a_terminal(self, capsys: pytest.CaptureFixture[str], newer: str) -> None:
        """capsys already makes stderr something that is not a tty, which is the condition -- a log file or a
        pipe is nobody reading."""
        _, _, err = run(capsys, "config", "show")

        assert "99.0.0" not in err

    def test_it_is_silent_during_update_itself(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, newer: str
    ) -> None:
        """That command reports this better and was asked to; saying it twice is noise."""
        monkeypatch.setattr("sys.stderr.isatty", lambda: True)

        _, _, err = run(capsys, "update", "--check")

        assert "run `vitruvio update`" not in err

    def test_it_asks_nothing_when_the_cache_is_fresh(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The notice must cost a file read, not a request, on all but the first run of a day."""
        monkeypatch.setattr("sys.stderr.isatty", lambda: True)
        calls = {"n": 0}

        def counted(timeout: float = 0) -> str:
            calls["n"] += 1
            return "99.0.0"

        monkeypatch.setattr(updates, "fetch_latest", counted)
        run(capsys, "config", "show")
        run(capsys, "config", "show")
        run(capsys, "config", "show")

        assert calls["n"] == 1

    def test_a_failing_check_cannot_fail_the_command(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It runs after the command succeeded. Nothing it can do is worth turning that into a non-zero exit."""
        monkeypatch.setattr("sys.stderr.isatty", lambda: True)

        def explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError("the check itself is broken")

        monkeypatch.setattr(updates, "is_due", explode)

        code, _, _ = run(capsys, "config", "show")

        assert code == ExitCode.OK

    def test_the_opt_out_silences_it(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, newer: str
    ) -> None:
        monkeypatch.setattr("sys.stderr.isatty", lambda: True)
        monkeypatch.setenv(updates.OPT_OUT, "1")

        _, _, err = run(capsys, "config", "show")

        assert "99.0.0" not in err
