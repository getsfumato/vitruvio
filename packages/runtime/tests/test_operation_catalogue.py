"""The declared facts, checked against the code that would have to be read otherwise.

A catalogue that nobody verifies is documentation, and documentation drifts -- the module table in
``ARCHITECTURE.md`` had drifted by three modules before it was generated from here. So every fact declared in
:mod:`vitruvio.runtime.operation_catalogue` is confirmed against the implementation, and the confirmations are
one-directional on purpose: an operation may declare *more* than it needs, because ``compound_search`` opens
RETRIEVE on somebody else's session and ``pull_source`` decides between INSPECT and WRITE at runtime, but it may
never declare less than the body asks for. Under-declaring is the direction that would let an interface offer a
write to a caller allowed only to read.

The walk follows private helpers and the async twin, because that is where the capability usually lives:
``doctor`` names none itself and reaches its brain through ``_brain``, and every synchronous distribution method
is a bridge whose body is one call to the coroutine.
"""

from __future__ import annotations

import ast
import importlib.util
from inspect import getmembers, isclass, isfunction
from pathlib import Path
from typing import Any

import pytest

from vitruvio.runtime.capability import Capability
from vitruvio.runtime.operation_catalogue import (
    OPERATION_CATALOGUE,
    Cost,
    Operation,
    OperationDomain,
    ResultKind,
    facade_operations,
    operations,
    operations_table,
    protocol_operations,
)

CASES = [(domain, operation) for domain, operation in operations()]
IDS = [f"{domain.class_name}.{operation.name}" for domain, operation in CASES]
OPS_DIR = Path(str(importlib.util.find_spec("vitruvio.runtime.ops.lifecycle").origin)).parent  # type: ignore[union-attr]


def _class(domain: OperationDomain) -> ast.ClassDef:
    found = importlib.util.find_spec(domain.module)
    if found is None or found.origin is None:  # pragma: no cover - a catalogued module always resolves
        raise RuntimeError(f"could not resolve {domain.module}")
    tree = ast.parse(Path(found.origin).read_text(encoding="utf-8"))
    return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == domain.class_name)


def _method(owner: ast.ClassDef, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    return next(
        node for node in owner.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


def _reached(owner: ast.ClassDef, name: str, seen: set[str] | None = None) -> tuple[set[str], bool]:
    """Which capabilities an operation names, and whether it enters the session's write, helpers included."""
    seen = {name} if seen is None else seen | {name}
    method = _method(owner, name)
    capabilities: set[str] = set()
    writes = False
    for node in ast.walk(method):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "Capability":
            capabilities.add(node.attr)
        if isinstance(node, ast.Attribute) and ast.unparse(node).endswith(("session.write", "session.pinned")):
            writes = writes or ast.unparse(node).endswith("session.write")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            if node.attr in seen or node.attr == "config":
                continue
            try:
                nested, nested_writes = _reached(owner, node.attr, seen)
            except StopIteration:
                continue
            capabilities |= nested
            writes = writes or nested_writes
    return capabilities, writes


def _origins(domain: OperationDomain) -> dict[str, str]:
    found = importlib.util.find_spec(domain.module)
    if found is None or found.origin is None:  # pragma: no cover - a catalogued module always resolves
        raise RuntimeError(f"could not resolve {domain.module}")
    tree = ast.parse(Path(found.origin).read_text(encoding="utf-8"))
    return {
        alias.asname or alias.name: node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
        for alias in node.names
    }


def _required_paths(method: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    arguments = method.args.args + method.args.kwonlyargs
    return [
        argument.arg
        for argument in arguments
        if argument.annotation is not None and ast.unparse(argument.annotation) == "Path"
    ]


@pytest.mark.parametrize(("domain", "operation"), CASES, ids=IDS)
class TestEveryDeclaredFactHolds:
    def test_the_capability_covers_what_the_body_asks_for(self, domain: OperationDomain, operation: Operation) -> None:
        owner = _class(domain)
        capabilities, writes = _reached(owner, operation.async_name or operation.name)
        needed = {Capability[name] for name in capabilities} | ({Capability.WRITE} if writes else set())
        if not needed:
            return
        assert operation.capability is not None, f"{operation.name} opens a brain and declares no capability"
        assert max(needed) <= operation.capability

    def test_holding_the_write_brain_is_declared_as_mutating(
        self, domain: OperationDomain, operation: Operation
    ) -> None:
        """Even when the body only reads through it: the write brain engages the retention policy and the
        validation gate and takes the session's writer slot, which is what a caller is being allowed to do."""
        _, writes = _reached(_class(domain), operation.async_name or operation.name)
        if writes:
            assert operation.mutates

    def test_naming_a_path_on_this_host_is_declared_local(self, domain: OperationDomain, operation: Operation) -> None:
        owner = _class(domain)
        required = _required_paths(_method(owner, operation.name))
        if required:
            assert operation.remote.value == "local", f"{operation.name} requires {required} and is offered remotely"

    def test_the_result_kind_matches_the_return_annotation(self, domain: OperationDomain, operation: Operation) -> None:
        returns = _method(_class(domain), operation.name).returns
        annotation = ast.unparse(returns) if returns is not None else "None"
        origins = _origins(domain)
        named = [node.id for node in ast.walk(returns) if isinstance(node, ast.Name)] if returns is not None else []
        if annotation == "bytes":
            expected = ResultKind.BINARY
        elif annotation == "dict[str, Any]":
            expected = ResultKind.JSON
        elif any(origins.get(name, "").endswith("_result") for name in named):
            expected = ResultKind.TYPED
        else:
            expected = ResultKind.SCALAR
        assert operation.result is expected

    def test_an_async_twin_exists_and_is_a_coroutine(self, domain: OperationDomain, operation: Operation) -> None:
        if operation.async_name is None:
            return
        assert operation.network, "an operation with a coroutine twin is one that waits on something"
        assert isinstance(_method(_class(domain), operation.async_name), ast.AsyncFunctionDef)


class TestTheCatalogueIsComplete:
    def test_no_operations_class_escapes_the_catalogue(self) -> None:
        """`test_facade` compares public methods against the catalogue *per catalogued domain*, so a class that
        is absent altogether is invisible to it. Adding one with a public method must not be silent."""
        catalogued = {domain.qualified_name for domain in OPERATION_CATALOGUE}
        for module in sorted(OPS_DIR.glob("*.py")):
            if module.stem == "__init__":
                continue
            name = f"vitruvio.runtime.ops.{module.stem}"
            imported = importlib.import_module(name)
            for class_name, owner in getmembers(imported, isclass):
                # `*Ops` is ADR-0013's naming for an operations class, and the only thing separating one from the
                # pydantic models that share these modules.
                if owner.__module__ != name or not class_name.endswith("Ops"):
                    continue
                if f"{name}.{class_name}" in catalogued:
                    continue
                public = [
                    method
                    for method, _ in getmembers(owner, isfunction)
                    if not method.startswith("_") and method != "config"
                ]
                assert public == [], f"{name}.{class_name} exposes {public} and is not catalogued"

    def test_a_sync_async_pair_is_one_protocol_operation(self) -> None:
        """Twelve distribution methods, six operations. An interface looping over the catalogue offers six."""
        distribution = [
            operation.name
            for domain, operation in protocol_operations()
            if domain.class_name in {"PublishOps", "InstallOps"}
        ]
        assert distribution == ["pack", "registry_check", "push", "tags", "plan_pull", "pull", "fetch"]
        assert len([name for _, name in facade_operations() if name.endswith("_async")]) == 5

    def test_nothing_host_local_is_offered_to_a_caller_elsewhere(self) -> None:
        offered = {operation.name for _, operation in protocol_operations()}
        assert not offered & {"register", "replace", "put_content", "ingest_run", "export_content", "migrate", "bench"}

    def test_cost_follows_the_facts_rather_than_being_declared_beside_them(self) -> None:
        costs = {operation.name: operation.cost for _, operation in operations()}
        assert costs["auth_keys"] is Cost.CHEAP
        assert costs["state"] is Cost.OPENS_BRAIN
        assert costs["push"] is Cost.NETWORK
        assert costs["bench"] is Cost.HEAVY

    def test_the_architecture_table_is_generated_from_the_catalogue(self) -> None:
        """The one that had drifted: it named `keys` for `auth_keys`, missed `fetch`, and had no reconcile row."""
        architecture = Path(__file__).parents[3] / "ARCHITECTURE.md"
        assert operations_table() in architecture.read_text(encoding="utf-8")

    def test_the_catalogue_costs_no_heavy_import(self) -> None:
        """It is read by the generator and by documentation, neither of which may pay for opening a brain."""
        import vitruvio.runtime.operation_catalogue

        assert "boltzmann" not in {name.split(".")[0] for name in _imports_of(vitruvio.runtime.operation_catalogue)}


def _imports_of(module: Any) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names
