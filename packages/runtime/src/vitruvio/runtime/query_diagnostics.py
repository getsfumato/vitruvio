"""Serializable, query-scoped diagnostics for human retrieval interfaces.

The planner owns the decision about which indices ran. This module only describes those chosen indices after the
Evidence Bundle exists, using the same opened brain and the same explanation. Keeping this in runtime prevents a TUI
from reaching through the service seam into index internals, and keeping it opt-in prevents ordinary API searches from
paying for a vector projection they did not ask to see.
"""

from __future__ import annotations

from typing import Any


def query_diagnostics(
    brain: Any,
    text: str,
    matches: list[dict[str, Any]],
    explanation: Any,
) -> dict[str, Any]:
    """Describe the selected plan's graph, vector and ordered-index views."""
    from boltzmann.blocks.memory_type import MemoryType
    from boltzmann.indices.base import IndexKind

    from vitruvio.indices import BTreeIndex, GraphIndex, OrderedKey, VectorIndex

    modules = brain.modules()
    consulted = explanation.indices_consulted
    planner = getattr(brain, "planner", None)
    capabilities = planner.capabilities(modules) if planner is not None and hasattr(planner, "capabilities") else None
    by_id = {str(match.get("block_id")): match for match in matches if match.get("block_id")}
    result_ids = list(by_id)

    graph_selected = any(IndexKind.GRAPH.value in kinds for kinds in consulted.values())
    # GraphExpand is federated by the executor: one operator may traverse graph indices outside its output scope to
    # reach provenance edges. Report every graph the operator really opens rather than only the scope printed on it.
    graph_scopes = (
        {
            memory_type.value
            for memory_type, module in modules.items()
            if (capabilities is None or capabilities.has(memory_type.value, IndexKind.GRAPH))
            and isinstance(module.indices.get(IndexKind.GRAPH.value), GraphIndex)
        }
        if graph_selected
        else set()
    )
    graph_edges: list[dict[str, Any]] = []
    graph_nodes: dict[str, dict[str, Any]] = {
        identity: _node(identity, by_id.get(identity), role="result") for identity in result_ids
    }
    for scope in sorted(graph_scopes):
        module = modules.get(MemoryType(scope))
        index = module.indices.get(IndexKind.GRAPH.value) if module is not None else None
        if not isinstance(index, GraphIndex):
            continue
        for source, target, kind, predicate, weight in index.edges(result_ids, limit=40):
            graph_nodes.setdefault(source, _node(source, by_id.get(source), role="related"))
            graph_nodes.setdefault(target, _node(target, by_id.get(target), role="related"))
            graph_edges.append(
                {
                    "source": source,
                    "target": target,
                    "kind": kind,
                    "predicate": predicate,
                    "weight": weight,
                    "scope": scope,
                }
            )

    vector_scopes: list[dict[str, Any]] = []
    for scope, kinds in sorted(consulted.items()):
        if IndexKind.VECTOR.value not in kinds:
            continue
        module = modules.get(MemoryType(scope))
        index = module.indices.get(IndexKind.VECTOR.value) if module is not None else None
        if not isinstance(index, VectorIndex):
            continue
        scoped_ids = [identity for identity in result_ids if str(by_id[identity].get("memory_type")) == scope]
        try:
            projection = index.project_2d(text, scoped_ids)
        except Exception as error:
            vector_scopes.append({"scope": scope, "error": str(error), "dimensions": 0, "points": []})
            continue
        for point in projection["points"]:
            identity = point.get("block_id")
            point["label"] = "query" if identity is None else graph_nodes.get(identity, _node(identity))["label"]
        vector_scopes.append({"scope": scope, **projection})

    ordered_scopes: list[dict[str, Any]] = []
    range_ops = [operator for operator in explanation.chosen.operators if operator.index == IndexKind.BTREE.value]
    for operator in range_ops:
        if not operator.scope:
            continue
        module = modules.get(MemoryType(operator.scope))
        index = module.indices.get(IndexKind.BTREE.value) if module is not None else None
        if not isinstance(index, BTreeIndex):
            continue
        key_name = str(operator.params.get("key") or "occurred_at")
        try:
            key = OrderedKey(key_name)
        except ValueError:
            continue
        ordered_scopes.append(
            {
                "scope": operator.scope,
                **index.window(
                    key,
                    low=operator.params.get("low"),
                    high=operator.params.get("high"),
                    prefix=operator.params.get("prefix"),
                ),
            }
        )

    return {
        "graph": {
            "selected": bool(graph_scopes),
            "scopes": sorted(graph_scopes),
            "nodes": list(graph_nodes.values()) if graph_scopes else [],
            "edges": graph_edges,
        },
        "vector": {"selected": bool(vector_scopes), "scopes": vector_scopes},
        "btree": {"selected": bool(range_ops), "scopes": ordered_scopes},
    }


def _node(identity: str, match: dict[str, Any] | None = None, *, role: str = "related") -> dict[str, Any]:
    """A compact, stable graph label without resolving another block."""
    payload = (match or {}).get("content") or {}
    label = (
        payload.get("label")
        or payload.get("summary")
        or payload.get("statement")
        or payload.get("media_type")
        or _short(identity)
    )
    return {
        "id": identity,
        "label": str(label).replace("\n", " ")[:64],
        "memory_type": (match or {}).get("memory_type"),
        "role": "result" if match is not None else role,
        "score": (match or {}).get("score"),
    }


def _short(identity: str) -> str:
    """Shorten a content identity for a diagram label."""
    algorithm, _, hexadecimal = identity.partition(":")
    return f"{algorithm}:{hexadecimal[:10]}" if hexadecimal else identity[:14]


__all__ = ["query_diagnostics"]
