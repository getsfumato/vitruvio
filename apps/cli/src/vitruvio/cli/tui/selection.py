"""What the browser needs in order to change which brain it is looking at.

The interface used to be handed one already-resolved service and could never be pointed anywhere else, which
made "which brain am I in" a question you answered by quitting. That is the wrong shape for how vitruvio is
actually used -- a project per client or per subject, several of them open at once -- so the browser can now
retarget itself, and this module is the two things that takes: a catalogue of what could be opened, and a
function that opens one.

Both are deliberately here rather than in :mod:`vitruvio.cli.tui.app`. Listing projects reads configuration
files and opens nothing, and resolving a selection is the *kernel's* precedence rather than a second copy of
it -- the browser must not become a place where a brain can be selected by rules the CLI does not share.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def catalogue(current: Path | None = None) -> list[dict[str, Any]]:
    """
    Every project that can be opened, and the brains each one holds.

    Opens no brain and builds no service: every field comes from a ``vitruvio.toml`` plus one ``is_layout``
    check, so a machine holding twenty projects fills the picker as fast as one holding a single project.

    Three sources, because a picker that offered only what somebody had *registered* was empty on the machine of
    the person it was built for. The registry is new; their projects were not, and vitruvio already knew where
    every one of them was:

    1. the project registry -- what ``--project`` accepts;
    2. the configuration file this session started in, registered or not;
    3. the ``vitruvio.toml`` above each brain this machine remembers, from ``brain use`` and ``brain init``.

    Three is the migration path, and it needs no command: a brain vitruvio has opened before is a project it can
    still find. Entries carry ``registered`` so the interface can say which ones ``--project`` does not reach yet.

    Args:
        current (Path | None): The configuration file this session started in, included even when it is not
            registered. Leaving it out would make the picker unable to offer the project you are standing in,
            which is the one case where nobody has needed to register anything.

    Returns:
        list[dict[str, Any]]: One entry per project, by name, each carrying its brains.
    """
    from pathlib import Path as PathType

    from vitruvio.kernel import is_layout, known_projects, load_project, read_state, selected_brain, walk_up

    # Keyed by the *resolved* path. The registry stores resolved paths and the session's own configuration file
    # arrives however the command line spelled it, so on a machine where the temporary or home directory is a
    # symlink -- macOS, every container -- the same project was offered twice under two spellings of one path.
    registered = {path.resolve() for path in known_projects().values()}
    files: dict[Path, Path] = {path.resolve(): path for path in known_projects().values()}
    if current is not None:
        files.setdefault(current.resolve(), current)

    state = read_state()
    remembered = [state.get("current"), *state.get("known", [])]
    for item in remembered:
        if not isinstance(item, str) or not item:
            continue
        layout = PathType(item)
        # `is_layout` first: a remembered path is a brain that may since have been deleted, and most of what
        # accumulates in that list is scratch directories that are already gone.
        if not is_layout(layout):
            continue
        # `walk_up` rather than `find_config_file`: the latter answers `$VITRUVIO_PROJECT` first, which would
        # make every remembered brain report the same project.
        if (beside := walk_up(layout.parent)) is not None:
            files.setdefault(beside.resolve(), beside)

    here = current.resolve() if current is not None else None
    entries: list[dict[str, Any]] = []
    for path in files:
        if not path.is_file():
            continue
        try:
            document = load_project(path)
        except Exception:
            # A project whose file will not parse is skipped rather than shown as an empty one: the picker's job
            # is to offer things that can be opened, and `config show` is where a broken file gets diagnosed.
            continue

        chosen = selected_brain(document)
        brains: list[dict[str, Any]] = []
        for name in sorted(document.brains):
            declared = document.brain_path(name)
            brains.append(
                {
                    "name": name,
                    "path": str(declared) if declared else None,
                    "present": bool(declared and is_layout(declared)),
                    "description": document.brains[name].description,
                    "selected": name == chosen,
                }
            )
        if not brains and document.brain.path:
            # A single-brain project names its brain in `[brain].path` and has no name for it. Offered anyway,
            # with `name` left None, because "this project's brain" is still a thing a reader wants to open.
            only = (path.parent / document.brain.path).expanduser().resolve()
            brains.append(
                {
                    "name": None,
                    "path": str(only),
                    "present": is_layout(only),
                    "description": "the project's only brain",
                    "selected": True,
                }
            )

        entries.append(
            {
                "project": document.project.name,
                "label": document.project.name or f"{path.parent.name}/",
                # The resolved path, so that a caller matching this against its own configuration file compares
                # one spelling of it rather than two.
                "config_file": str(path),
                "description": document.project.description,
                "brains": brains,
                "current": path == here,
                # Whether `--project <name>` reaches it. Shown, because a project offered here that the CLI
                # cannot address by name is a difference somebody will otherwise discover from the CLI refusing.
                "registered": path in registered and document.project.name is not None,
            }
        )

    return sorted(entries, key=lambda entry: str(entry["label"]))


def open_selection(config_file: Path, brain: str | None) -> dict[str, Any]:
    """
    Resolve one project-and-brain pair and build a service over it.

    Goes through :func:`vitruvio.kernel.resolve` with the pair stated explicitly, so the browser's picker and a
    ``vitruvio --project x --brain y`` command line arrive at the same brain by the same rules. The actor
    overrides from the invocation are carried through: a selection made inside the browser is still the same
    session, and a write from it must be attributed to whoever the command line said.

    Args:
        config_file (Path): The project's configuration file.
        brain (str | None): A brain the project declares, or ``None`` for a single-brain project's own.

    Returns:
        dict[str, Any]: The ``service``, and what the header and ``i`` need to say about it.
    """
    from pathlib import Path as PathType

    from vitruvio.cli.context import current as invocation_context
    from vitruvio.kernel import resolve
    from vitruvio.runtime import BrainService

    invocation = invocation_context()
    config = resolve(
        brain=PathType(brain) if brain is not None else None,
        config=config_file,
        actor_id=invocation.actor_id,
        actor_kind=invocation.actor_kind,
    )
    return {
        "service": BrainService(config),
        "brain": str(config.brain),
        "origin": config.brain_origin.value,
        "name": config.brain_name,
        "project": config.project_name,
    }


__all__ = ["catalogue", "open_selection"]
