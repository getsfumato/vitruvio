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

from vitruvio.cli.context import current
from vitruvio.kernel import ExitCode

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

    lines = [
        f"{'module':<12} {'kind':<10} {'blocks':>7} {'size':>9} {'state':<16} engine",
        f"{'-' * 12} {'-' * 10} {'-' * 7} {'-' * 9} {'-' * 16} {'-' * 14}",
    ]
    for row in result["indices"]:
        size = f"{row['size_bytes'] / 1024:.1f}K" if row["size_bytes"] else "-"
        lines.append(
            f"{row['memory_type']:<12} {row['kind']:<10} {row['population']:>7} {size:>9} "
            f"{row['state']:<16} {row['engine']}"
        )
    if not result["indices"]:
        lines = ["No indices registered."]

    if unavailable := result["unavailable"]:
        # One line rather than one per entry. The per-index detail stays in the JSON envelope, where whatever is
        # driving the CLI reads it; eleven warnings above a fifteen-row table just hides the table.
        kinds = sorted({name.split(".", 1)[1] for name in unavailable})
        console.warn(
            f"{len(unavailable)} declared index(es) are not available in this build: {', '.join(kinds)} "
            f"(pass --json for the full list)"
        )
    return console.emit("index.list", result, lines=lines)


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

    lines = [
        f"{'module':<12} {'kind':<10} {'blocks':>7}  {'root':<18}",
        f"{'-' * 12} {'-' * 10} {'-' * 7}  {'-' * 18}",
    ]
    from vitruvio.cli.render import short

    for row in result["indices"]:
        lines.append(
            f"{row['memory_type']:<12} {row['kind']:<10} {row['population']:>7}  {short(row['bound_root']):<18}"
        )
    lines += ["", f"{result['written']} index files written to {result['home']}"]

    travelling = result.get("travelling") or []
    lines.append(f"travelling  {', '.join(travelling) if travelling else '(none)'}")
    for module, outcome in sorted((result.get("vouched") or {}).items()):
        if outcome != "vouched":
            # A warning rather than a line: an unvouched vector index means a publish that silently omits the one index
            # a consumer cannot rebuild for itself.
            console.warn(f"{module}: the vector index will not be published -- {outcome}")
    return console.emit("index.build", result, lines=lines)


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

    lines: list[str] = []
    for entry in result["statistics"]:
        lines += [
            f"{entry['memory_type']}",
            f"  freshness    {entry['freshness']}" + (f"  ({entry['reason']})" if entry.get("reason") else ""),
            f"  blocks       {entry['cardinality']} ({entry['resolvable']} resolvable)",
            f"  indices      {', '.join(entry['indices']) or '(none)'}",
            f"  built at     {entry['built_at'] or '-'}",
        ]
        if entry["columns"]:
            distinct = ", ".join(f"{name}={count}" for name, count in entry["columns"].items() if count)
            lines.append(f"  facets       {distinct or '(none populated)'}")
        if entry["vocabulary"]:
            lines.append(f"  vocabulary   {entry['vocabulary']} terms, {entry['postings']} postings")
        if entry["graph_edges"]:
            lines.append(f"  graph        {entry['graph_edges']} edges")
        if entry["vectors"]:
            lines.append(f"  vectors      {entry['vectors']}")
        lines.append("")
    if not lines:
        lines = ["No statistics yet. Run `vitruvio index build`."]
    return console.emit("index.stats", result, lines=lines)


@app.command(name="verify")
def verify() -> ExitCode:
    """Check every index against the composition it claims to describe.

    Exits 5 when an index is stale, so this is usable as a gate before a publish. Staleness is not corruption:
    the fix is `index build`, and the message says so.
    """
    console = current().console
    result = current().service().index_verify()

    lines = [
        f"{'module':<12} {'kind':<10} {'state':<16} detail",
        f"{'-' * 12} {'-' * 10} {'-' * 16} {'-' * 30}",
    ]
    for row in result["capabilities"]:
        lines.append(f"{row['memory_type']:<12} {row['kind']:<10} {row['state']:<16} {row['detail'] or ''}")
    if not result["capabilities"]:
        lines = ["No indices registered."]

    if result["stale"]:
        from vitruvio.kernel import VitruvioError

        raise VitruvioError(
            f"{result['stale']} index(es) describe a different composition than the one installed",
            hint="run `vitruvio index build` -- an index is derived, so rebuilding it is always safe",
        )
    return console.emit("index.verify", result, lines=lines)


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
    lines = [f"{'removed' if apply else 'would remove'}: {len(result['removed'])} file(s)"]
    lines += [f"  {path}" for path in result["removed"]]
    if not apply and result["removed"]:
        lines.append("")
        lines.append("pass --apply to delete them")
    return console.emit("index.gc", result, lines=lines)
