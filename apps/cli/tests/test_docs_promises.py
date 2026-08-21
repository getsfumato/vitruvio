"""The documentation may not promise a command that does not exist.

This test exists because the documentation *did*. `vitruvio bench` and `vitruvio calibrate` were described in the
guide, in an ADR, and -- worst of all -- in a skill, none of them built. A skill is read by an agent, which will run
what it is told and get a usage error it cannot interpret; and an ADR is read as a record of what shipped.

Prose is the hard part: "vitruvio writes", "vitruvio carries", "vitruvio isolates itself" all look like commands to a
regex. So the check is narrow on purpose -- only occurrences inside a fenced code block or inline code count, which is
where a command is actually being *offered* rather than mentioned.
"""

from __future__ import annotations

import re
from pathlib import Path

from vitruvio.cli.main import app

ROOT = Path(__file__).resolve().parents[3]
"""The repository root, from apps/cli/tests/."""

DOCUMENTS = ("docs", "skills", "README.md")

INVOCATION = re.compile(r"(?:^|[\s(`])vitruvio ((?:[a-z][a-z0-9-]*)(?:\s+[a-z][a-z0-9-]*){0,2})")
"""A command being offered. Applied only to code, never to prose."""

NOT_COMMANDS = frozenset(
    {
        # Words that follow "vitruvio" in a code context without being subcommands: flags handled separately, and
        # the distribution's own name in an install line.
        "brain",  # also a real group; kept for clarity that the filter is about *unknown* words only
    }
)


def known_commands() -> set[str]:
    """Every command path the CLI has, plus each of its prefixes."""

    def walk(node: object, path: tuple[str, ...] = ()) -> list[str]:
        children = getattr(node, "_commands", None) or {}
        real = [(name, child) for name, child in children.items() if not name.startswith("-")]
        if not real:
            return [" ".join(path)]
        found: list[str] = []
        for name, child in real:
            found.extend(walk(child, (*path, name)))
        return found

    commands = set(walk(app))
    for command in list(commands):
        parts = command.split()
        for index in range(1, len(parts)):
            commands.add(" ".join(parts[:index]))
    return {command for command in commands if command}


def code_spans(text: str) -> list[str]:
    """Every fenced block and inline-code span, which is where a command is offered rather than mentioned."""
    fenced = re.findall(r"```.*?```", text, flags=re.DOTALL)
    inline = re.findall(r"`[^`\n]+`", text)
    return [*fenced, *inline]


def documents() -> list[Path]:
    """Every markdown file that could offer a command."""
    found: list[Path] = []
    for entry in DOCUMENTS:
        target = ROOT / entry
        if target.is_file():
            found.append(target)
        elif target.is_dir():
            found.extend(sorted(target.rglob("*.md")))
    return found


def test_the_documentation_offers_only_commands_that_exist() -> None:
    """A promised command that does not exist costs a reader a turn to discover, and tells an agent nothing useful."""
    commands = known_commands()
    broken: list[str] = []

    for document in documents():
        text = document.read_text(encoding="utf-8")
        for span in code_spans(text):
            for match in INVOCATION.finditer(span):
                words = [word for word in match.group(1).split() if not word.startswith("-")]
                if not words or words[0] in NOT_COMMANDS:
                    continue
                # The longest prefix that exists wins: `brain init ./demo` is `brain init` plus an argument.
                if any(" ".join(words[:length]) in commands for length in range(len(words), 0, -1)):
                    continue
                broken.append(f"{document.relative_to(ROOT)}: vitruvio {' '.join(words)}")

    assert not broken, "documentation offers commands that do not exist:\n  " + "\n  ".join(sorted(set(broken)))


def test_the_check_would_catch_a_broken_promise(tmp_path: Path) -> None:
    """Otherwise the test above passes because the regex matches nothing, which is the failure mode of every
    grep-based check: it goes green when it stops working."""
    commands = known_commands()
    span = "`vitruvio teleport --now`"
    words = [word for word in INVOCATION.search(span).group(1).split() if not word.startswith("-")]  # type: ignore[union-attr]
    assert words == ["teleport"]
    assert not any(" ".join(words[:length]) in commands for length in range(len(words), 0, -1))


def test_a_real_command_is_recognised() -> None:
    """The other half: the check must not flag something that does exist."""
    commands = known_commands()
    assert "bench" in commands
    assert "config embedder test" in commands
    assert "dist push" in commands


CLI_SKILL = ROOT / "skills" / "vitruvio-cli" / "SKILL.md"
"""The skill whose whole content is the command surface, so it is the one most able to be wrong."""


def documented_commands() -> set[str]:
    """Every command the vitruvio-cli skill names in an inline-code span."""
    text = CLI_SKILL.read_text(encoding="utf-8")
    groups = {command.split()[0] for command in known_commands()}
    found: set[str] = set()
    for span in re.findall(r"`([^`\n]+)`", text):
        words = [word for word in span.strip().split() if not word.startswith("-")]
        # A span is a command only if it starts with a group name: `--json` and `vitruvio.toml` are not.
        if not words or words[0] not in groups:
            continue
        # Trailing placeholders are arguments, not subcommands: `brain init PATH` is `brain init`.
        while words and (words[-1].isupper() or "=" in words[-1] or "-OR-" in words[-1]):
            words.pop()
        if words:
            found.add(" ".join(words))
    return found


def test_the_cli_skill_names_only_commands_that_exist() -> None:
    """The existing check above only sees invocations written as `vitruvio <cmd>`, and this skill writes them bare in
    tables -- seventy-two rows the regex cannot see. Without this, the one document whose entire purpose is the
    command surface would be the least verified thing in the repository."""
    commands = known_commands()
    invented = sorted(command for command in documented_commands() if command not in commands)
    assert not invented, f"the vitruvio-cli skill names commands that do not exist: {invented}"


def test_the_cli_skill_covers_every_group() -> None:
    """A group nobody documented is a group an agent will not find, and the failure is silent: it simply never
    reaches for that command."""
    groups = {command.split()[0] for command in known_commands() if " " in command}
    documented = {command.split()[0] for command in documented_commands()}
    assert not groups - documented, f"undocumented command groups: {sorted(groups - documented)}"


def test_the_counts_in_the_skill_are_the_real_ones() -> None:
    """The skill opens with "fifteen groups, ninety commands". Pinned so that adding a command forces the prose to
    be updated rather than quietly becoming wrong -- the same bargain `reference --check` makes.

    `browse` is a top-level command rather than a group, which is why adding it and its three reading commands moved
    the command count and left the group count alone. `reconcile` moved both: ten commands and a group of their own,
    because joining two histories is not transport and does not belong under `dist`."""
    text = CLI_SKILL.read_text(encoding="utf-8")
    commands = known_commands()
    leaves = [command for command in commands if not any(other.startswith(f"{command} ") for other in commands)]
    groups = {command.split()[0] for command in commands if " " in command}

    words = {15: "Fifteen", 90: "ninety"}
    assert words[15] in text or str(len(groups)) in text, f"there are {len(groups)} groups"
    assert words[90] in text, f"the skill does not state the command count; there are {len(leaves)}"
    assert len(groups) == 15, f"the skill says fifteen groups; there are now {len(groups)}"
    assert len(leaves) == 90, f"the skill says ninety commands; there are now {len(leaves)}"
