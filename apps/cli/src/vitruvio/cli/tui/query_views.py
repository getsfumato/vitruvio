"""Rich renderables for the query workspace in ``vitruvio browse``.

These functions draw only service data. They never open an index, recompute a plan or imply a storage shape the index
does not have: the B-tree view explicitly renders the sorted-array engine used by Vitruvio.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text

from vitruvio.cli.render import theme


def plan_view(plan: dict[str, Any] | None) -> Text:
    """Render the chosen operators and consulted indices."""
    if not plan:
        return Text("No cost-based plan was reported for this query.", style="muted")
    view = Text()
    consulted = plan.get("indices_consulted") or {}
    view.append("selected indices\n", style="heading")
    if consulted:
        for scope, kinds in consulted.items():
            view.append(f"{scope:<11}", style=theme.MEMORY_STYLES.get(str(scope), "value"))
            view.append("  " + " · ".join(str(kind) for kind in kinds) + "\n", style="value")
    else:
        view.append("none — the planner chose an exhaustive scan\n", style="warn")

    cost = float(plan.get("est_cost_us") or 0.0)
    recall = float(plan.get("est_recall") or 0.0)
    view.append(f"\n{plan.get('intent', 'unknown')} intent", style="value")
    view.append(f"  ·  {cost:,.1f} µs estimated  ·  {recall:.0%} recall\n", style="muted")
    view.append(f"plan {plan.get('signature', '-')}\n\n", style="digest")

    view.append("physical operators\n", style="heading")
    for operator in plan.get("operators") or []:
        view.append(f"#{operator.get('node_id')} ", style="muted")
        view.append(str(operator.get("op", "?")), style="count" if operator.get("index") else "value")
        if operator.get("scope"):
            view.append(f"  {operator['scope']}", style=theme.MEMORY_STYLES.get(str(operator["scope"]), "value"))
        if operator.get("index"):
            view.append(f"  [{operator['index']}]", style="score")
        if operator.get("inputs"):
            view.append("  ← " + ", ".join(f"#{node}" for node in operator["inputs"]), style="muted")
        view.append("\n")

    degradations = plan.get("degradations") or []
    if degradations:
        view.append("\ndegraded\n", style="warn")
        for item in degradations:
            view.append(f"{item.get('kind', 'degradation')}: {item.get('detail', '')}\n", style="warn")
    return view


def graph_view(data: dict[str, Any] | None) -> Text:
    """Draw the query-scoped graph as numbered nodes and directed, typed edges."""
    data = data or {}
    if not data.get("selected"):
        return _not_selected("graph", "No GraphExpand operator ran for this query.")
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []
    numbers = {str(node.get("id")): position for position, node in enumerate(nodes, start=1)}
    view = Text()
    view.append(f"{len(nodes)} nodes  ·  {len(edges)} edges", style="count")
    scopes = data.get("scopes") or []
    if scopes:
        view.append("  ·  " + ", ".join(scopes), style="muted")
    view.append("\n\n")
    for node in nodes:
        number = numbers[str(node.get("id"))]
        view.append(f"● {number:02d} ", style="score" if node.get("role") == "result" else "muted")
        view.append(str(node.get("label") or theme.short(str(node.get("id")))), style="value")
        if node.get("memory_type"):
            view.append(f"  {node['memory_type']}", style=theme.MEMORY_STYLES.get(str(node["memory_type"]), "value"))
        view.append("\n")
    if not edges:
        view.append("\nThe graph index ran, but no stored edge touches the returned blocks.", style="muted")
        return view
    view.append("\n")
    for edge in edges:
        source = numbers.get(str(edge.get("source")), 0)
        target = numbers.get(str(edge.get("target")), 0)
        relation = str(edge.get("predicate") or edge.get("kind") or "related")
        view.append(f"{source:02d} ──{relation}──▶ {target:02d}\n", style="muted")
    return view


def vector_view(data: dict[str, Any] | None, *, width: int = 49, height: int = 15) -> Text:
    """Draw PCA coordinates on a fixed terminal scatter plot."""
    data = data or {}
    if not data.get("selected"):
        return _not_selected("vector", "No VectorSearch or BruteVector operator ran for this query.")
    scopes = data.get("scopes") or []
    if not scopes:
        return Text("The vector index ran, but no projection was available.", style="warn")
    view = Text()
    for position, scope in enumerate(scopes):
        if position:
            view.append("\n")
        view.append(_vector_scope(scope, width=width, height=height))
    return view


def _vector_scope(scope: dict[str, Any], *, width: int, height: int) -> Text:
    """One consulted vector space, kept separate so coordinates from unrelated embedders are never mixed."""
    if scope.get("error"):
        return Text(f"{scope.get('scope', 'vector')}: projection failed: {scope['error']}\n", style="warn")
    points = scope.get("points") or []
    if not points:
        return Text(
            f"{scope.get('scope', 'vector')}: no returned block had a plottable vector.\n",
            style="muted",
        )

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
        column = round((float(point.get("x", 0.0)) + 1.0) * (width - 1) / 2)
        row = round((1.0 - float(point.get("y", 0.0))) * (height - 1) / 2)
        marker = "Q" if point.get("role") == "query" else str(position % 10)
        canvas[max(0, min(height - 1, row))][max(0, min(width - 1, column))] = marker
        legend.append((marker, str(point.get("label") or "query")))

    view = Text()
    view.append(f"{scope.get('scope', 'vector')}  ·  {scope.get('dimensions', 0)}D → 2D PCA\n", style="heading")
    view.append("Coordinates show relative geometry, not match scores.\n\n", style="muted")
    view.append("\n".join("".join(row) for row in canvas), style="digest")
    view.append("\n\n")
    for marker, label in legend:
        view.append(f"{marker} ", style="score" if marker == "Q" else "count")
        view.append(label[:68] + "\n", style="value")
    return view


def btree_view(data: dict[str, Any] | None) -> Text:
    """Draw the real sorted-array range window and bisect boundaries."""
    data = data or {}
    if not data.get("selected"):
        return _not_selected("B-tree", "No RangeScan operator ran for this query.")
    scopes = data.get("scopes") or []
    if not scopes:
        return Text("RangeScan ran, but its ordered window was unavailable.", style="warn")
    view = Text()
    for scope in scopes:
        view.append(f"{scope.get('scope')} · {scope.get('key')}", style="heading")
        view.append(f"  ·  {scope.get('total', 0)} values\n", style="muted")
        start, end = int(scope.get("start", 0)), int(scope.get("end", 0))
        entries = scope.get("entries") or []
        selected = max(0, end - start)
        view.append(f"sorted-array engine · bisect window [{start}, {end}) · {selected} selected\n", style="muted")
        visible = {int(entry.get("position", 0)) for entry in entries}
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
            position = int(entry.get("position", 0))
            left = "[" if start < end and position == start else " "
            right = "]" if start < end and position + 1 == end else " "
            style = "score" if entry.get("selected") else "muted"
            view.append(f"{left}{position:>5}  {str(entry.get('value', ''))[:42]:<42}{right}", style=style)
            view.append(f"  {theme.short(entry.get('block_id'))}\n", style="digest")
        view.append("\n")
    return view


def _not_selected(name: str, detail: str) -> Text:
    view = Text()
    view.append(f"{name} not selected\n", style="heading")
    view.append(detail, style="muted")
    return view


__all__ = ["btree_view", "graph_view", "plan_view", "vector_view"]
