"""``vitruvio skills`` -- installing the agent-facing documentation into a consumer's repository.

The paper describes a **skill** as one of a brain's access contracts, alongside the CLI itself. This command is what
makes that real: any repository holding a brain can obtain the skills without cloning vitruvio, so the knowledge of
*how to drive this* travels with the brain rather than living in vitruvio's own docs.

The skills are **authored** at ``skills/`` in the repository root, and ``src/vitruvio/cli/skills`` is a symlink to
it. One copy under version control, two addresses: the root is where a human edits them and where a tool that
installs skills without pip finds them, and the packaged path is what puts them in the wheel -- which is what ties a
skill to the version of the CLI it documents. A skill installed from a different release than the binary it drives is
the failure that arrangement exists to prevent, and it is also why ``cli-reference.md`` is generated from the command
declarations rather than written by hand.

A symlink rather than hatchling's ``force-include``, and the difference is not stylistic. A force-include reaching
outside the project directory cannot be represented in an **sdist**, so building the wheel from one failed with
"Forced include not found" -- which is to say ``pip install vitruvio`` would break for anyone whose installer
preferred the sdist. Building the wheel directly worked, which is exactly why that went unnoticed until an install
from the sdist was actually attempted.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter
from rich.text import Text

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.kernel import ExitCode, VitruvioError

app = App(
    name="skills",
    help="Install the agent-facing skills into a repository.",
    result_action="return_value",
    exit_on_error=False,
)

PACKAGED = Path(__file__).parent.parent / "skills"
"""Where the shipped copies live inside an installed wheel."""


def _authored() -> Path | None:
    """
    The repository's own ``skills/``, when this is running from a working copy rather than a wheel.

    Walks up looking for a directory that holds both ``skills`` and ``pyproject.toml``, so a stray ``skills``
    directory somewhere above an installed environment cannot be mistaken for vitruvio's.

    Returns:
        Path | None: The authored directory, or ``None`` when there is none above this file.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "skills"
        if candidate.is_dir() and (parent / "pyproject.toml").is_file():
            return candidate
    return None


def _source() -> Path:
    """
    Where to copy skills from: the authored directory when there is one, else the packaged copy.

    Authored first. In an installed wheel :func:`_authored` returns ``None`` anyway -- it requires a
    ``pyproject.toml`` beside the ``skills`` directory, and an installed environment has neither -- so the order
    costs nothing there, and in a working copy it guarantees that ``skills install`` copies the file a human just
    edited rather than any copy made at install time.
    """
    return _authored() or PACKAGED


SOURCE = _source()
"""Resolved once at import, because it cannot change within a process."""

DEFAULT_TARGET = Path(".claude") / "skills"
"""Where Claude Code looks. Overridable, because it is not the only agent runtime."""


def _available() -> list[Path]:
    """
    The skill directories this build ships.

    Returns:
        list[Path]: One directory per skill, sorted, each holding a ``SKILL.md``.
    """
    if not SOURCE.is_dir():  # pragma: no cover - only if package data was excluded from the wheel
        return []
    return sorted(item for item in SOURCE.iterdir() if item.is_dir() and (item / "SKILL.md").is_file())


@app.command(name="list")
def list_() -> ExitCode:
    """List the skills this build ships, and what each one covers."""
    console = current().console
    skills = _available()

    records = []
    for skill in skills:
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        description = ""
        for line in text.splitlines():
            if line.startswith("description:"):
                description = line.split(":", 1)[1].strip()
                break
        references = sorted(item.name for item in (skill / "references").glob("*.md"))
        records.append({"name": skill.name, "description": description, "references": references})

    table = render.table("skill", "covers", "references")
    for record in records:
        table.add_row(
            str(record["name"]),
            # Not truncated any more. The description is what tells an agent whether the skill is the one it
            # needs, and the 110-character cut was there only because a fixed-width column had to end somewhere.
            Text(str(record["description"])),
            Text(", ".join(str(item) for item in record["references"]), style="muted"),
        )
    if not records:
        console.warn("this build ships no skills, which means the package data was not included in the wheel")
    return console.emit(
        "skills.list",
        {"skills": records, "source": str(SOURCE)},
        view=table if records else render.stack(),
    )


@app.command(name="install")
def install(
    *,
    into: Annotated[Path | None, Parameter(name=["--into"])] = None,
    skill: Annotated[list[str] | None, Parameter(name=["--skill", "-s"], negative=())] = None,
    force: bool = False,
) -> ExitCode:
    """Copy the skills into a repository so an agent can read them.

    Writes to `.claude/skills/` by default. An existing skill directory is left alone unless `--force`: a consumer
    may have edited one, and silently overwriting local edits is not something a copy command should do.

    Parameters
    ----------
    into
        Where to write. Defaults to `.claude/skills` under the working directory.
    skill
        Install only these. Repeatable. Defaults to all of them.
    force
        Overwrite skills that are already there.
    """
    console = current().console
    target = (into or DEFAULT_TARGET).expanduser().resolve()
    available = {item.name: item for item in _available()}

    wanted = list(skill) if skill else sorted(available)
    if unknown := [name for name in wanted if name not in available]:
        raise VitruvioError(
            f"no such skill: {', '.join(unknown)}",
            hint=f"available: {', '.join(sorted(available)) or '(none)'}",
        )

    target.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    skipped: list[str] = []
    for name in wanted:
        destination = target / name
        if destination.exists() and not force:
            skipped.append(name)
            continue
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(available[name], destination)
        installed.append(name)

    if skipped:
        console.warn(f"already present, left untouched: {', '.join(skipped)} (use --force to overwrite)")

    view = render.stack(
        render.fields(
            [
                ("target", str(target)),
                ("installed", ", ".join(installed) or "(nothing new)"),
            ]
        ),
        "",
        render.empty("an agent should start from the `vitruvio` skill; the others are reached from it"),
    )
    return console.emit(
        "skills.install",
        {"target": str(target), "installed": installed, "skipped": skipped},
        view=view,
    )
