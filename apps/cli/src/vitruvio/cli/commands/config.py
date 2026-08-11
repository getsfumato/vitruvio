"""``vitruvio config`` -- inspect and edit the project configuration.

These commands are the reason the kernel is its own distribution. They must run in tens of milliseconds
with pydantic as the heaviest import, so nothing here may reach for the runtime, an index engine, or an
embedder. ``config show`` in particular has to work when the configuration is *broken*, which is exactly
when a user needs it, so it reports what it found rather than requiring a valid brain.
"""

from __future__ import annotations

import json as jsonlib
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from cyclopts import App, Parameter

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.kernel import (
    ConfigError,
    ExitCode,
    Secret,
    load_project,
    paths,
    provider_key,
    registry_credentials,
    update_config,
)

if TYPE_CHECKING:
    from rich.table import Table

    from vitruvio.kernel import ProjectConfig

app = App(
    name="config", help="Inspect and edit the project configuration.", result_action="return_value", exit_on_error=False
)


def _target() -> Path | None:
    """
    Which configuration file these commands operate on.

    One function for all five, and it deliberately asks the *same* question every other command asks -- it is
    literally the kernel's own project selection: `--config`, then `--project`, then the environment, then the walk
    up from the working directory, then the file beside an explicitly named `--brain`. Without that last layer,
    `config set --brain elsewhere/demo` wrote a brand new `vitruvio.toml` into the working directory, silently,
    while `config show` for the same brain read a different file. Two commands disagreeing about which file is
    "the configuration" is worse than either being wrong.

    The one deviation is that `--config` is taken verbatim without having to exist. These commands are the ones
    that *create* a configuration file -- `vitruvio --config new.toml config set actor.id ...` is how a project
    starts -- so the kernel's insistence that the path be a file already is right for every other caller and wrong
    for this one.

    Returns:
        Path | None: The file, or ``None`` when no configuration exists anywhere.
    """
    context = current()
    if context.config is not None:
        return context.config
    return context.config_file()


def _redact(document: dict[str, Any]) -> dict[str, Any]:
    """
    Replace anything that looks like a secret with a redaction.

    There is no field for a secret in the schema, so in principle nothing here needs redacting. In practice
    people put tokens in files anyway, and a ``config show`` that helpfully prints one into a terminal
    transcript is a bad afternoon. This is a belt on top of the structural braces.

    Args:
        document (dict[str, Any]): The dumped configuration.

    Returns:
        dict[str, Any]: The same shape, with suspicious leaves masked.
    """
    suspicious = ("token", "password", "secret", "api_key", "apikey", "key")

    def walk(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {name: walk(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [walk(item, key) for item in value]
        if isinstance(value, str) and any(word in key.lower() for word in suspicious):
            return Secret(value, source="file").masked()
        return value

    walked: dict[str, Any] = walk(document)
    return walked


def _describe(project: ProjectConfig, source: Path | None) -> Table:
    """
    Render the configuration for a human.

    Args:
        project (ProjectConfig): The loaded configuration.
        source (Path | None): Where it came from.

    Returns:
        Table: The label-and-value block to print.
    """
    from rich.text import Text

    actor = project.actor
    text = project.text_embedder
    vision = project.vision_embedder
    username, token = registry_credentials()

    from vitruvio.kernel import known_projects, selected_brain

    name = project.project.name
    registered = name is not None and name in known_projects()
    # Whether `--project <name>` reaches this project is the first thing to know about it now that a name is how
    # an invocation addresses one, and it is invisible in the file: the registry is machine state, not config.
    if name is None:
        addressable = "(unnamed -- set [project] name to address it by name)"
    elif registered:
        addressable = f"{name}  (registered: --project {name} works from anywhere)"
    else:
        addressable = f"{name}  (not registered -- run `vitruvio project register`)"

    if project.brains:
        chosen = selected_brain(project)
        declared = ", ".join(f"{brain}*" if brain == chosen else brain for brain in sorted(project.brains))
        brains = f"{declared}" + ("   * this project's saved default" if chosen else "")
    else:
        brains = project.brain.path or "(not set)"

    pairs: list[tuple[str, Any]] = [
        ("config file", source or "(none -- using defaults)"),
        ("project", addressable),
        ("brain", brains),
        ("actor", f"{actor.id or '(not set)'} [{actor.kind.value}]"),
        (
            "policy",
            (
                f"{project.policy.profile.value}"
                f"  canonical drops: {'allowed' if project.policy.build().canonical_drop_allowed else 'refused'}"
            ),
        ),
        ("text embed", text.uri + (f" @{text.revision}" if text.revision else "")),
        ("vision embed", vision.uri if vision else "(none -- images are not embedded)"),
        ("indices", f"{len(project.indices)} registered{' (defaults)' if not project.index else ''}"),
        (
            "registry",
            f"{project.registry.reference or '(not set)'}:{project.registry.tag}"
            + ("  [insecure]" if project.registry.insecure else ""),
        ),
        (
            "credentials",
            f"{username or '(none)'} / {token.masked() if token else '(none)'}"
            + (f"  from {token.source}" if token else ""),
        ),
        ("state file", str(paths.state_file())),
        ("model cache", str(paths.model_cache())),
    ]

    missing = [name for name in ("openai", "voyage", "cohere", "anthropic") if provider_key(name) is None]
    if missing:
        pairs.append(("absent keys", Text(f"{', '.join(missing)} (only needed for those providers)", style="muted")))
    return render.fields(pairs)


@app.command(name="show")
def show(*, effective: bool = False) -> ExitCode:
    """Print the configuration, and where it came from.

    Works even when the configuration is invalid, because that is when you need it. Secrets are always
    masked, in both human and JSON output.

    Parameters
    ----------
    effective
        Include the defaults that were filled in, rather than only what the file states.
    """
    console = current().console
    source = _target()
    project = load_project(source)

    if console.json_mode:
        document = project.model_dump(mode="json", exclude_defaults=not effective)
        payload = {
            "config_file": str(source) if source else None,
            "config": _redact(document),
            "index_count": len(project.indices),
            "indices_are_defaults": not project.index,
            "state_file": str(paths.state_file()),
            "model_cache": str(paths.model_cache()),
        }
        return console.emit("config.show", payload)

    return console.emit("config.show", view=_describe(project, source))


@app.command(name="path")
def path() -> ExitCode:
    """Print the path of the configuration file that would be used."""
    console = current().console
    found = _target()
    if found is None:
        console.warn("no vitruvio.toml found; defaults are in use")
        return console.emit("config.path", {"config_file": None}, lines=[])
    return console.emit("config.path", {"config_file": str(found)}, lines=[str(found)])


@app.command(name="get")
def get(key: str) -> ExitCode:
    """Print one value, addressed by dotted key.

    Parameters
    ----------
    key
        A dotted path, e.g. `actor.id` or `planner.rrf_k`.
    """
    console = current().console
    source = _target()
    project = load_project(source)

    cursor: Any = project.model_dump(mode="json")
    for part in key.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            raise ConfigError(
                f"{key} is not set",
                hint="run `vitruvio config show --effective --json` to see every key and its default",
            )
        cursor = cursor[part]

    # A bare value on stdout, unstyled and unwrapped: `vitruvio config get actor.id` is the one command here
    # whose output is *meant* to land in a shell variable, so it stays a line rather than becoming a renderable.
    rendered = cursor if isinstance(cursor, str) else jsonlib.dumps(cursor, default=str)
    return console.emit("config.get", {"key": key, "value": cursor}, lines=[rendered])


@app.command(name="set")
def set_(
    key: str,
    value: str,
    *,
    file: Annotated[
        Path | None, Parameter(name=["--file"], help="Which vitruvio.toml to write. Defaults to the nearest one.")
    ] = None,
) -> ExitCode:
    """Set one value, addressed by dotted key.

    The file is round-tripped through a plain document, so **comments are lost**. Hand-editing stays the
    better path for anything structural; this exists for the edits that should not need an editor.

    Parameters
    ----------
    key
        A dotted path, e.g. `actor.id`.
    value
        The new value. Parsed as JSON when it looks like JSON, so `true`, `42` and `["a","b"]` land as the
        types they look like rather than as strings.
    file
        Which file to write.
    """
    console = current().console
    # The brain's neighbour before the working directory's fallback: writing a new file at cwd for a brain that
    # already has one beside it is how `config set` and `config show` came to read different files.
    target = file or _target() or Path.cwd() / paths.CONFIG_FILE

    try:
        parsed: Any = jsonlib.loads(value)
    except jsonlib.JSONDecodeError:
        parsed = value

    written = update_config(target, key, parsed)
    console.warn(f"comments in {written} were not preserved")
    return console.emit(
        "config.set",
        {"config_file": str(written), "key": key, "value": parsed},
        view=render.fields([("set", key), ("in", str(written))]),
    )


@app.command(name="validate")
def validate() -> ExitCode:
    """Check that the configuration parses and satisfies the schema.

    Exits 3 when it does not, so this is usable as a CI gate on a committed vitruvio.toml.
    """
    console = current().console
    source = _target()
    if source is None:
        console.warn("no vitruvio.toml found; nothing to validate")
        return console.emit(
            "config.validate",
            {"config_file": None, "valid": True},
            view=render.verdict(True, yes="ok (defaults)"),
        )

    project = load_project(source)
    payload = {
        "config_file": str(source),
        "valid": True,
        "index_count": len(project.indices),
        "policy_profile": project.policy.profile.value,
    }
    return console.emit("config.validate", payload, view=render.fields([("ok", str(source))]))


embedder_app = App(
    name="embedder",
    help="Inspect and test the configured embedding providers.",
    result_action="return_value",
    exit_on_error=False,
)
app.command(embedder_app)


@embedder_app.command(name="list")
def embedder_list() -> ExitCode:
    """List the embedding providers this build knows, and whether each can run.

    Read the `semantic` column. Hashed features rank, and rank plausibly — a brain built with the zero-dependency
    default looks exactly like one built with a real model right up until you notice it never finds a synonym.
    """
    console = current().console
    result = current().service(require_brain=False).embedders()

    from rich.text import Text

    text = result["text"]
    vision = result["vision"]
    configured = f"{vision['provider']}:{vision['model']}" if vision else "(none)"
    head = render.fields(
        [
            (
                "text",
                Text(
                    f"{text['provider']}:{text['model']}",
                    style="value" if result["semantic"] else "warn",
                ).append("" if result["semantic"] else "   (hashed features, not semantics)"),
            ),
            ("vision", configured),
        ]
    )
    table = render.table("", "provider", "ranks by", "detail")
    for row in result["providers"]:
        table.add_row(
            render.verdict(bool(row["installed"]), yes="ok", no="---"),
            row["provider"],
            Text("semantic" if row["semantic"] else "hashed", style="value" if row["semantic"] else "warn"),
            Text("" if row["installed"] else f"install {row['extra']}", style="muted"),
        )

    return console.emit("config.embedder.list", result, view=render.stack(head, "", table))


@embedder_app.command(name="test")
def embedder_test(
    *,
    which: str = "text",
    text: str | None = None,
) -> ExitCode:
    """Embed one phrase and report what came back.

    The number to read is the **width**. A remote model's dimensionality is what the model tag carries, and vitruvio
    refuses to guess it — so for a model it does not already know, this is how you find the value to put in
    `dims` under `[embedding.text]`.

    Parameters
    ----------
    which
        `text` or `vision`.
    text
        What to embed. Defaults to a Spanish phrase, so a model that only handles English shows up as a plausible
        vector rather than as an error.
    """
    console = current().console
    result = current().service(require_brain=False).test_embedder(which=which, text=text)

    view = render.fields(
        [
            ("provider", f"{result['provider']}:{result['model']}"),
            # The width is what a caller came here for -- it is the value to put in `dims` -- so it is the one
            # field with weight on it.
            ("width", render.count(result["measured_dimensions"])),
            ("normalized", render.verdict(bool(result["normalized"]))),
            ("elapsed", f"{result['elapsed_ms']} ms"),
            ("tag", result["tag"]),
        ]
    )
    if not result["semantic"]:
        console.warn(
            "this embedder hashes features rather than modelling meaning: it will match a plural to its singular "
            "through the shared analyzer, and will never find a synonym"
        )
    if result["measured_dimensions"] != result["declared_dimensions"]:
        console.warn(
            f"the model returned {result['measured_dimensions']} dimensions and the tag claims "
            f"{result['declared_dimensions']}; set dims = {result['measured_dimensions']}"
        )
    return console.emit("config.embedder.test", result, view=view)
