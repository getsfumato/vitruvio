"""Turning an operation's result into JSON, and refusing to guess when it is not.

Every interface serializes the same dictionaries, so what "serializable" means has to be decided once. It used to
be decided by ``json.dumps(..., default=str)``, which decides it by never refusing: an object with no JSON form
becomes its ``repr()``, the envelope is well-formed, the test passes, and the field quietly carries
``PosixPath('/Users/...')`` where a caller expected a string. That is the failure mode the wire contract exists to
prevent, and the flag was in the CLI's very first commit with no failure behind it.

In the kernel rather than beside ``vitruvio.runtime.wire``, which is where SDK models become dictionaries, because
this is not about the SDK: it is a property of the dictionary, and the CLI must be able to check it without paying
the ~124ms of importing the runtime on every ``--help``.
"""

from __future__ import annotations

import json
from math import isfinite
from typing import Any

from vitruvio.kernel.errors import VitruvioError

JSON_SCALARS = (str, int, float, bool, type(None))
"""What may appear as a leaf. ``bool`` before ``int`` is irrelevant here and deliberate elsewhere."""


class WireContractError(VitruvioError):
    """An operation returned something no interface can serialize.

    Exit 1 rather than a usage code, and that is the accurate one: no caller can cause this and no caller can fix
    it. An operation put a value in its result that the wire contract does not allow, which is a bug in vitruvio.
    """

    code = "WIRE_CONTRACT"


def dumps(payload: Any, *, indent: int | None = 2) -> str:
    """
    Serialize a result, naming what stopped it rather than coercing it.

    Args:
        payload (Any): The result to serialize.
        indent (int | None): Passed through to :func:`json.dumps`.

    Returns:
        str: The JSON text.

    Raises:
        WireContractError: Something in the payload has no JSON form.
    """
    try:
        # `allow_nan=False`: Python's default emits the bare words `NaN`, `Infinity` and `-Infinity`, which are
        # not JSON and which a strict parser refuses. A statistic that came out non-finite is a bug upstream, and
        # an envelope no consumer can read is a worse way to learn about it than an error naming the field.
        return json.dumps(payload, indent=indent, sort_keys=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        path, value = _offender(payload)
        described = "not finite" if isinstance(value, float) else f"{type(value).__name__}, which has no JSON form"
        raise WireContractError(
            f"{path} is {described} ({error})",
            hint="the operation that produced it must convert the value, rather than the envelope guessing",
        ) from error


def _offender(payload: Any, path: str = "data") -> tuple[str, Any]:
    """
    Where the first unserializable value is, for an error a reader can act on.

    Walked only after :func:`json.dumps` has already failed, so the cost is paid once and never on the happy
    path. ``json.dumps`` says *what* it could not serialize and never *where*, and "Object of type PosixPath is
    not JSON serializable" over a result with ninety keys is not a place to start looking. Its message for a
    non-finite float names neither, which is worse still.

    Args:
        payload (Any): The value to search.
        path (str): The dotted path reached so far.

    Returns:
        tuple[str, Any]: The path and the value found there.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if not isinstance(key, str):
                return f"{path}[{key!r}]", key
            found = _offender(value, f"{path}.{key}")
            if found[1] is not _NOTHING:
                return found
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            found = _offender(value, f"{path}[{index}]")
            if found[1] is not _NOTHING:
                return found
    elif not isinstance(payload, JSON_SCALARS) or (isinstance(payload, float) and not isfinite(payload)):
        return path, payload
    return path, _NOTHING


class _Nothing:
    """A sentinel distinct from ``None``, which is itself a valid JSON value."""


_NOTHING = _Nothing()


__all__ = ["WireContractError", "dumps"]
