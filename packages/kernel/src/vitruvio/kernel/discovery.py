"""Finding the project, finding the brain within it, and merging both with flags and environment.

Two questions, in that order, because the second is answered *inside* the first: a brain name means nothing
until it is known whose vocabulary it belongs to.

**Which project.** A project is a ``vitruvio.toml``, and four layers name one:

1. ``--config FILE`` -- that file, verbatim.
2. ``--project NAME`` -- looked up in the machine's project registry, which is what makes a project
   addressable from *any* directory. This is the layer that lets three terminals drive three projects at once.
3. ``$VITRUVIO_CONFIG`` and ``$VITRUVIO_PROJECT`` -- the same two answers, said by an environment rather than
   by an argument. What one agent's session, a container, or a shell profile exports.
4. The nearest ``vitruvio.toml``, walking up from the working directory -- the answer for a person standing
   inside a project.

**Which brain**, within whatever project that produced:

1. ``--brain NAME`` -- a brain the project declares -- or ``--brain PATH``. An explicit instruction wins.
2. ``$VITRUVIO_BRAIN`` -- how an agent, a container, or a CI job says it without rewriting files.
3. ``[brain].path`` in the project file -- a single-brain project's committed answer.
4. The project's only named brain, when it holds exactly one: there is no ambiguity to resolve, so requiring
   ``--brain`` would be ceremony.
5. What ``brain use`` last recorded **for this project**.
6. Nothing. An error that names the layers -- and in a project of six brains, names the six.

**A project that declares brains has no machine-wide "current brain",** and that subtraction is the point of
this module's current shape. One pointer shared by every terminal cannot describe what vitruvio is actually
used for: several agents, several projects, several subjects, at the same time. Worse, it resolved in
*silence* -- a pointer left by ``brain use`` in one project answered for a different project whose brains
were all addressed by name, and the wrong brain was written to with nobody informed. So the pointer is now
per project (:func:`remember_brain` keys it by project) and the machine-wide one survives only for a brain
that belongs to no project at all, which is the one case it was always right for.

The walk-up resolves a relative path *against the file's directory*, never against the working directory. A
project config that means a different brain depending on which subdirectory you happened to be in is not a
reproducibility artifact.

Selecting a brain and finding a configuration are separate questions, and ``--brain`` affects both: when
names a brain outside the current tree and the walk-up from the working directory finds nothing, the walk
restarts beside the brain. ``brain init`` writes ``vitruvio.toml`` next to the brain it creates, so without
that second look the very next command reads no configuration -- and the symptom is a write refused for want
of an actor that was configured all along. The working directory still wins when both exist.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tomli_w
from pydantic import ValidationError

from vitruvio.kernel.config import ActorSpec, Origin, ProjectConfig, ResolvedConfig
from vitruvio.kernel.errors import BrainNotSelectedError, ConfigError, ProjectNotKnownError
from vitruvio.kernel.paths import CONFIG_FILE, is_layout, state_file

if TYPE_CHECKING:
    from boltzmann.blocks.provenance import ActorKind

ENV_BRAIN = "VITRUVIO_BRAIN"
ENV_CONFIG = "VITRUVIO_CONFIG"
ENV_PROJECT = "VITRUVIO_PROJECT"
ENV_ACTOR_ID = "VITRUVIO_ACTOR_ID"
ENV_ACTOR_KIND = "VITRUVIO_ACTOR_KIND"

PROJECTS_KEY = "projects"
"""The state-file table mapping a project's name to its configuration file.

A registry of *names*, holding no configuration of its own -- every value is a path to a committed
``vitruvio.toml``, and everything about a project is still read from that file. Which is what keeps this
machine-local convenience from becoming a second, unversioned place a project can be configured.
"""

SELECTED_KEY = "selected"
"""The state-file table mapping a project to the brain ``brain use`` last chose *within it*."""


def known_projects() -> dict[str, Path]:
    """
    Every project this machine can address by name.

    Returns:
        dict[str, Path]: Project name to its configuration file, including entries whose file has since been
        moved or deleted -- reported rather than filtered, because a project that vanished is something to be
        told about rather than a name that mysteriously stops resolving.
    """
    table = read_state().get(PROJECTS_KEY, {})
    if not isinstance(table, dict):
        return {}
    return {name: Path(str(path)) for name, path in table.items() if isinstance(path, str) and path}


def register_project(name: str, config_file: Path) -> Path:
    """
    Make a project addressable by name from any directory.

    Args:
        name (str): The project's name, as ``[project].name`` declares it.
        config_file (Path): Its ``vitruvio.toml``.

    Returns:
        Path: The state file that was written.
    """
    state = read_state()
    table = state.get(PROJECTS_KEY)
    projects = dict(table) if isinstance(table, dict) else {}
    projects[name] = str(config_file.expanduser().resolve())
    state[PROJECTS_KEY] = projects
    return write_state(state)


def forget_project(name: str) -> bool:
    """
    Drop a project from the registry, leaving its files entirely alone.

    Args:
        name (str): The project's name.

    Returns:
        bool: Whether there was such an entry.
    """
    state = read_state()
    table = state.get(PROJECTS_KEY)
    projects = dict(table) if isinstance(table, dict) else {}
    if name not in projects:
        return False
    del projects[name]
    state[PROJECTS_KEY] = projects
    write_state(state)
    return True


def project_config_file(name: str) -> Path:
    """
    Resolve a project name to its configuration file.

    Args:
        name (str): The project's name.

    Returns:
        Path: Its ``vitruvio.toml``.

    Raises:
        ProjectNotKnownError: If no project of that name is registered, or its file has since gone. Both
            messages list what *is* registered: a name that does not resolve is nearly always a typo or a
            project registered on another machine, and the list settles which.
    """
    projects = known_projects()
    known = ", ".join(sorted(projects)) or "none"
    candidate = projects.get(name)
    if candidate is None:
        raise ProjectNotKnownError(
            f"no project called {name!r} is registered on this machine (registered: {known})",
            hint="run `vitruvio project register` in its directory, or `vitruvio project list` to see them",
        )
    if not candidate.is_file():
        raise ProjectNotKnownError(
            f"project {name!r} is registered at {candidate}, which no longer exists",
            hint=f"re-register it from where it lives now, or `vitruvio project forget {name}`",
        )
    return candidate


def walk_up(start: Path) -> Path | None:
    """
    The nearest ``vitruvio.toml`` at or above one directory, consulting no environment and no state.

    Split out of :func:`find_config_file` because two callers want the walk *without* the overrides in front
    of it. Asking "which project does this brain belong to" about twenty remembered brains must answer twenty
    different things; through the override layers it would answer ``$VITRUVIO_PROJECT`` twenty times.

    Args:
        start (Path): Where to start.

    Returns:
        Path | None: The file, or ``None`` if the walk reached the root without finding one.
    """
    here = start.resolve()
    for directory in (here, *here.parents):
        candidate = directory / CONFIG_FILE
        if candidate.is_file():
            return candidate
    return None


def find_config_file(start: Path | None = None) -> Path | None:
    """
    Locate the nearest ``vitruvio.toml``, walking up towards the filesystem root.

    Stops at the first hit rather than merging every file on the way up: layered configuration across
    directory levels is a feature nobody asked for and a debugging session everybody remembers.

    Two environment layers come first, and they are read here rather than only in :func:`resolve` so that
    every caller agrees about which file "the configuration" is. ``config set`` writing one file while
    ``config show`` read another is a bug this module has already shipped once.

    Args:
        start (Path | None): Where to start. Defaults to the working directory.

    Returns:
        Path | None: The file, or ``None`` if the walk reached the root without finding one.

    Raises:
        ConfigError: If ``$VITRUVIO_CONFIG`` points at something that is not a file.
        ProjectNotKnownError: If ``$VITRUVIO_PROJECT`` names a project this machine has not registered.
    """
    override = os.environ.get(ENV_CONFIG, "").strip()
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_file():
            raise ConfigError(
                f"{ENV_CONFIG} points at {candidate}, which is not a file",
                hint=f"unset {ENV_CONFIG} or point it at a vitruvio.toml",
            )
        return candidate.resolve()

    named = os.environ.get(ENV_PROJECT, "").strip()
    if named:
        return project_config_file(named)

    return walk_up(start or Path.cwd())


def load_project(path: Path | None) -> ProjectConfig:
    """
    Parse a ``vitruvio.toml``.

    Args:
        path (Path | None): The file, or ``None`` for an all-defaults configuration.

    Returns:
        ProjectConfig: The parsed configuration, carrying its own source path.

    Raises:
        ConfigError: If the file is not valid TOML, or does not match the schema. Both messages name the
            file and the offending key, because a configuration error the user cannot locate is a
            configuration error they cannot fix.
    """
    if path is None:
        return ProjectConfig()

    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path} is not valid TOML: {error}") from error
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error

    try:
        return ProjectConfig(**document, source=path)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}" for issue in error.errors()
        )
        raise ConfigError(f"{path} does not match the vitruvio schema -- {details}") from error


def read_state() -> dict[str, Any]:
    """
    Read the XDG state file, tolerating its absence.

    A corrupt state file is *not* fatal: it holds conveniences (the current brain, cached registry facts),
    never anything that cannot be re-established. Failing a command because a cache is malformed would be
    the wrong trade.

    Returns:
        dict[str, Any]: The parsed state, or an empty mapping.
    """
    path = state_file()
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def write_state(state: dict[str, Any]) -> Path:
    """
    Replace the state file, atomically.

    Args:
        state (dict[str, Any]): The whole document to write.

    Returns:
        Path: The file written.
    """
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".toml.tmp")
    temporary.write_bytes(tomli_w.dumps(state).encode("utf-8"))
    temporary.replace(path)
    return path


def note_brain(brain: Path) -> Path:
    """
    Record that this machine has seen a layout, choosing nothing.

    The ``known`` list is a most-recently-seen history, not a selection, and the two are separated because
    every caller that creates a brain wants the first and none of them want the second: ``project add`` making
    the brain it just created into somebody's default would be a side effect nobody asked for.

    What the history is *for* is answering "which projects exist on this machine" later. A brain created by
    ``project add`` was invisible to that question, so a project whose brains were all created that way could
    not be offered by the browser's picker at all -- which is how this function came to exist.

    Args:
        brain (Path): The layout directory.

    Returns:
        Path: The state file that was written.
    """
    resolved = str(brain.resolve())
    state = read_state()
    known = [item for item in state.get("known", []) if item != resolved]
    state["known"] = [resolved, *known][:20]
    return write_state(state)


def remember_brain(brain: Path, *, project: str | None = None, name: str | None = None) -> Path:
    """
    Record a brain as a default, scoped to a project when it belongs to one.

    The scoping is the whole design. A machine-wide pointer answers "which brain" for *every* terminal at
    once, which is wrong the moment two projects are open -- and it answered silently, so the wrong brain got
    written to rather than a question getting asked. Given a project, the choice is recorded under that
    project's name and reaches nothing else; the machine-wide pointer is written only for a brain that
    belongs to no project.

    Args:
        brain (Path): The layout directory.
        project (str | None): The project this choice belongs to, as :func:`selection_key` computes it.
        name (str | None): The brain's name within that project. Required for a scoped choice, since a
            project selects brains by name.

    Returns:
        Path: The state file that was written.
    """
    resolved = str(brain.resolve())
    state = read_state()
    known = [item for item in state.get("known", []) if item != resolved]
    # Kept regardless of scope: `known` is a most-recently-seen list for `brain list`, not a selection, and
    # a brain being *interesting* is machine-wide even when choosing it is not.
    state["known"] = [resolved, *known][:20]

    if project and name:
        table = state.get(SELECTED_KEY)
        selected = dict(table) if isinstance(table, dict) else {}
        selected[project] = name
        state[SELECTED_KEY] = selected
    else:
        state["current"] = resolved
    return write_state(state)


def selection_key(project: ProjectConfig) -> str | None:
    """
    What to file a project's saved brain choice under.

    Its declared name when it has one, and otherwise its configuration file's path -- so that a project
    nobody has named yet still gets a selection of its own rather than sharing the machine-wide pointer with
    every other unnamed project.

    Args:
        project (ProjectConfig): The loaded configuration.

    Returns:
        str | None: The key, or ``None`` when there is no project at all to key on.
    """
    if project.project.name:
        return project.project.name
    return str(project.source) if project.source is not None else None


def selected_brain(project: ProjectConfig) -> str | None:
    """
    Which brain ``brain use`` last chose within one project.

    Args:
        project (ProjectConfig): The loaded configuration.

    Returns:
        str | None: The brain's name, or ``None`` when this project has no saved choice -- including when the
        saved one has since been removed from the project, which is treated as no choice rather than as an
        error: the answer to "that brain is gone" is the same list of names that a fresh project gets.
    """
    key = selection_key(project)
    if key is None:
        return None
    table = read_state().get(SELECTED_KEY, {})
    if not isinstance(table, dict):
        return None
    chosen = table.get(key)
    if isinstance(chosen, str) and chosen in project.brains:
        return chosen
    return None


def update_config(path: Path, key: str, value: Any) -> Path:
    """
    Set one dotted key in a ``vitruvio.toml`` and write it back.

    The document is round-tripped through a plain dictionary, which **loses comments and reorders nothing
    but formatting**. That is a real cost for a file people are encouraged to comment, so callers should
    say so, and hand-editing stays the recommended path for anything structural. What this exists for is
    the two writes that must not require an editor: ``vitruvio config set`` and the model-tag write-back
    after a vector index is first built.

    Args:
        path (Path): The file to update. Created if absent.
        key (str): A dotted path, e.g. ``actor.id`` or ``planner.rrf_k``.
        value (Any): The value to set. ``None`` removes the key.

    Returns:
        Path: The file written.

    Raises:
        ConfigError: If an intermediate key exists and is not a table, or if the result does not validate.
    """
    document: dict[str, Any] = {}
    if path.is_file():
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            raise ConfigError(f"{path} is not valid TOML: {error}") from error

    parts = key.split(".")
    cursor = document
    for part in parts[:-1]:
        existing = cursor.setdefault(part, {})
        if not isinstance(existing, dict):
            raise ConfigError(f"{key} cannot be set: {part} is a value, not a table")
        cursor = existing

    if value is None:
        cursor.pop(parts[-1], None)
    else:
        cursor[parts[-1]] = value

    # Validate before writing. A config file that the next command refuses to parse is a worse outcome than
    # a rejected `config set`.
    try:
        ProjectConfig(**document)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}" for issue in error.errors()
        )
        raise ConfigError(f"setting {key} would make {path} invalid -- {details}") from error

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".toml.tmp")
    temporary.write_bytes(tomli_w.dumps(document).encode("utf-8"))
    temporary.replace(path)
    return path


def _brain_from_layers(
    brain_flag: Path | None,
    project: ProjectConfig,
) -> tuple[Path, Origin, str | None]:
    """
    Apply the brain-selection precedence.

    A project that holds several brains selects one **by name**, so ``--brain algebra`` and
    ``--brain ./brains/algebra`` both work. The name is tried first: within a project the names are the
    vocabulary, and a directory in the working tree that happens to share a name with a project member must not
    shadow the member -- silently operating on the wrong brain is the failure worth spending a lookup to avoid.

    Args:
        brain_flag (Path | None): The ``--brain`` value, a name or a path.
        project (ProjectConfig): The loaded project configuration.

    Returns:
        tuple[Path, Origin, str | None]: The selected path, which layer chose it, and its project name when it
        has one.

    Raises:
        BrainNotSelectedError: If no layer named a brain.
    """
    if brain_flag is not None:
        named = project.brain_path(str(brain_flag))
        if named is not None:
            return named, Origin.FLAG, str(brain_flag)
        return brain_flag.expanduser().resolve(), Origin.FLAG, None

    from_env = os.environ.get(ENV_BRAIN, "").strip()
    if from_env:
        named = project.brain_path(from_env)
        if named is not None:
            return named, Origin.ENVIRONMENT, from_env
        return Path(from_env).expanduser().resolve(), Origin.ENVIRONMENT, None

    if project.brain.path and project.source is not None:
        # Relative to the file, not to cwd. This is the whole point of the walk-up.
        return (project.source.parent / project.brain.path).expanduser().resolve(), Origin.FILE, None

    if len(project.brains) == 1:
        # A project with exactly one brain has no ambiguity to resolve, so requiring --brain would be ceremony.
        # With two or more it is a real question, and the error below asks it by name.
        only = next(iter(project.brains))
        path = project.brain_path(only)
        if path is not None:
            return path, Origin.FILE, only

    if (chosen := selected_brain(project)) is not None:
        # This project's own saved choice, not the machine's. Two terminals in two projects therefore do not
        # overwrite each other's answer to "which brain", which is what a single pointer did.
        path = project.brain_path(chosen)
        if path is not None:
            return path, Origin.STATE, chosen

    if project.brains:
        # Deliberately *before* the machine-wide pointer, and it raises rather than falling through to it. A
        # project that names its brains has said what the vocabulary is; answering from a pointer another
        # project's `brain use` happened to leave behind would operate on a brain nobody in this command
        # mentioned, and content addressing has no undo for a write into the wrong subject.
        names = ", ".join(sorted(project.brains))
        raise BrainNotSelectedError(
            f"this project holds {len(project.brains)} brains and none was selected",
            hint=f"pass --brain with one of: {names}",
        )

    current = read_state().get("current")
    if isinstance(current, str) and current:
        # Only reachable for a project that declares no brains at all -- the loose brain that `brain use PATH`
        # was always the right answer for.
        return Path(current).expanduser().resolve(), Origin.STATE, None

    raise BrainNotSelectedError(
        "no brain is selected",
        hint=(
            'pass --brain NAME or --brain PATH, set VITRUVIO_BRAIN, add [brain] path = "./brain" to a '
            "vitruvio.toml, or run `vitruvio brain use` once"
        ),
    )


def parse_actor_kind(value: str | ActorKind | None, *, source: str) -> ActorKind | None:
    """
    Coerce a string into the SDK's actor kind, listing the valid values when it will not coerce.

    Exists so that a caller -- the CLI, a future MCP server -- can accept ``--actor-kind agent`` without
    importing ``boltzmann`` itself. Translating a user-supplied string into a protocol value is the kernel's
    job; an interface that reaches for the SDK to do it has reached around the service layer.

    Args:
        value (str | ActorKind | None): What the caller was given.
        source (str): Where it came from, for the error message.

    Returns:
        ActorKind | None: The coerced value, or ``None`` when nothing was given.

    Raises:
        ConfigError: If the string names no actor kind.
    """
    from boltzmann.blocks.provenance import ActorKind as Kind

    if value is None or value == "":
        return None
    if isinstance(value, Kind):
        return value
    try:
        return Kind(value)
    except ValueError as error:
        permitted = ", ".join(item.value for item in Kind)
        raise ConfigError(f"{source} is {value!r}; expected one of: {permitted}") from error


def _actor_from_layers(
    project: ProjectConfig,
    actor_id: str | None,
    actor_kind: str | ActorKind | None,
) -> tuple[ActorSpec, Origin]:
    """
    Apply the actor precedence: flag, then environment, then file, then default.

    Args:
        project (ProjectConfig): The loaded configuration.
        actor_id (str | None): The ``--actor`` value.
        actor_kind (str | ActorKind | None): The ``--actor-kind`` value, coerced if it is a string.

    Returns:
        tuple[ActorSpec, Origin]: The resolved actor and where its identifier came from.
    """

    spec = project.actor
    origin = Origin.FILE if spec.id else Origin.DEFAULT

    env_id = os.environ.get(ENV_ACTOR_ID, "").strip()
    if env_id:
        spec, origin = spec.model_copy(update={"id": env_id}), Origin.ENVIRONMENT

    env_kind = parse_actor_kind(os.environ.get(ENV_ACTOR_KIND, "").strip(), source=ENV_ACTOR_KIND)
    if env_kind is not None:
        spec = spec.model_copy(update={"kind": env_kind})

    if actor_id:
        spec, origin = spec.model_copy(update={"id": actor_id}), Origin.FLAG
    flag_kind = parse_actor_kind(actor_kind, source="--actor-kind")
    if flag_kind is not None:
        spec = spec.model_copy(update={"kind": flag_kind})

    return spec, origin


def select_config_file(
    *,
    project: str | None = None,
    config: Path | None = None,
    brain: Path | None = None,
    start: Path | None = None,
) -> Path | None:
    """
    Answer "which project" -- the first of this module's two questions.

    Kept separate from :func:`resolve` because two callers need only this half: ``config set`` has to write
    the same file ``config show`` reads, and the browser's project picker has to list projects without
    opening any brain.

    Args:
        project (str | None): ``--project``, a name in the machine's registry.
        config (Path | None): ``--config``, taken verbatim.
        brain (Path | None): ``--brain``, used only for the second look described in this module's docstring.
        start (Path | None): Where the walk-up begins. Defaults to the working directory.

    Returns:
        Path | None: The configuration file, or ``None`` when no layer names one.

    Raises:
        ConfigError: If ``--config`` names something that is not a file.
        ProjectNotKnownError: If ``--project`` names a project this machine has not registered.
    """
    if config is not None:
        resolved = config.expanduser().resolve()
        if not resolved.is_file():
            raise ConfigError(f"{resolved} is not a file", hint="check the --config path")
        return resolved

    if project is not None:
        # Above the environment and the walk-up, and it raises rather than falling back: an agent that named a
        # project and silently got the one it happens to be standing in is the failure this flag exists to end.
        return project_config_file(project)

    found = find_config_file(start)
    if found is None and brain is not None:
        # An explicit `--brain` outside the current tree still deserves its own configuration. `brain init` writes
        # `vitruvio.toml` beside the brain it creates, so walking up only from the working directory means the next
        # command against that brain reads *no* configuration at all -- and the first symptom is `source register`
        # refusing to write for want of an actor that was in fact configured. Cwd wins when both exist, because that
        # is the layer the user is standing in.
        found = find_config_file(brain.expanduser().resolve().parent)
    return found


def resolve(
    *,
    brain: Path | None = None,
    config: Path | None = None,
    project: str | None = None,
    actor_id: str | None = None,
    actor_kind: str | ActorKind | None = None,
    start: Path | None = None,
    require_layout: bool = True,
    require_brain: bool = True,
) -> ResolvedConfig:
    """
    Merge flags, environment, file and state into one answer.

    Args:
        brain (Path | None): ``--brain``, a name the project declares or a path.
        config (Path | None): ``--config``, bypassing the walk-up.
        project (str | None): ``--project``, a name in the machine's project registry. What lets one
            invocation state its whole context -- project and brain -- without depending on the working
            directory or on any saved pointer.
        actor_id (str | None): ``--actor``.
        actor_kind (str | ActorKind | None): ``--actor-kind``, as a string or the SDK enum.
        start (Path | None): Where the walk-up begins. Defaults to the working directory.
        require_layout (bool): Whether the selected path must already be an OCI layout. ``brain init`` is
            the one caller that passes ``False``, because it is about to create one.
        require_brain (bool): Whether a brain must be selected at all. The ``project`` commands pass ``False``:
            they are about the project rather than about any one brain, and a project that holds no brains yet
            is the state ``project show`` most needs to be able to report.

    Returns:
        ResolvedConfig: Everything the runtime needs, with the provenance of each answer.

    Raises:
        ConfigError: If the configuration is unreadable or invalid.
        ProjectNotKnownError: If ``--project`` named a project this machine has not registered.
        BrainNotSelectedError: If no layer named a brain.
        BrainNotFoundError: If the named path is not a brain and one was required.
    """
    from vitruvio.kernel.errors import BrainNotFoundError

    config_path = select_config_file(project=project, config=config, brain=brain, start=start)
    # Which layer chose the *project*, reported by `config show` and by the browser's header. The same question
    # the brain's origin answers, and asked for the same reason: `--project` and a walk-up land in the same
    # field, and "why am I in this project" is unanswerable from the path alone.
    if config is not None or project is not None:
        project_origin = Origin.FLAG
    elif os.environ.get(ENV_CONFIG, "").strip() or os.environ.get(ENV_PROJECT, "").strip():
        project_origin = Origin.ENVIRONMENT
    elif config_path is not None:
        project_origin = Origin.FILE
    else:
        project_origin = Origin.DEFAULT

    document = load_project(config_path)
    try:
        selected, origin, brain_name = _brain_from_layers(brain, document)
    except BrainNotSelectedError:
        if require_brain:
            raise
        # Stands in for "no brain", and is never opened: the only callers that get here are asking about the
        # project. A None would ripple an optional through every consumer of ResolvedConfig for one case.
        selected, origin, brain_name = Path.cwd(), Origin.DEFAULT, None
        require_layout = False
    actor, actor_origin = _actor_from_layers(document, actor_id, actor_kind)

    if require_layout and not is_layout(selected):
        detail = "does not exist" if not selected.exists() else "is not an OCI layout"
        raise BrainNotFoundError(
            f"{selected} {detail}, so it is not a brain",
            hint=f"run `vitruvio brain init {selected}` to create one",
        )

    return ResolvedConfig(
        brain=selected,
        brain_origin=origin,
        brain_name=brain_name,
        project=document.model_copy(update={"actor": actor}),
        project_origin=project_origin,
        actor_origin=actor_origin,
        config_file=config_path,
    )
