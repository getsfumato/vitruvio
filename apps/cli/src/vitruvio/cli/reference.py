"""Generating the CLI reference from the app itself.

The skill ships a `cli-reference.md`, and a stale reference is worse than no reference: an agent that trusts a flag
which no longer exists spends its next turn recovering from a usage error. So the file is *generated* from the
cyclopts app -- the same declaration that parses the arguments -- and CI regenerates it and fails if the committed
copy differs.

Run as ``python -m vitruvio.cli.reference`` to print it, or ``--write`` to update the shipped copy.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from cyclopts import App

from vitruvio.cli.main import app


def _reference() -> Path:
    """
    Where the generated reference is written.

    The authored copy at ``skills/`` in the repository root, which is the one under version control and the one the
    package's own ``skills`` symlink points at. Falls back to the packaged path so ``--check`` still answers inside an
    installed environment, where there is no repository to write into.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "skills" / "vitruvio" / "references" / "cli-reference.md"
        if candidate.parent.is_dir() and (parent / "pyproject.toml").is_file():
            return candidate
    return Path(__file__).parent / "skills" / "vitruvio" / "references" / "cli-reference.md"


REFERENCE = _reference()
"""Where the generated file lives, inside the skill that ships it."""

HEADER = """# CLI reference

Generated from the command declarations by `python -m vitruvio.cli.reference`. Do not edit by hand: a reference that
disagrees with the parser is worse than none, because it costs a turn to discover.

Every command accepts the global options `--brain`, `--config`, `--actor`, `--actor-kind`, `--json`, `--quiet`,
`--no-color` and `--verbose`. Pass `--json` whenever something other than a person is reading.
"""


def _first_line(text: str | None) -> str:
    """
    The first sentence of a help string.

    Args:
        text (str | None): The docstring or help text.

    Returns:
        str: One line, or empty.
    """
    if not text:
        return ""
    for line in inspect.cleandoc(text).splitlines():
        if stripped := line.strip():
            return stripped
    return ""


def _parameters(function: Any) -> list[str]:
    """
    The flags a command takes, from its signature.

    Read from the signature rather than from a hand-written list, for the same reason the whole file is generated.

    Args:
        function (Any): The command function.

    Returns:
        list[str]: One entry per parameter.
    """
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):  # pragma: no cover - a builtin would not reach here
        return []

    entries: list[str] = []
    for name, parameter in signature.parameters.items():
        flag = name.rstrip("_").replace("_", "-")
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            entries.append(f"`{flag}...`")
        elif parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            required = "" if parameter.default is not inspect.Parameter.empty else " *(required)*"
            entries.append(f"`--{flag}`{required}")
        elif parameter.default is inspect.Parameter.empty:
            entries.append(f"`{flag}`")
        else:
            entries.append(f"`[{flag}]`")
    return entries


def _commands(group: App) -> list[tuple[str, Any]]:
    """
    The named commands of a group, excluding the ones cyclopts adds.

    Args:
        group (App): The group app.

    Returns:
        list[tuple[str, Any]]: Name and target, in declaration order.
    """
    found: list[tuple[str, Any]] = []
    seen: set[int] = set()
    for name, target in group._commands.items():
        if name.startswith("-") or id(target) in seen:
            continue
        seen.add(id(target))
        found.append((name, target))
    return found


def _function(target: Any) -> Any:
    """
    The function behind a command.

    cyclopts wraps every command in its own ``App`` and keeps the callable on ``default_command``. Reading the
    ``App`` itself instead produces a reference documenting cyclopts' constructor -- ``--console``,
    ``--error-formatter`` -- on every command, which is worse than no reference at all.

    Args:
        target (Any): The registered command.

    Returns:
        Any: The callable, unwrapped.
    """
    function = getattr(target, "default_command", None) or target
    return getattr(function, "__wrapped__", function)


def _section(target: Any, path: list[str], depth: int) -> list[str]:
    """
    One command or one group, and everything under it.

    Recursive, because groups nest: ``config embedder list`` is three levels, and a generator that assumed two
    documented the *middle* level as though it were a command -- which meant emitting cyclopts' own constructor
    (``--console``, ``--error-formatter``) as if those were flags of vitruvio's.

    Args:
        target (Any): The registered command or group.
        path (list[str]): The command words leading here.
        depth (int): Heading depth, capped so a deep tree does not emit ``#######``.

    Returns:
        list[str]: Markdown lines.
    """
    heading = "#" * min(depth, 6)
    children = _commands(target) if getattr(target, "_commands", None) else []

    if not children:
        function = _function(target)
        parameters = " ".join(_parameters(function))
        return [
            f"{heading} `vitruvio {' '.join(path)}` {parameters}".rstrip(),
            "",
            _first_line(getattr(function, "__doc__", "")),
            "",
        ]

    lines = [f"{heading} `vitruvio {' '.join(path)}`", "", _first_line(getattr(target, "help", "")), ""]
    for name, child in children:
        lines += _section(child, [*path, name], depth + 1)
    return lines


def render() -> str:
    """
    The whole reference, as Markdown.

    Returns:
        str: The document.
    """
    lines = [HEADER]
    for name, group in _commands(app):
        lines += _section(group, [name], 2)
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    """
    Print the reference, or write it to the shipped location.

    Args:
        argv (list[str] | None): Arguments. ``--write`` updates the file; ``--check`` compares without writing.

    Returns:
        int: ``0``, or ``1`` when ``--check`` found the committed copy out of date.
    """
    import sys

    arguments = sys.argv[1:] if argv is None else argv
    document = render()

    if "--check" in arguments:
        current = REFERENCE.read_text(encoding="utf-8") if REFERENCE.is_file() else ""
        if current == document:
            return 0
        print(f"{REFERENCE} is out of date; run `python -m vitruvio.cli.reference --write`", file=sys.stderr)
        return 1

    if "--write" in arguments:
        REFERENCE.parent.mkdir(parents=True, exist_ok=True)
        REFERENCE.write_text(document, encoding="utf-8")
        print(f"wrote {REFERENCE}", file=sys.stderr)
        return 0

    print(document, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
