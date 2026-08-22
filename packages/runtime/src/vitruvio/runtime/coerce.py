"""Turning a caller's string into an SDK value, and saying so when it will not turn.

A handful of conversions, used by nearly every operation. They live here rather than on the service because
they are the part of an operation that has nothing to do with which brain is open: a memory type is a memory
type before anything is resolved.

All of them report a bad string as a *usage* failure rather than letting it surface as a protocol one.
``"semantic"`` misspelled is not a brain that failed to answer, and a caller told otherwise goes looking in
the wrong place.

The reconcile strategy is here for a second reason as well. The kernel declares its own enum so that it stays
importable without the SDK, and this is the one place the two meet -- so if they ever drift, they drift here
rather than in whichever operation happened to convert one first.
"""

from __future__ import annotations

from boltzmann.blocks.memory_type import MemoryType
from boltzmann.identity.digest import BlockId, OciDigest
from boltzmann.reconcile import ReconcileStrategy as SdkReconcileStrategy

from vitruvio.kernel import ReconcileStrategy, VitruvioError
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


def snapshot_digest(value: str) -> OciDigest:
    """
    Parse a snapshot identity, reporting a malformed one as a usage error rather than a protocol failure.

    The same reasoning as :func:`block_id`, and worth having separately because the two are not
    interchangeable: a snapshot is addressed by the digest of its document, a block by a block identity, and
    passing one where the other belongs is a mistake a caller should be told about in those terms.

    Args:
        value (str): The digest, as ``sha256:...``.

    Returns:
        OciDigest: The parsed digest.
    """
    with translated():
        return OciDigest.parse(value)


def strategy(value: ReconcileStrategy | str) -> SdkReconcileStrategy:
    """
    Coerce a reconciliation strategy into the SDK's, listing the three when it will not coerce.

    There is no fallback and no default. A caller that has nothing to convert has nothing to reconcile with,
    and must say so rather than pass something arbitrary here.

    Args:
        value (ReconcileStrategy | str): The strategy, from configuration or from a command.

    Returns:
        SdkReconcileStrategy: The coerced value.

    Raises:
        VitruvioError: If the string names no strategy.
    """
    try:
        return SdkReconcileStrategy(str(value))
    except ValueError as error:
        permitted = ", ".join(item.value for item in SdkReconcileStrategy)
        raise VitruvioError(
            f"{value!r} is not a reconciliation strategy; expected one of: {permitted}",
            hint="the three differ in whose name stays on the incoming work, not in tidiness",
        ) from error
