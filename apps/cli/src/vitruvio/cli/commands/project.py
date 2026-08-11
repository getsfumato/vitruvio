"""``vitruvio project`` -- one project, many brains.

A brain is the unit of *publication*, not the unit of work. A degree has a subject per brain, an agency has a client
per brain, a team has a service per brain -- and the reason to keep them apart rather than in one brain is that each
one is then publishable, installable and droppable on its own. Somebody who wants one subject should not have to pull
five.

What a project adds is the shared part: one actor, one retention policy, one embedder, one registry account. Declare
those once in `vitruvio.toml` and every brain in the project inherits them, which is what stops six brains from
drifting into six different retrieval behaviours.

```toml
[project]
name = "facultad"

[registry]
namespace = "docker.io/you"        # or omit it, and log in with `registry login --from-docker`

[brains.algebra]
path = "./brains/algebra"

[brains.analisis-ii]
path = "./brains/analisis-ii"
```

Then `--brain algebra` selects by **name**, and `dist push` derives `docker.io/you/facultad-algebra` without anyone
writing a registry reference per subject.

A project is also addressable **by name from anywhere**, which is what `register`, `list` and `forget` are for.
`project init` registers the project it creates, so from then on

```console
$ vitruvio --project facultad --brain analisis-numerico search "..."
$ vitruvio --project eticompass --brain metrica-a search "..."
```

both work from any directory, at the same time, in different terminals. Nothing is "active": every invocation says
which project and which brain it means, and two agents running concurrently cannot change each other's answer.
"""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter
from rich.text import Text

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.kernel import ExitCode, VitruvioError

app = App(
    name="project",
    help="Manage a project and the brains it holds.",
    result_action="return_value",
    exit_on_error=False,
)


@app.command(name="init")
def init(
    name: str,
    *,
    description: str | None = None,
    namespace: str | None = None,
) -> ExitCode:
    """Create a project in the working directory.

    Writes a `vitruvio.toml` with no brains yet — add them with `project add`. The project's name prefixes every
    derived repository, so `facultad` + `algebra` publishes to `<namespace>/facultad-algebra`. That prefix is what
    keeps two projects that each have a brain called `notes` from publishing to one repository and silently
    overwriting each other.

    Parameters
    ----------
    name
        The project's name. Becomes part of every repository, so it takes lowercase letters, digits and single
        separators — `facultad`, not `Facultad 2026`.
    description
        What this project is.
    namespace
        `<host>/<account>` under which each brain derives its repository. Omit it and vitruvio uses whichever
        registry account you are logged in as, which is what makes `registry login --from-docker` enough.
    """
    from pathlib import Path

    from pydantic import ValidationError

    from vitruvio.kernel import CONFIG_FILE, ConfigError, ProjectSpec, RegistrySpec, find_config_file, update_config

    console = current().console
    context = current()

    # Validated before a file is written, so a rejected name never leaves a half-made project behind. The pydantic
    # error is translated here rather than allowed to escape: unhandled, it exits 1 -- "a bug in vitruvio" -- for
    # what is a mistyped argument, and prints a traceback instead of the envelope.
    try:
        ProjectSpec(name=name, description=description)
        RegistrySpec(namespace=namespace)
    except ValidationError as error:
        detail = "; ".join(issue["msg"].removeprefix("Value error, ") for issue in error.errors())
        raise ConfigError(detail) from error

    target = context.config or find_config_file() or Path.cwd() / CONFIG_FILE
    if target.exists():
        from vitruvio.kernel import load_project

        existing = load_project(target)
        if existing.project.name and existing.project.name != name:
            raise VitruvioError(
                f"{target} already declares the project {existing.project.name!r}",
                hint="edit it, or run this somewhere else to start a separate project",
            )

    update_config(target, "project.name", name)
    if description:
        update_config(target, "project.description", description)
    if namespace:
        update_config(target, "registry.namespace", namespace)
    if actor := context.actor_id:
        update_config(target, "actor.id", actor)

    # Registered as it is created, so `--project <name>` works from anywhere from the first command onwards.
    # Requiring a second, separate `project register` would make the flag look conditional on ceremony, and the
    # whole point of addressing a project by name is that it needs no directory and no saved state.
    from vitruvio.kernel import register_project

    register_project(name, target)

    view = render.stack(
        render.fields(
            [
                ("project", name),
                ("config", str(target)),
                ("namespace", namespace or "(from your registry login)"),
            ]
        ),
        "",
        render.empty("add a brain with `vitruvio project add <name>`"),
        render.empty(f"reach it from anywhere with `vitruvio --project {name} ...`"),
    )
    return console.emit(
        "project.init",
        {
            "name": name,
            "description": description,
            "namespace": namespace,
            "config_file": str(target),
            "registered": True,
        },
        view=view,
    )


@app.command(name="register")
def register(name: str | None = None, *, path: str | None = None) -> ExitCode:
    """Make a project addressable by `--project <name>` from any directory.

    Records the project's name against its `vitruvio.toml` in vitruvio's own state — a registry of *names*, holding
    no configuration of its own. Everything about the project is still read from the committed file, so registering
    is not a second place a project can be configured; it is only how a name stops depending on the working
    directory.

    `project init` does this for you. This command is for a project you cloned, moved, or created before the
    registry existed.

    Parameters
    ----------
    name
        The name to register it under. Defaults to the project's own `[project].name`.
    path
        The project's directory, or its `vitruvio.toml` directly. Defaults to the one this command would
        otherwise resolve — so `--path ~/brains/facultad` registers a project without changing directory,
        which is what registering several of them in a row wants.
    """
    from pathlib import Path

    from vitruvio.kernel import CONFIG_FILE, load_project, register_project

    console = current().console
    context = current()

    if path is not None:
        given = Path(path).expanduser()
        target = (given / CONFIG_FILE) if given.is_dir() else given
        if not target.is_file():
            raise VitruvioError(
                f"{target} is not a file, so there is no project there to register",
                hint="pass the project's directory, or its vitruvio.toml",
            )
        target = target.resolve()
    else:
        target = context.config_file()  # type: ignore[assignment]
    if target is None:
        raise VitruvioError(
            "there is no vitruvio.toml here to register",
            hint="pass --path, run this inside a project, or `vitruvio project init <name>` first",
        )

    document = load_project(target)
    chosen = name or document.project.name
    if not chosen:
        raise VitruvioError(
            f"{target} declares no [project] name, so there is nothing to register it under",
            hint="pass a name, or set it with `vitruvio config set project.name <name>`",
        )

    register_project(chosen, target)
    brains = ", ".join(sorted(document.brains)) or "(none yet)"
    view = render.stack(
        render.fields([("registered", chosen), ("config", str(target)), ("brains", brains)]),
        "",
        render.empty(f"use it from anywhere with `vitruvio --project {chosen} --brain <brain> ...`"),
    )
    return console.emit(
        "project.register",
        {"name": chosen, "config_file": str(target), "brains": sorted(document.brains)},
        view=view,
    )


@app.command(name="list")
def list_() -> ExitCode:
    """List every project this machine can address by name, and the brains each one holds.

    This is the map of what `--project` accepts. Opens no brain: every column comes from a `vitruvio.toml`, so a
    machine holding twenty projects answers as fast as one holding one.

    A project whose file has since moved or been deleted is listed as missing rather than hidden — a name that
    silently stopped resolving is worse than one that says why.
    """
    from vitruvio.kernel import known_projects, load_project

    console = current().console
    projects = known_projects()

    entries = []
    for name in sorted(projects):
        path = projects[name]
        present = path.is_file()
        brains: list[str] = []
        if present:
            try:
                brains = sorted(load_project(path).brains)
            except VitruvioError:
                # A project whose file no longer parses still belongs on this list, with its brains unknown:
                # `--project` will report the parse error itself, and hiding the name here would make that
                # error look like an unregistered project instead of a broken file.
                present = False
        entries.append({"name": name, "config_file": str(path), "present": present, "brains": brains})

    if not entries:
        console.warn("no projects are registered on this machine yet")
        return console.emit(
            "project.list",
            {"projects": entries},
            view=render.empty("run `vitruvio project register` inside a project, or `vitruvio project init <name>`"),
        )

    table = render.table("project", "brains", "config", "state")
    for entry in entries:
        held = entry["brains"] if isinstance(entry["brains"], list) else []
        table.add_row(
            str(entry["name"]),
            Text(", ".join(held) or "(none)", style="value" if held else "muted"),
            Text(str(entry["config_file"]), style="muted"),
            render.verdict(bool(entry["present"]), yes="", no="missing"),
        )
    return console.emit("project.list", {"projects": entries}, view=table)


@app.command(name="forget")
def forget(name: str) -> ExitCode:
    """Drop a project from this machine's registry.

    Touches nothing on disk: the `vitruvio.toml`, the brains and everything in them are left exactly as they are.
    All that is lost is the ability to say `--project <name>` without standing in the directory.

    Parameters
    ----------
    name
        The project's name.
    """
    from vitruvio.kernel import forget_project, known_projects

    console = current().console
    if not forget_project(name):
        known = ", ".join(sorted(known_projects())) or "none"
        raise VitruvioError(
            f"no project called {name!r} is registered (registered: {known})",
            hint="`vitruvio project list` shows them",
        )
    view = render.stack(
        render.fields([("forgot", name)]),
        "",
        render.empty("nothing was deleted; the project's files are untouched"),
    )
    return console.emit("project.forget", {"name": name}, view=view)


@app.command(name="show")
def show() -> ExitCode:
    """List the project's brains, where each lives, and where each publishes.

    Opens no brain, so this stays fast on a project of any size: what is in a brain is a different question, and
    `brain state` answers it one at a time.

    The `repository` column is what `dist push` would use. When no namespace is configured it is derived from the
    account you are logged in as, so it is worth reading before a first push — it is the whole destination.
    """
    console = current().console
    result = current().service(require_brain=False).project()

    if result["namespace"]:
        destination = str(result["namespace"])
    elif result["account"]:
        destination = f"docker.io/{result['account']}  (from your registry login)"
    else:
        destination = "(nowhere: no namespace, and no registry login)"

    head = render.fields(
        [
            ("project", result["name"] or "(unnamed)"),
            ("config", result["config_file"] or "(none)"),
            ("publishes", destination),
            ("tag", result["tag"]),
        ]
    )
    if not result["brains"]:
        console.warn("this project holds no brains yet; add one with `vitruvio project add <name>`")

    table = render.table("", "brain", "repository", "state", "description")
    for brain in result["brains"]:
        states = []
        # Shown, because a prohibition nobody can see is one somebody works around by accident: the reader would
        # otherwise take the repository column as a statement that a push goes there.
        if not brain["publish"]:
            states.append("publish = false")
        if not brain["exists"]:
            states.append("not created")
        table.add_row(
            Text("*", style="ok") if brain["selected"] else "",
            brain["name"],
            Text(brain["repository"] or "(no repository)", style="muted" if not brain["repository"] else "value"),
            Text(", ".join(states), style="warn"),
            Text(brain["description"] or "", style="muted"),
        )

    if result["namespace"] is None and result["account"] is None and result["brains"]:
        console.warn(
            "no registry namespace is configured and no registry login was found, so these brains have nowhere to "
            "publish. Run `vitruvio registry login docker.io --from-docker`, or set [registry] namespace"
        )
    return console.emit("project.show", result, view=render.stack(head, "", table if result["brains"] else None))


@app.command(name="add")
def add(
    name: str,
    *,
    path: str | None = None,
    description: str | None = None,
    reference: str | None = None,
    no_create: Annotated[bool, Parameter(name=["--no-create"])] = False,
    no_publish: Annotated[bool, Parameter(name=["--no-publish"])] = False,
) -> ExitCode:
    """Add a brain to the project, creating its layout.

    Defaults to `./brains/<name>` beside the configuration file. The name becomes part of the derived repository,
    so it takes lowercase letters, digits and single separators — `analisis-ii`, not `Análisis II`.

    Parameters
    ----------
    name
        The brain's name within the project.
    path
        Where the layout goes. Defaults to `./brains/<name>`.
    description
        What this brain holds. Worth setting: six named brains are unreadable without one.
    reference
        An explicit repository, when the derived one is not what you want.
    no_create
        Register the name without creating a layout, for a brain that already exists elsewhere.
    no_publish
        Refuse `dist push` for this brain. For somebody else's upstream: a pulled brain is a writable working copy
        like any other, so a stray push publishes a fork of it under this project's repository and the two lineages
        diverge with nobody informed. Stops an accident, not an intent.
    """
    console = current().console
    result = (
        current()
        .service(require_brain=False)
        .add_brain(
            name,
            path=path,
            description=description,
            reference=reference,
            create=not no_create,
            publish=not no_publish,
        )
    )
    from pathlib import Path

    from vitruvio.kernel import note_brain, register_project

    # A project that gains a brain is one somebody is working in, so it is worth being addressable. Keeps a
    # project made before the registry existed from needing a separate `project register` nobody would think to run.
    project_name = None
    if (config_file := result.get("config_file")) and (project_name := result.get("project")):
        register_project(str(project_name), Path(str(config_file)))

    if result["created"]:
        # Noted, not selected. A layout this machine has seen is how the browser's picker finds a project later,
        # and a brain created here used to be absent from that history -- so a project whose brains were all made
        # by this command could not be offered at all.
        note_brain(Path(str(result["path"])))

    pairs: list[tuple[str, object]] = [
        ("added", result["name"]),
        ("path", f"{result['path']}{'  (created)' if result['created'] else ''}"),
    ]
    if not result["publish"]:
        pairs.append(("publish", Text("false -- `dist push` will refuse this brain", style="warn")))
    # Named with its project when there is one: `--brain analisis-numerico` alone is only unambiguous from inside
    # the project, and the whole reason this command prints a next step is that people copy it.
    selector = f"--project {project_name} --brain {result['name']}" if project_name else f"--brain {result['name']}"
    view = render.stack(
        render.fields(pairs),
        "",
        render.empty(f"use it with `vitruvio {selector} ...`"),
    )
    return console.emit("project.add", result, view=view)


@app.command(name="remove")
def remove(name: str) -> ExitCode:
    """Unregister a brain from the project.

    The layout on disk is left alone, and its path is printed. "Remove it from this project" and "destroy it" are
    different requests, and a brain may be the only copy of what it holds — so this command only ever does the
    first one.

    Parameters
    ----------
    name
        The brain's name.
    """
    console = current().console
    result = current().service(require_brain=False).remove_brain(name)
    view = render.stack(
        render.fields([("removed", f"{result['name']} from the project"), ("still at", result["path"])]),
        "",
        render.empty("nothing was deleted; remove the directory yourself if that is what you meant"),
    )
    return console.emit("project.remove", result, view=view)
