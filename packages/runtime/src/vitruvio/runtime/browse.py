"""What a block looks like in a *list*, independent of who is drawing the list.

Reading a brain and querying one are different operations. A query returns an Evidence Bundle: ranked,
scored, and shaped by the planner. Browsing returns the module in its own order, with one line per block, and
no ranking at all -- which is what makes it possible to answer "what is actually in here" rather than "what
matches this". The protocol has nothing to say about the second question, so nothing here pretends it does:
these rows carry no score, and a row is never presented as more relevant than the row above it.

The reason this is a module in the runtime rather than a formatter in the CLI is the same reason
:mod:`vitruvio.runtime.wire` is: the TUI, a future MCP ``brain/list`` tool and a future HTTP ``GET /module``
have to agree on what a block's one-line identity *is*. Two implementations of "which field is the title"
would show a user two different brains.

**A block's title is chosen per memory type, not guessed.** A canonical block has no label -- it is bytes
plus a media type -- so its title is its origin-facing identity, while a semantic block has a label written
for exactly this purpose. Falling back to a generic scan of the payload would put ``"registration"`` in the
title column of a provenance record and call it a name.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from boltzmann.blocks.base import Block
from boltzmann.blocks.memory_type import MemoryType

TITLE_FIELDS: Mapping[str, tuple[str, ...]] = {
    "canonical": ("media_type",),
    "episodic": ("summary",),
    "semantic": ("label",),
    "procedural": ("label",),
    "provenance": (),
}
"""Which payload field names a block of each memory type, in preference order.

``provenance`` is empty on purpose: a provenance block has no name of its own, and its identity is the kind of
record it is plus the block it is about. That is assembled in :func:`_provenance_title` instead of pretending
one field carries it.
"""

DETAIL_FIELDS: Mapping[str, tuple[str, ...]] = {
    "canonical": ("origin",),
    "episodic": ("context", "outcome"),
    "semantic": ("statement",),
    "procedural": ("goal",),
    "provenance": (),
}
"""The second line: what the block says, as distinct from what it is called."""


def _first(payload: Mapping[str, Any], fields: tuple[str, ...]) -> str:
    """
    The first of ``fields`` present in ``payload`` as a non-empty string.

    Args:
        payload (Mapping[str, Any]): The block's payload.
        fields (tuple[str, ...]): Field names, in preference order.

    Returns:
        str: The value, stripped, or ``""`` when none is present.
    """
    for field in fields:
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _provenance_title(payload: Mapping[str, Any]) -> str:
    """
    Name a provenance block by what it records rather than by a field.

    Args:
        payload (Mapping[str, Any]): The block's payload.

    Returns:
        str: e.g. ``registration``, or ``provenance`` when the record cannot be read.
    """
    record = payload.get("record")
    if isinstance(record, dict):
        kind = record.get("record_type")
        if isinstance(kind, str) and kind:
            return kind
    return "provenance"


def _provenance_detail(payload: Mapping[str, Any]) -> str:
    """
    What a provenance record is about: the block, and the actor who wrote it.

    Args:
        payload (Mapping[str, Any]): The block's payload.

    Returns:
        str: A one-line description, or ``""``.
    """
    record = payload.get("record")
    if not isinstance(record, dict):
        return ""
    parts: list[str] = []
    if isinstance(subject := record.get("block"), str):
        parts.append(subject)
    actor = record.get("actor")
    if isinstance(actor, dict) and isinstance(actor.get("id"), str):
        parts.append(f"by {actor['id']}")
    if isinstance(at := record.get("at"), str):
        parts.append(at)
    return "  ".join(parts)


def row(block: Block, memory_type: MemoryType, *, origin: str | None = None, resolvable: bool = True) -> dict[str, Any]:
    """
    One block, as a line in a list.

    Args:
        block (Block): The resolved block.
        memory_type (MemoryType): Which module holds it.
        origin (str | None): Where a canonical block came from, read from its registration record by the caller.
            A canonical block carries no name of its own -- its identity must not depend on what anyone called
            the file -- so the name is passed in rather than looked for in the payload.
        resolvable (bool): Whether the store could read it. A row is still produced for a block that could not
            be read, because a version that names a block nobody can read is a fact a reader has to see rather
            than a row that silently vanishes from the list.

    Returns:
        dict[str, Any]: The row. ``title`` and ``detail`` are always present and always strings; everything
        else is present only when that memory type has it.
    """
    kind = memory_type.value
    payload = block.payload()

    title = _provenance_title(payload) if kind == "provenance" else _first(payload, TITLE_FIELDS.get(kind, ()))
    detail = _provenance_detail(payload) if kind == "provenance" else _first(payload, DETAIL_FIELDS.get(kind, ()))

    if kind == "canonical" and origin:
        # The file name becomes the title, and the full origin the detail when it says more than the name does: a
        # reader scanning a canonical module is looking for the lecture notes, not for the eleventh
        # `application/pdf`. The media type is a column of its own, so nothing is lost by moving it out of here.
        name = origin.rsplit("/", 1)[-1]
        title, detail = name, "" if origin == name else origin

    result: dict[str, Any] = {
        "block_id": str(block.block_id),
        "memory_type": kind,
        "title": title or "(unnamed)",
        "detail": detail,
        "resolvable": resolvable,
    }
    if origin:
        result["origin"] = origin

    # Everything below is per-memory-type and omitted rather than nulled when absent: a `media_type` of None on
    # a semantic block would suggest the field means something there.
    for field in ("media_type", "size", "subject", "occurred_at", "kind", "tags"):
        value = payload.get(field)
        if value not in (None, "", [], {}):
            result[field] = value
    if isinstance(blob := payload.get("blob"), str):
        result["blob"] = blob
    view = payload.get("normalized_view")
    if isinstance(view, dict) and isinstance(view.get("blob"), str):
        result["normalized_view"] = {
            "blob": view["blob"],
            "media_type": view.get("media_type", "text/plain"),
            "size": view.get("size", 0),
        }
    for field in ("evidence", "sources", "steps", "relations"):
        value = payload.get(field)
        if isinstance(value, list) and value:
            result[f"{field}_count"] = len(value)
    return result


def unreadable(block_id: str, memory_type: str, reason: str) -> dict[str, Any]:
    """
    A row for a block the store could not produce.

    Missing and tombstoned are different from each other and both are different from corruption, so the reason
    is carried rather than flattened into an absence.

    Args:
        block_id (str): The block a root still names.
        memory_type (str): Which module names it.
        reason (str): Why it could not be read.

    Returns:
        dict[str, Any]: A row shaped like any other, with ``resolvable`` false.
    """
    return {
        "block_id": block_id,
        "memory_type": memory_type,
        "title": "(unreadable)",
        "detail": reason,
        "resolvable": False,
    }


def matches(entry: Mapping[str, Any], needle: str) -> bool:
    """
    Whether a row satisfies a browse filter.

    Substring, case-insensitive, over the fields a person reads -- title, detail, subject, tags and the
    identity. This is deliberately *not* a query: it filters rows already fetched, names no index, and cannot
    rank. Text retrieval is :meth:`~vitruvio.runtime.BrainService.search`, which the planner drives; conflating
    the two would put a second, worse retrieval path next to the one with a cost model behind it.

    Args:
        entry (Mapping[str, Any]): A row from :func:`row`.
        needle (str): What to look for. Empty matches everything.

    Returns:
        bool: Whether it matches.
    """
    if not needle:
        return True
    wanted = needle.casefold()
    haystack = [
        str(entry.get("title", "")),
        str(entry.get("detail", "")),
        str(entry.get("subject", "")),
        str(entry.get("block_id", "")),
        str(entry.get("media_type", "")),
        *(str(tag) for tag in entry.get("tags", []) or []),
    ]
    return any(wanted in item.casefold() for item in haystack)


__all__ = ["DETAIL_FIELDS", "TITLE_FIELDS", "matches", "row", "unreadable"]
