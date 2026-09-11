"""Rich renderables for the query workspace in ``vitruvio browse``.

These functions draw only service data. They never open an index, recompute a plan or imply a storage shape the index
does not have: the B-tree view explicitly renders the sorted-array engine used by Vitruvio.
"""

from __future__ import annotations

from rich.text import Text

from vitruvio.cli.render import theme
from vitruvio.runtime.retrieval_result import (
    BTreeDiagnosticsResult,
    GraphDiagnosticsResult,
    SearchPlanResult,
    VectorDiagnosticsResult,
    VectorScopeResult,
)


def plan_view(plan: SearchPlanResult | None) -> Text:
    """Render the chosen operators and consulted indices."""
    if plan is None:
        return Text("No cost-based plan was reported for this query.", style="muted")
    view = Text()
    consulted = plan["indices_consulted"]
    view.append("selected indices\n", style="heading")
    if consulted:
        for scope, kinds in consulted.items():
            view.append(f"{scope:<11}", style=theme.MEMORY_STYLES.get(scope, "value"))
            view.append("  " + " · ".join(kinds) + "\n", style="value")
    else:
        view.append("none — the planner chose an exhaustive scan\n", style="warn")

    view.append(f"\n{plan['intent']} intent", style="value")
    view.append(f"  ·  {plan['est_cost_us']:,.1f} µs estimated  ·  {plan['est_recall']:.0%} recall\n", style="muted")
    view.append(f"plan {plan['signature']}\n\n", style="digest")

    view.append("physical operators\n", style="heading")
    for operator in plan["operators"]:
        view.append(f"#{operator['node_id']} ", style="muted")
        view.append(operator["op"], style="count" if operator["index"] else "value")
        if operator["scope"]:
            view.append(f"  {operator['scope']}", style=theme.MEMORY_STYLES.get(operator["scope"], "value"))
        if operator["index"]:
            view.append(f"  [{operator['index']}]", style="score")
        if operator["inputs"]:
            view.append("  ← " + ", ".join(f"#{node}" for node in operator["inputs"]), style="muted")
        view.append("\n")

    if plan["degradations"]:
        view.append("\ndegraded\n", style="warn")
        for item in plan["degradations"]:
            view.append(f"{item['kind']}: {item['detail']}\n", style="warn")
    return view


def graph_view(data: GraphDiagnosticsResult | None) -> Text:
    """Draw the query-scoped graph as numbered nodes and directed, typed edges."""
    if data is None or not data["selected"]:
        return _not_selected("graph", "No GraphExpand operator ran for this query.")
    nodes = data["nodes"]
    edges = data["edges"]
    numbers = {node["id"]: position for position, node in enumerate(nodes, start=1)}
    view = Text()
    view.append(f"{len(nodes)} nodes  ·  {len(edges)} edges", style="count")
    if data["scopes"]:
        view.append("  ·  " + ", ".join(data["scopes"]), style="muted")
    view.append("\n\n")
    for node in nodes:
        number = numbers[node["id"]]
        view.append(f"● {number:02d} ", style="score" if node["role"] == "result" else "muted")
        view.append(node["label"] or theme.short(node["id"]), style="value")
        if node["memory_type"]:
            view.append(f"  {node['memory_type']}", style=theme.MEMORY_STYLES.get(node["memory_type"], "value"))
        view.append("\n")
    if not edges:
        view.append("\nThe graph index ran, but no stored edge touches the returned blocks.", style="muted")
        return view
    view.append("\n")
    for edge in edges:
        source = numbers.get(edge["source"], 0)
        target = numbers.get(edge["target"], 0)
        relation = edge["predicate"] or edge["kind"] or "related"
        view.append(f"{source:02d} ──{relation}──▶ {target:02d}\n", style="muted")
    return view


def vector_view(data: VectorDiagnosticsResult | None, *, width: int = 49, height: int = 15) -> Text:
    """Draw PCA coordinates on a fixed terminal scatter plot."""
    if data is None or not data["selected"]:
        return _not_selected("vector", "No VectorSearch or BruteVector operator ran for this query.")
    if not data["scopes"]:
        return Text("The vector index ran, but no projection was available.", style="warn")
    view = Text()
    for position, scope in enumerate(data["scopes"]):
        if position:
            view.append("\n")
        view.append(_vector_scope(scope, width=width, height=height))
    return view


def _vector_scope(scope: VectorScopeResult, *, width: int, height: int) -> Text:
    """One consulted vector space, kept separate so coordinates from unrelated embedders are never mixed."""
    if error := scope.get("error"):
        return Text(f"{scope['scope']}: projection failed: {error}\n", style="warn")
    points = scope["points"]
    if not points:
        return Text(f"{scope['scope']}: no returned block had a plottable vector.\n", style="muted")

    canvas = [[" " for _ in range(width)] for _ in range(height)]
    axis_x = height // 2
    axis_y = width // 2
    for column in range(width):
        canvas[axis_x][column] = "─"
    for row in range(height):
        canvas[row][axis_y] = "│"
    canvas[axis_x][axis_y] = "┼"
    legend: list[tuple[str, str]] = []
    for position, point in enumerate(points):
        column = round((point["x"] + 1.0) * (width - 1) / 2)
        row = round((1.0 - point["y"]) * (height - 1) / 2)
        marker = "Q" if point["role"] == "query" else str(position % 10)
        canvas[max(0, min(height - 1, row))][max(0, min(width - 1, column))] = marker
        legend.append((marker, point["label"] or "query"))

    view = Text()
    view.append(f"{scope['scope']}  ·  {scope['dimensions']}D → 2D PCA\n", style="heading")
    view.append("Coordinates show relative geometry, not match scores.\n\n", style="muted")
    view.append("\n".join("".join(row) for row in canvas), style="digest")
    view.append("\n\n")
    for marker, label in legend:
        view.append(f"{marker} ", style="score" if marker == "Q" else "count")
        view.append(label[:68] + "\n", style="value")
    return view


def btree_view(data: BTreeDiagnosticsResult | None) -> Text:
    """Draw the real sorted-array range window and bisect boundaries."""
    if data is None or not data["selected"]:
        return _not_selected("B-tree", "No RangeScan operator ran for this query.")
    if not data["scopes"]:
        return Text("RangeScan ran, but its ordered window was unavailable.", style="warn")
    view = Text()
    for scope in data["scopes"]:
        view.append(f"{scope['scope']} · {scope['key']}", style="heading")
        view.append(f"  ·  {scope['total']} values\n", style="muted")
        start, end = scope["start"], scope["end"]
        entries = scope["entries"]
        selected = max(0, end - start)
        view.append(f"sorted-array engine · bisect window [{start}, {end}) · {selected} selected\n", style="muted")
        visible = {entry["position"] for entry in entries}
        if start == end:
            view.append(f"empty range · insertion point {start}\n\n", style="muted")
        else:
            hidden = []
            if start not in visible:
                hidden.append("start")
            if end - 1 not in visible:
                hidden.append("end")
            if hidden:
                view.append(f"{'+'.join(hidden)} boundary outside the bounded slice\n", style="muted")
            view.append("visible brackets mark boundaries present in the slice\n\n", style="muted")
        for entry in entries:
            position = entry["position"]
            left = "[" if start < end and position == start else " "
            right = "]" if start < end and position + 1 == end else " "
            style = "score" if entry["selected"] else "muted"
            view.append(f"{left}{position:>5}  {entry['value'][:42]:<42}{right}", style=style)
            view.append(f"  {theme.short(entry['block_id'])}\n", style="digest")
        view.append("\n")
    return view


def _not_selected(name: str, detail: str) -> Text:
    view = Text()
    view.append(f"{name} not selected\n", style="heading")
    view.append(detail, style="muted")
    return view


__all__ = ["btree_view", "graph_view", "plan_view", "vector_view"]
