"""Turning a caller's string into an SDK value, and saying so when it will not turn.

Two conversions, used by nearly every operation. They live here rather than on the service because they are the
part of an operation that has nothing to do with which brain is open: a memory type is a memory type before
anything is resolved.

Both report a bad string as a *usage* failure rather than letting it surface as a protocol one. ``"semantic"``
misspelled is not a brain that failed to answer, and a caller told otherwise goes looking in the wrong place.
"""

from __future__ import annotations

from boltzmann.blocks.memory_type import MemoryType
from boltzmann.identity.digest import BlockId

from vitruvio.kernel import VitruvioError
from vitruvio.runtime.mapping import translated


def memory_type(value: str) -> MemoryType:
    """
    Coerce a string into a memory type, listing the valid ones when it will not coerce.

    Args:
        value (str): The name.

    Returns:
        MemoryType: The coerced value.

    Raises:
        VitruvioError: If the string names no module.
    """
    try:
        return MemoryType(value)
    except ValueError as error:
        permitted = ", ".join(item.value for item in MemoryType)
        raise VitruvioError(f"{value!r} is not a memory type; expected one of: {permitted}") from error


def block_id(value: str) -> BlockId:
    """Parse a block identity, reporting a malformed one as a usage error rather than a protocol failure."""
    with translated():
        return BlockId.parse(value)
