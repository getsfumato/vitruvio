"""The skills, the completion scripts, and the generated reference.

Three things are asserted here that are easy to break and silent when broken: the skills are actually present as
package data, the generated reference matches the committed copy, and the completion scripts parse in their own
shells. Each failure mode is invisible at runtime -- a `skills install` that installs nothing exits 0, a stale
reference reads plausibly, and a malformed completion script just stops completing.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from vitruvio.cli import reference
from vitruvio.cli.commands import skills as skills_command
from vitruvio.cli.main import main
from vitruvio.kernel import ExitCode

EXPECTED = {
    "vitruvio",
    "vitruvio-cli",
    "vitruvio-query",
    "vitruvio-ingest",
    "vitruvio-retention",
    "vitruvio-dist",
    "vitruvio-reconcile",
    "vitruvio-compound",
}

CREATION_SKILLS = (
    Path(__file__).resolve().parents[3] / "skills" / "vitruvio" / "SKILL.md",
    Path(__file__).resolve().parents[3] / "skills" / "vitruvio-cli" / "SKILL.md",
)


def envelope(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    """Invoke the CLI in JSON mode and parse the single object it printed."""
    code = main(["--json", *args])
    return code, json.loads(capsys.readouterr().out)


class TestSkillsArePresent:
    def test_every_skill_ships(self) -> None:
        """A `skills install` that installs nothing exits 0 and looks like a success, so presence is asserted."""
        installed = {item.name for item in skills_command._available()}
        assert installed >= EXPECTED

    def test_each_skill_has_frontmatter_with_a_description(self) -> None:
        """The description is what decides whether a skill gets loaded at all, so an empty one makes the skill dead
        weight that never fires."""
        for skill in skills_command._available():
            text = (skill / "SKILL.md").read_text(encoding="utf-8")
            assert text.startswith("---\n"), skill.name
            assert "\nname: " in text, skill.name
            assert "\ndescription: " in text, skill.name
            description = next(line for line in text.splitlines() if line.startswith("description: "))
            assert len(description) > 60, f"{skill.name}: a description this short will not match a real request"

    def test_the_entry_skill_carries_its_references(self) -> None:
        entry = next(item for item in skills_command._available() if item.name == "vitruvio")
        present = {item.name for item in (entry / "references").glob("*.md")}
        assert present == {"cli-reference.md", "json-envelope.md", "exit-codes.md", "evidence-bundle.md"}

    def test_no_skill_promises_a_field_that_does_not_exist(self) -> None:
        """`answer` is the one an agent would most like to exist, and the design says it never will. A skill that
        implied otherwise would teach exactly the wrong habit."""
        for skill in skills_command._available():
            for document in [skill / "SKILL.md", *(skill / "references").glob("*.md")]:
                text = document.read_text(encoding="utf-8")
                assert '"answer"' not in text, document

    @pytest.mark.parametrize("skill", CREATION_SKILLS)
    def test_brain_creation_requires_an_explicit_governance_choice(self, skill: Path) -> None:
        """Neither init's nor migration's opposite default may silently choose the new brain's trust posture."""
        text = skill.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        assert "governed" in normalized
        assert "ungoverned" in normalized
        assert "Offer exactly those two" in normalized
        assert "do not recommend or select a default" in normalized
        assert "do not create anything until the user answers" in normalized
        assert "personal, shared, public" in normalized
        assert "defaults to ungoverned" in normalized
        assert "migration defaults to governed" in normalized
        assert "public key" in normalized
        assert "private key" in normalized
        assert "quorum" in normalized
        for scope in ("ingest", "commit", "drop:canonical", "redact", "govern", "propose"):
            assert f"`{scope}`" in normalized


class TestInstall:
    def test_install_writes_every_skill(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        code, payload = envelope(capsys, "skills", "install", "--into", str(tmp_path / "s"))
        assert code == ExitCode.OK
        assert set(payload["data"]["installed"]) >= EXPECTED
        assert (tmp_path / "s" / "vitruvio" / "SKILL.md").is_file()
        assert (tmp_path / "s" / "vitruvio" / "references" / "cli-reference.md").is_file()

    def test_an_existing_skill_is_left_alone(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        """A consumer may have edited one, and silently overwriting local edits is not something a copy command
        should do."""
        target = tmp_path / "s"
        envelope(capsys, "skills", "install", "--into", str(target))
        edited = target / "vitruvio" / "SKILL.md"
        edited.write_text("local edits", encoding="utf-8")

        code, payload = envelope(capsys, "skills", "install", "--into", str(target))
        assert code == ExitCode.OK
        assert "vitruvio" in payload["data"]["skipped"]
        assert edited.read_text(encoding="utf-8") == "local edits"

    def test_force_overwrites(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        target = tmp_path / "s"
        envelope(capsys, "skills", "install", "--into", str(target))
        (target / "vitruvio" / "SKILL.md").write_text("local edits", encoding="utf-8")

        envelope(capsys, "skills", "install", "--into", str(target), "--force")
        assert (target / "vitruvio" / "SKILL.md").read_text(encoding="utf-8") != "local edits"

    def test_one_skill_can_be_installed_alone(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        code, payload = envelope(
            capsys, "skills", "install", "--into", str(tmp_path / "s"), "--skill", "vitruvio-query"
        )
        assert code == ExitCode.OK
        assert payload["data"]["installed"] == ["vitruvio-query"]

    def test_an_unknown_skill_lists_the_options(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        code, payload = envelope(
            capsys, "skills", "install", "--into", str(tmp_path / "s"), "--skill", "vitruvio-telepathy"
        )
        assert code != ExitCode.OK
        assert "vitruvio-query" in (payload["error"]["hint"] or "")


class TestReference:
    def test_the_committed_reference_is_up_to_date(self) -> None:
        """A reference that disagrees with the parser costs an agent a turn to discover, so it is generated and
        checked rather than maintained."""
        assert reference.main(["--check"]) == 0, "run `python -m vitruvio.cli.reference --write`"

    def test_it_documents_the_commands_and_not_cyclopts_internals(self) -> None:
        """cyclopts wraps each command in its own App, so reading the App's signature instead of the function's
        produced a reference full of `--error-formatter` on every command."""
        document = reference.render()
        assert "vitruvio task validate" in document
        assert "vitruvio retain plan-drop" in document
        assert "--error-formatter" not in document
        assert "--console" not in document

    def test_parameter_name_overrides_match_the_actual_parser(self) -> None:
        document = reference.render()
        assert "`--class`" in document
        assert "`--classes`" not in document
        assert "`--scheme`" in document
        assert "`--schemes`" not in document

    def test_true_boolean_defaults_document_the_effective_negative_flag(self) -> None:
        document = reference.render()
        migrate = next(line for line in document.splitlines() if "`vitruvio brain migrate`" in line)
        assert "`--no-governed`" in migrate
        assert "`--governed`" not in migrate


class TestCompletion:
    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_a_script_is_produced_for_each_shell(self, capsys: pytest.CaptureFixture[str], shell: str) -> None:
        code, payload = envelope(capsys, "completion", shell)
        assert code == ExitCode.OK
        assert "vitruvio" in payload["data"]["script"]

    @pytest.mark.parametrize("shell", ["bash", "zsh"])
    def test_the_script_parses_in_its_own_shell(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, shell: str
    ) -> None:
        """A malformed completion script does not error -- it just stops completing, which nobody reports."""
        if shutil_which(shell) is None:  # pragma: no cover - depends on the machine
            pytest.skip(f"{shell} is not installed")
        _, payload = envelope(capsys, "completion", shell)
        script = tmp_path / f"vitruvio.{shell}"
        script.write_text(payload["data"]["script"], encoding="utf-8")
        completed = subprocess.run([shell, "-n", str(script)], capture_output=True, timeout=30, check=False)
        assert completed.returncode == 0, completed.stderr.decode()

    def test_the_command_names_come_from_the_app(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Offering a command that no longer exists is worse than offering none, and it fails silently."""
        _, payload = envelope(capsys, "completion", "bash")
        for group in ("brain", "task", "ingest", "retain", "dist", "registry", "skills"):
            assert group in payload["data"]["script"]

    def test_an_unsupported_shell_lists_the_supported_ones(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, payload = envelope(capsys, "completion", "powershell")
        assert code != ExitCode.OK
        assert "zsh" in (payload["error"]["hint"] or "")


def shutil_which(name: str) -> str | None:
    """``shutil.which``, imported here to keep the module's imports at the top honest about what it needs."""
    import shutil

    return shutil.which(name)


class TestThePackagingOfSkills:
    """The arrangement that keeps one authored copy while still shipping it, and the trap it replaced.

    `apps/cli/src/vitruvio/cli/skills` is a symlink to `skills/` at the repository root. The obvious alternative --
    hatchling's `force-include` from `../../skills` -- builds a correct *wheel* and an **unbuildable sdist**: a
    force-include cannot reach outside the project directory, so `pip install vitruvio` failed with "Forced include
    not found" for anyone whose installer preferred the sdist. Building the wheel directly worked, which is precisely
    why it went unnoticed until an install from the sdist was attempted.
    """

    ROOT = Path(__file__).resolve().parents[3]

    def test_the_packaged_path_is_a_link_to_the_authored_one(self) -> None:
        packaged = self.ROOT / "apps" / "cli" / "src" / "vitruvio" / "cli" / "skills"
        assert packaged.is_symlink(), "the packaged path must be a link, not a second copy that can drift"
        assert packaged.resolve() == (self.ROOT / "skills").resolve()

    def test_the_authored_directory_is_the_only_copy(self) -> None:
        """Two copies under version control would drift, and the drift would be invisible: `skills install` reads one
        of them and a reviewer reads the other.

        `.claude/` is excluded rather than overlooked. Those skills are for an agent working *on* this repository --
        a different audience, a different lifecycle, and not shipped to anyone.
        """
        excluded = {".venv", ".claude", "node_modules"}
        real = {path.resolve() for path in self.ROOT.glob("**/skills/**/SKILL.md") if not excluded & set(path.parts)}
        assert real, "no SKILL.md found at all, which means this test is asserting nothing"
        assert all(path.is_relative_to((self.ROOT / "skills").resolve()) for path in real), sorted(real)

    def test_nothing_declares_a_force_include(self) -> None:
        """The specific mistake, pinned by name. It is easy to reach for again -- it looks tidier in the config and
        its failure is invisible in every check short of installing from an sdist."""
        manifest = (self.ROOT / "apps" / "cli" / "pyproject.toml").read_text(encoding="utf-8")
        assert "force-include" not in manifest, "a force-include here cannot be represented in an sdist"

    def test_the_command_reads_the_authored_copy_in_a_working_tree(self) -> None:
        """So that editing a skill and running `skills install` installs what was just edited, rather than whatever
        the last `uv sync` happened to place in site-packages."""
        assert skills_command.SOURCE.resolve() == (self.ROOT / "skills").resolve()
