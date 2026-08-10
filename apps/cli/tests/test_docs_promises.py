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

DOCUMENTS = ("docs", "apps/cli/src/vitruvio/cli/skills", "README.md")

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
