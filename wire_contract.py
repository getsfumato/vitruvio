"""The wire contract, checked against every operation the suite happens to run.

Eighty-two of the eighty-four facade operations return an anonymous ``dict[str, Any]``. Renaming a field, or
changing one from a string to an object, type-checks, passes, and breaks whichever interface reads it -- which
until now was only the CLI, and will not stay that way.

Writing eighty-four example invocations to pin those shapes would be a second test suite that drifts from the
first. This wraps the operations instead, so **the suite that already exists is the corpus**: every call any test
makes contributes what it saw to one checked-in file, and a call whose result carries a path the file does not
know fails the test that made it, naming the operation and the path.

What is recorded is the *shape*: a set of dotted paths, each with the JSON types seen at it. Not the values --
those are digests and timestamps and would change every run. Types as well as paths, because "renamed" and
"retyped" are both drift and only one of them shows up as a new path.

    pytest -p no:xdist --record-shapes      # rewrite the file from a full run

Recording needs one process: each worker sees a different slice, and merging behind pytest's back is machinery
this does not need. Checking needs no merge at all, which is why it is done per call rather than per session --
every worker checks whatever it ran.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

GOLDEN = Path(__file__).with_name("tests") / "operation_shapes.json"
UNOBSERVED = Path(__file__).with_name("tests") / "operation_shapes_unobserved.json"

_recording: dict[str, dict[str, set[str]]] = {}
_expected: dict[str, dict[str, set[str]]] = {}
_mode = {"record": False}


def _token(value: Any) -> str:
    """The JSON type of a leaf. ``bool`` before ``int`` because it is one in Python and is not one in JSON."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, str):
        return "str"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return type(value).__name__


def shape(payload: Any, path: str = "", into: dict[str, set[str]] | None = None) -> dict[str, set[str]]:
    """
    Every path in a payload, and the JSON types seen at each.

    Args:
        payload (Any): The result.
        path (str): The dotted path reached so far.
        into (dict[str, set[str]] | None): Accumulator, so several observations can merge.

    Returns:
        dict[str, set[str]]: Path to the set of type tokens seen there.
    """
    found = {} if into is None else into
    if isinstance(payload, dict):
        found.setdefault(path or ".", set()).add("object")
        for key, value in payload.items():
            shape(value, f"{path}.{key}" if path else str(key), found)
    elif isinstance(payload, (list, tuple)):
        found.setdefault(path or ".", set()).add("array")
        for value in payload:
            shape(value, f"{path}[]", found)
    else:
        found.setdefault(path or ".", set()).add(_token(payload))
    return found


def _merge(operation: str, observed: dict[str, set[str]]) -> None:
    known = _recording.setdefault(operation, {})
    for path, tokens in observed.items():
        known.setdefault(path, set()).update(tokens)


def _check(operation: str, observed: dict[str, set[str]]) -> None:
    known = _expected.get(operation)
    if known is None:
        raise AssertionError(
            f"{operation} has no recorded wire shape. Re-record with:\n    uv run pytest -p no:xdist --record-shapes"
        )
    for path, tokens in sorted(observed.items()):
        allowed = known.get(path)
        if allowed is None:
            raise AssertionError(
                f"{operation} returned a field nothing has recorded: {path!r} ({'|'.join(sorted(tokens))}).\n"
                f"If the change is intended, re-record with:\n"
                f"    uv run pytest -p no:xdist --record-shapes"
            )
        if not tokens <= allowed:
            raise AssertionError(
                f"{operation}.{path} is {'|'.join(sorted(tokens - allowed))}, "
                f"recorded as {'|'.join(sorted(allowed))}.\n"
                f"If the change is intended, re-record with:\n"
                f"    uv run pytest -p no:xdist --record-shapes"
            )


def _observe(operation: str, result: Any) -> None:
    observed = shape(result)
    if _mode["record"]:
        _merge(operation, observed)
    else:
        _check(operation, observed)


def _wrap(owner: type, name: str, operation: str) -> None:
    import functools
    import inspect

    original = getattr(owner, name)

    @functools.wraps(original)
    def observed(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        _observe(operation, result)
        return result

    @functools.wraps(original)
    async def observed_async(*args: Any, **kwargs: Any) -> Any:
        result = await original(*args, **kwargs)
        _observe(operation, result)
        return result

    # `functools.wraps` and not a hand-copied `__name__`: the conformance tests read `inspect.signature` through
    # `__wrapped__` and `get_type_hints` through `__annotations__`, and a wrapper that carried only the name made
    # the typed reconciliation slice look like it returned `Any`.
    setattr(owner, name, observed_async if inspect.iscoroutinefunction(original) else observed)


def install(record: bool) -> None:
    """
    Wrap every operation whose result is JSON, on the class that implements it.

    On the operations class rather than the facade, so a caller that reaches past the facade -- the TUI's
    reconciliation screen does -- is observed too, and a facade call is not counted twice.

    Args:
        record (bool): Rewrite the golden file at the end of the session instead of checking against it.
    """
    import importlib

    from vitruvio.runtime.operation_catalogue import OPERATION_CATALOGUE, ResultKind

    _mode["record"] = record
    if not record:
        _expected.update(
            {
                operation: {path: set(tokens) for path, tokens in paths.items()}
                for operation, paths in json.loads(GOLDEN.read_text(encoding="utf-8")).items()
            }
        )
    for domain in OPERATION_CATALOGUE:
        owner = getattr(importlib.import_module(domain.module), domain.class_name)
        for operation in domain.operations:
            if operation.result in (ResultKind.JSON, ResultKind.TYPED):
                for name in operation.names:
                    _wrap(owner, name, operation.name)


def write() -> None:
    """Write what this session saw, plus the operations it never reached."""
    from vitruvio.runtime.operation_catalogue import OPERATION_CATALOGUE, ResultKind

    GOLDEN.write_text(
        json.dumps(
            {op: {p: sorted(t) for p, t in sorted(paths.items())} for op, paths in sorted(_recording.items())}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    every = {
        operation.name
        for domain in OPERATION_CATALOGUE
        for operation in domain.operations
        if operation.result in (ResultKind.JSON, ResultKind.TYPED)
    }
    UNOBSERVED.write_text(json.dumps(sorted(every - set(_recording)), indent=2) + "\n", encoding="utf-8")
