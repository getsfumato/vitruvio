"""Generate the artifacts the operation catalogue is authoritative for.

Two of them: the statically visible ``BrainService`` facade, and the module summary in ``ARCHITECTURE.md``. The
second is here rather than maintained by hand because it states the same facts -- which operations a module owns,
what they may open, whether they write -- and a second copy of a fact is a copy that goes stale. It had.
"""

from __future__ import annotations

import argparse
import ast
import copy
import importlib.util
import subprocess
import sys
from collections import defaultdict
from functools import cache
from pathlib import Path

from vitruvio.runtime.operation_catalogue import OPERATION_CATALOGUE, Exposure, OperationDomain, operations_table

TARGET = Path(__file__).with_name("_generated_facade.py")
ARCHITECTURE = Path(__file__).parents[5] / "ARCHITECTURE.md"
BEGIN = "<!-- operations:begin -->"
END = "<!-- operations:end -->"


def _source_path(domain: OperationDomain) -> Path:
    """Resolve an operations module without importing ``vitruvio.runtime`` and its facade."""
    found = importlib.util.find_spec(domain.module)
    if found is None or found.origin is None:
        raise RuntimeError(f"could not resolve {domain.module}")
    return Path(found.origin)


@cache
def _origins(module: str) -> dict[str, str]:
    """Where each name an operations module imports comes from.

    A forwarder copies the implementation's annotations verbatim, so whatever they name has to be importable in
    the generated module too. Reading it from the source rather than listing it here is what lets a domain
    introduce a result type without editing the generator.
    """
    found = importlib.util.find_spec(module)
    if found is None or found.origin is None:
        raise RuntimeError(f"could not resolve {module}")
    tree = ast.parse(Path(found.origin).read_text(encoding="utf-8"))
    return {
        alias.asname or alias.name: node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None and node.module != "__future__"
        for alias in node.names
    }


def _annotated(method: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Every name the signature's annotations mention."""
    annotations = [argument.annotation for argument in method.args.args + method.args.kwonlyargs]
    annotations.append(method.returns)
    return {
        node.id
        for annotation in annotations
        if annotation is not None
        for node in ast.walk(annotation)
        if isinstance(node, ast.Name)
    }


def _method(domain: OperationDomain, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(_source_path(domain).read_text(encoding="utf-8"))
    owner = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == domain.class_name),
        None,
    )
    if owner is None:
        raise RuntimeError(f"{domain.qualified_name} does not exist")
    method = next(
        (
            node
            for node in owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        ),
        None,
    )
    if method is None:
        raise RuntimeError(f"{domain.qualified_name}.{name} does not exist")
    return method


def _summary(method: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    doc = ast.get_docstring(method, clean=True) or method.name.replace("_", " ").capitalize()
    return doc.split("\n\n", 1)[0].replace("\n", " ")


def _forwarder(domain: OperationDomain, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    source = _method(domain, name)
    args = copy.deepcopy(source.args)
    positional: list[ast.expr] = [
        ast.Name(id=argument.arg, ctx=ast.Load())
        for argument in (*args.posonlyargs, *args.args)
        if argument.arg != "self"
    ]
    if args.vararg is not None:
        positional.append(ast.Starred(value=ast.Name(id=args.vararg.arg, ctx=ast.Load()), ctx=ast.Load()))
    keywords = [
        ast.keyword(arg=argument.arg, value=ast.Name(id=argument.arg, ctx=ast.Load())) for argument in args.kwonlyargs
    ]
    if args.kwarg is not None:
        keywords.append(ast.keyword(arg=None, value=ast.Name(id=args.kwarg.arg, ctx=ast.Load())))
    call = ast.Call(
        func=ast.Attribute(
            value=ast.Attribute(value=ast.Name(id="self", ctx=ast.Load()), attr=domain.property_name, ctx=ast.Load()),
            attr=name,
            ctx=ast.Load(),
        ),
        args=positional,
        keywords=keywords,
    )
    result: ast.expr = ast.Await(value=call) if isinstance(source, ast.AsyncFunctionDef) else call
    body: list[ast.stmt] = [
        ast.Expr(
            value=ast.Constant(value=(f"{_summary(source)}\n\nSee :meth:`{domain.module}.{domain.class_name}.{name}`."))
        ),
        ast.Return(value=result),
    ]
    if isinstance(source, ast.AsyncFunctionDef):
        node = ast.parse("async def generated():\n    pass").body[0]
        assert isinstance(node, ast.AsyncFunctionDef)
    else:
        node = ast.parse("def generated():\n    pass").body[0]
        assert isinstance(node, ast.FunctionDef)
    # Let the running interpreter create the complete AST node first. Python 3.12 added ``type_params`` to function
    # nodes, so constructing them field by field makes the generator depend on one interpreter's AST schema.
    node.name = name
    node.args = args
    node.body = body
    node.decorator_list = []
    node.returns = copy.deepcopy(source.returns)
    node.type_comment = None
    return node


def _import_block(needed: dict[str, set[str]]) -> str:
    """The generated module's imports, in the two groups ruff's isort rules expect."""
    groups: list[list[str]] = [[], []]
    for module in sorted(needed):
        line = f"from {module} import {', '.join(sorted(needed[module]))}"
        groups[module.startswith("vitruvio")].append(line)
    return "\n\n".join("\n".join(group) for group in groups if group)


def render() -> str:
    """Render the complete generated module deterministically."""
    needed: dict[str, set[str]] = defaultdict(set)
    needed["functools"].add("cached_property")
    class_lines = [
        "class GeneratedFacade:",
        '    """Generated operation properties and direct facade forwarding."""',
        "",
        "    session: BrainSession",
    ]
    for domain in OPERATION_CATALOGUE:
        class_lines.extend(
            [
                "",
                "    @cached_property",
                f"    def {domain.property_name}(self) -> {domain.class_name}:",
                f'        """The {domain.class_name} operations."""',
                f"        return {domain.class_name}(self.session)",
            ]
        )
        for exported in domain.exports:
            class_lines.extend(["", f"    {exported} = {domain.class_name}.{exported}"])
        if domain.exposure is Exposure.FACADE:
            for operation in domain.method_names:
                forwarder = _forwarder(domain, operation)
                origins = _origins(domain.module)
                for name in _annotated(forwarder):
                    if name in origins:
                        needed[origins[name]].add(name)
                rendered = ast.unparse(ast.fix_missing_locations(forwarder))
                class_lines.extend(["", *[f"    {line}" if line else "" for line in rendered.splitlines()]])
    for domain in OPERATION_CATALOGUE:
        needed[domain.module].add(domain.class_name)
    needed["vitruvio.runtime.session"].add("BrainSession")
    header = f"""# Generated by ``python -m vitruvio.runtime.generate_facade``. Do not edit by hand.
from __future__ import annotations

{_import_block(needed)}

"""
    source = header + "\n".join(class_lines) + '\n\n\n__all__ = ["GeneratedFacade"]\n'
    formatter = Path(sys.executable).with_name("ruff")
    completed = subprocess.run(
        [str(formatter), "format", "--stdin-filename", str(TARGET), "-"],
        input=source,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def documented() -> str | None:
    """``ARCHITECTURE.md`` with its operations table refreshed, or ``None`` outside a source checkout."""
    if not ARCHITECTURE.is_file():
        return None
    text = ARCHITECTURE.read_text(encoding="utf-8")
    head, _, rest = text.partition(BEGIN)
    _, _, tail = rest.partition(END)
    if not rest or not tail:
        raise RuntimeError(f"{ARCHITECTURE} has no {BEGIN}/{END} block to write the operations table into")
    return f"{head}{BEGIN}\n{operations_table()}\n{END}{tail}"


def main(argv: list[str] | None = None) -> int:
    """Write the artifacts, or check that the committed copies are current."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    artifacts = [(TARGET, render()), *([(ARCHITECTURE, documented())] if documented() is not None else [])]
    for path, expected in artifacts:
        if expected is None:
            continue
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                parser.error(f"{path} is stale; run python -m vitruvio.runtime.generate_facade")
            continue
        path.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess checks
    raise SystemExit(main())
