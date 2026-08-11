"""``vitruvio index`` -- build, inspect and reclaim the derived views.

Indices are **derived**: the blocks are the record, and every structural index is a deterministic function of
them. Deleting the whole ``.vitruvio/`` directory costs time and never knowledge, which is the property that makes
these commands safe to run.

The one exception, when it lands, is the vector index: rebuilding it needs a model, so it is persisted and it
travels inside the published artifact. ``index list`` marks it, because a brain published without one is a brain
nobody else can search semantically.
"""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter
from rich.console import RenderableType
from rich.text import Text

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.kernel import ExitCode

STATE_STYLES = {"ready": "ok", "empty": "warn", "stale": "warn", "unavailable": "bad", "absent": "muted"}
"""How an index's state is coloured. An empty registered index is yellow rather than green: the planner treats
one as unusable, and it looks identical to a built index in every column except this one."""


def _state(value: str) -> Text:
    """
    An index state, styled by what it means for retrieval.

    Args:
        value (str): The state the service reported.

    Returns:
        Text: The styled state.
    """
    return Text(value, style=STATE_STYLES.get(value, "value"))


app = App(
    name="index",
    help="Build and inspect the derived indices.",
    result_action="return_value",
    exit_on_error=False,
)


@app.command(name="list")
def list_() -> ExitCode:
    """List every registered index: kind, module, how much it holds, and where it lives.

    A population of zero on a registered index is worth looking at. An empty index does not announce itself, so
    the planner treats one as unusable rather than as an index that matches nothing -- but the reason it is empty
    is usually that `index build` has not run.
    """
    console = current().console
    result = current().service().index_list()

    if not result["indices"]:
        view: RenderableType = render.empty("No indices registered.")
    else:
        table = render.table("module", "kind", ("blocks", "right"), ("size", "right"), "state", "engine")
        for row in result["indices"]:
            table.add_row(
                render.kind(row["memory_type"]),
                row["kind"],
                str(row["population"]),
                Text(f"{row['size_bytes'] / 1024:.1f}K" if row["size_bytes"] else "-", style="muted"),
                _state(row["state"]),
                Text(row["engine"], style="muted"),
            )
        view = table

    if unavailable := result["unavailable"]:
        # One line rather than one per entry. The per-index detail stays in the JSON envelope, where whatever is
        # driving the CLI reads it; eleven warnings above a fifteen-row table just hides the table.
        kinds = sorted({name.split(".", 1)[1] for name in unavailable})
        console.warn(
            f"{len(unavailable)} declared index(es) are not available in this build: {', '.join(kinds)} "
            f"(pass --json for the full list)"
        )
    return console.emit("index.list", result, view=view)


@app.command(name="build")
def build(
    *,
    memory_type: Annotated[list[str] | None, Parameter(name=["--memory-type", "-m"], negative=())] = None,
    force: bool = False,
) -> ExitCode:
    """Build or refresh the indices.

    Cheap to repeat: each index diffs the incoming block set against what it holds and applies only the
    difference, so a rebuild after adding one block costs one block's work.

    Parameters
    ----------
    memory_type
        Restrict to these modules. Repeatable.
    force
        Discard what is held and rebuild from scratch. For when a sidecar is suspect rather than stale.
    """
    console = current().console
    result = current().service().index_build(memory_types=memory_type, force=force)

    table = render.table("module", "kind", ("blocks", "right"), "root")
    for row in result["indices"]:
        table.add_row(
            render.kind(row["memory_type"]),
            row["kind"],
            str(row["population"]),
            render.digest(row["bound_root"]),
        )

    travelling = result.get("travelling") or []
    footer = render.fields(
        [
            ("written", f"{result['written']} index files to {result['home']}"),
            (
                "travelling",
                Text(", ".join(travelling), style="ok") if travelling else Text("(none)", style="warn"),
            ),
        ]
    )
    for module, outcome in sorted((result.get("vouched") or {}).items()):
        if outcome != "vouched":
            # A warning rather than a line: an unvouched vector index means a publish that silently omits the one index
            # a consumer cannot rebuild for itself.
            console.warn(f"{module}: the vector index will not be published -- {outcome}")
    return console.emit("index.build", result, view=render.stack(table, footer))


@app.command(name="stats")
def stats(
    *,
    memory_type: Annotated[str | None, Parameter(name=["--memory-type", "-m"])] = None,
) -> ExitCode:
    """Print the statistics the query planner costs against.

    `freshness` is the line to read first. Stale means the statistics describe a different composition than the
    one installed, and the planner responds by estimating pessimistically rather than by trusting them -- so a
    stale catalogue makes retrieval slower, not wrong.

    Parameters
    ----------
    memory_type
        Restrict to one module.
    """
    console = current().console
    result = current().service().index_stats(memory_type=memory_type)

    parts: list[RenderableType] = []
    for entry in result["statistics"]:
        fresh = entry["freshness"]
        pairs: list[tuple[str, object]] = [
            # Freshness first, and coloured: stale does not mean wrong, it means the planner stops trusting the
            # catalogue and estimates pessimistically instead. That is a slower brain, and it is the one line here
            # that tells you so.
            (
                "freshness",
                Text(
                    fresh + (f"  ({entry['reason']})" if entry.get("reason") else ""),
                    style="ok" if fresh == "fresh" else "warn",
                ),
            ),
            ("blocks", f"{entry['cardinality']} ({entry['resolvable']} resolvable)"),
            ("indices", ", ".join(entry["indices"]) or "(none)"),
            ("built at", entry["built_at"] or "-"),
        ]
        if entry["columns"]:
            distinct = ", ".join(f"{name}={count}" for name, count in entry["columns"].items() if count)
            pairs.append(("facets", distinct or "(none populated)"))
        if entry["vocabulary"]:
            pairs.append(("vocabulary", f"{entry['vocabulary']} terms, {entry['postings']} postings"))
        if entry["graph_edges"]:
            pairs.append(("graph", f"{entry['graph_edges']} edges"))
        if vectors := entry["vectors"]:
            # A mapping of space to population, written the way `facets` above is rather than as a Python repr:
            # `{'text': 1}` was what this printed, and a dict literal in human output is a renderer that gave up.
            pairs.append(
                (
                    "vectors",
                    ", ".join(f"{space}={count}" for space, count in sorted(vectors.items()))
                    if isinstance(vectors, dict)
                    else str(vectors),
                )
            )
        parts += [render.fields(pairs, title=entry["memory_type"]), ""]
    if not parts:
        parts = [render.empty("No statistics yet. Run `vitruvio index build`.")]
    return console.emit("index.stats", result, view=parts)


@app.command(name="verify")
def verify() -> ExitCode:
    """Check every index against the composition it claims to describe.

    Exits 5 when an index is stale, so this is usable as a gate before a publish. Staleness is not corruption:
    the fix is `index build`, and the message says so.
    """
    console = current().console
    result = current().service().index_verify()

    if not result["capabilities"]:
        view: RenderableType = render.empty("No indices registered.")
    else:
        table = render.table("module", "kind", "state", "detail")
        for row in result["capabilities"]:
            table.add_row(
                render.kind(row["memory_type"]),
                row["kind"],
                _state(row["state"]),
                Text(row["detail"] or "", style="muted"),
            )
        view = table

    if result["stale"]:
        from vitruvio.kernel import VitruvioError

        raise VitruvioError(
            f"{result['stale']} index(es) describe a different composition than the one installed",
            hint="run `vitruvio index build` -- an index is derived, so rebuilding it is always safe",
        )
    return console.emit("index.verify", result, view=view)


@app.command(name="gc")
def gc(*, apply: bool = False) -> ExitCode:
    """Delete index files that no longer belong to a declared index.

    A dry run by default, mirroring `retain prune`: a command that deletes files should have to be asked twice.

    Parameters
    ----------
    apply
        Actually delete, rather than reporting what would go.
    """
    console = current().console
    result = current().service().index_gc(apply=apply)
    head = render.fields(
        [(("removed" if apply else "would remove"), f"{len(result['removed'])} file(s)")],
    )
    listing = render.lines(result["removed"], style="muted") if result["removed"] else None
    advice = None
    if not apply and result["removed"]:
        advice = Text("pass --apply to delete them", style="warn")
    return console.emit("index.gc", result, view=render.stack(head, listing, advice))
