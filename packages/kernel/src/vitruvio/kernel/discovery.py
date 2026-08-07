"""Finding the configuration file, finding the brain, and merging the two with flags and environment.

The precedence is fixed and every layer exists for a reason:

1. ``--brain PATH`` -- an explicit instruction wins.
2. ``$VITRUVIO_BRAIN`` -- how an agent, a container, or a CI job says it without rewriting files.
3. ``[brain].path`` in the nearest ``vitruvio.toml``, walking up from the working directory -- the
   reproducible answer, committed with the project.
4. ``current`` in the XDG state file, written by ``brain use`` -- the interactive convenience.
5. Nothing. An error that names all four, because "no brain selected" with no further detail is the least
   useful message a tool can produce.

The walk-up in (3) resolves a relative path *against the file's directory*, never against the working
directory. A project config that means a different brain depending on which subdirectory you happened to
be in is not a reproducibility artifact.

Selecting a brain and finding a configuration are separate questions, and (1) affects both: when ``--brain``
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
from vitruvio.kernel.errors import BrainNotSelectedError, ConfigError
from vitruvio.kernel.paths import CONFIG_FILE, is_layout, state_file

if TYPE_CHECKING:
    from boltzmann.blocks.provenance import ActorKind

ENV_BRAIN = "VITRUVIO_BRAIN"
ENV_CONFIG = "VITRUVIO_CONFIG"
ENV_ACTOR_ID = "VITRUVIO_ACTOR_ID"
ENV_ACTOR_KIND = "VITRUVIO_ACTOR_KIND"


def find_config_file(start: Path | None = None) -> Path | None:
    """
    Locate the nearest ``vitruvio.toml``, walking up towards the filesystem root.

    Stops at the first hit rather than merging every file on the way up: layered configuration across
    directory levels is a feature nobody asked for and a debugging session everybody remembers.

    Args:
        start (Path | None): Where to start. Defaults to the working directory.

    Returns:
        Path | None: The file, or ``None`` if the walk reached the root without finding one.
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

    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / CONFIG_FILE
        if candidate.is_file():
            return candidate
    return None


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


def remember_brain(brain: Path) -> Path:
    """
    Record a brain as the interactive default, keeping a most-recent-first list of the others.

    Args:
        brain (Path): The layout directory.

    Returns:
        Path: The state file that was written.
    """
    resolved = str(brain.resolve())
    state = read_state()
    known = [item for item in state.get("known", []) if item != resolved]
    state["current"] = resolved
    state["known"] = [resolved, *known][:20]
    return write_state(state)


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
) -> tuple[Path, Origin]:
    """
    Apply the brain-selection precedence.

    Args:
        brain_flag (Path | None): The ``--brain`` value.
        project (ProjectConfig): The loaded project configuration.

    Returns:
        tuple[Path, Origin]: The selected path and which layer chose it.

    Raises:
        BrainNotSelectedError: If no layer named a brain.
    """
    if brain_flag is not None:
        return brain_flag.expanduser().resolve(), Origin.FLAG

    from_env = os.environ.get(ENV_BRAIN, "").strip()
    if from_env:
        return Path(from_env).expanduser().resolve(), Origin.ENVIRONMENT

    if project.brain.path and project.source is not None:
        # Relative to the file, not to cwd. This is the whole point of the walk-up.
        return (project.source.parent / project.brain.path).expanduser().resolve(), Origin.FILE

    current = read_state().get("current")
    if isinstance(current, str) and current:
        return Path(current).expanduser().resolve(), Origin.STATE

    raise BrainNotSelectedError(
        "no brain is selected",
        hint=(
            'pass --brain PATH, set VITRUVIO_BRAIN, add [brain] path = "./brain" to a vitruvio.toml, '
            "or run `vitruvio brain use PATH` once"
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


def resolve(
    *,
    brain: Path | None = None,
    config: Path | None = None,
    actor_id: str | None = None,
    actor_kind: str | ActorKind | None = None,
    start: Path | None = None,
    require_layout: bool = True,
) -> ResolvedConfig:
    """
    Merge flags, environment, file and state into one answer.

    Args:
        brain (Path | None): ``--brain``.
        config (Path | None): ``--config``, bypassing the walk-up.
        actor_id (str | None): ``--actor``.
        actor_kind (str | ActorKind | None): ``--actor-kind``, as a string or the SDK enum.
        start (Path | None): Where the walk-up begins. Defaults to the working directory.
        require_layout (bool): Whether the selected path must already be an OCI layout. ``brain init`` is
            the one caller that passes ``False``, because it is about to create one.

    Returns:
        ResolvedConfig: Everything the runtime needs, with the provenance of each answer.

    Raises:
        ConfigError: If the configuration is unreadable or invalid.
        BrainNotSelectedError: If no layer named a brain.
        BrainNotFoundError: If the named path is not a brain and one was required.
    """
    from vitruvio.kernel.errors import BrainNotFoundError

    config_path: Path | None
    if config is not None:
        config_path = config.expanduser().resolve()
        if not config_path.is_file():
            raise ConfigError(f"{config_path} is not a file", hint="check the --config path")
    else:
        config_path = find_config_file(start)
        if config_path is None and brain is not None:
            # An explicit `--brain` outside the current tree still deserves its own configuration. `brain init` writes
            # `vitruvio.toml` beside the brain it creates, so walking up only from the working directory means the
            # next command against that brain reads *no* configuration at all -- and the first symptom is `source
            # register` refusing to write for want of an actor that was in fact configured. Cwd wins when both exist,
            # because that is the layer the user is standing in.
            config_path = find_config_file(brain.expanduser().resolve().parent)

    project = load_project(config_path)
    selected, origin = _brain_from_layers(brain, project)
    actor, actor_origin = _actor_from_layers(project, actor_id, actor_kind)

    if require_layout and not is_layout(selected):
        detail = "does not exist" if not selected.exists() else "is not an OCI layout"
        raise BrainNotFoundError(
            f"{selected} {detail}, so it is not a brain",
            hint=f"run `vitruvio brain init {selected}` to create one",
        )

    return ResolvedConfig(
        brain=selected,
        brain_origin=origin,
        project=project.model_copy(update={"actor": actor}),
        actor_origin=actor_origin,
        config_file=config_path,
    )
