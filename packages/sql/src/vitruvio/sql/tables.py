"""Every module, as a table: one row per member, one column per field its blocks can carry.

The projection is the contract of this package. A column named here is a column a caller's SQL may name, so
changing one is a change to what every saved query means -- which is why it is versioned by
:data:`SQL_PROJECTION_ID` and why a cached table built under another projection is never read.

Three rules shape it:

- **Every member gets a row.** A block whose bytes are gone -- tombstoned under an erasure policy, or never
  installed by a selective pull -- is still in the composition and still proves into the root, so it is still
  counted by ``count(*)``. Its row carries ``resolvable = false`` and nulls for everything its payload would have
  said. Dropping it would make a redacted brain look like a smaller one.
- **Values are what the block says, not what an index folded.** ``subject`` is the subject as written; a caller
  who wants case-insensitive matching says ``lower(subject)`` or ``ILIKE``, and the SQL means exactly what it reads.
- **Nested fields stay nested.** ``tags`` is a ``VARCHAR[]`` and ``steps`` a list of structs, so "which procedures
  use X" is an ``UNNEST`` the caller writes rather than a flattening this module guessed at.

Timestamps are the protocol's RFC3339 ``Z`` strings, stored as naive ``TIMESTAMP`` in UTC. Comparing one with
``'2025-01-01'`` works as written.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from boltzmann.blocks.memory_type import MemoryType

SQL_PROJECTION_ID = "vitruvio-sql-projection/1"
"""Bumped whenever a column is added, removed, renamed or retyped. A cached table records it and is rebuilt on a
mismatch, so a stale cache can cost a rebuild and never a wrong answer."""

_CONTENT = "STRUCT(blob VARCHAR, media_type VARCHAR, size BIGINT)"


@dataclass(frozen=True, slots=True)
class Column:
    """
    One column of a derived table.

    Attributes:
        name (str): What SQL calls it.
        type (str): Its DuckDB type.
        doc (str): One line on what it holds, for ``vitruvio sql --schema``.
    """

    name: str
    type: str
    doc: str


@dataclass(frozen=True, slots=True)
class TableSpec:
    """
    One derived table: the memory type it projects and its columns, in order.

    Attributes:
        name (str): The table name a query uses, which is the memory type's own name.
        memory_type (MemoryType | None): The module it projects. ``None`` for the ``blocks`` union.
        columns (tuple[Column, ...]): Its columns, the ledger's included.
        doc (str): One line on what a row is.
    """

    name: str
    memory_type: MemoryType | None
    columns: tuple[Column, ...]
    doc: str

    @property
    def stored(self) -> tuple[Column, ...]:
        """The columns projected from the blocks themselves, which are what the cache holds.

        The ledger columns are joined on at load time: supersession is a fact about the provenance module, not
        about this one, so it can change while this module's root does not -- and a cache keyed on this root
        would then keep answering with the old one.
        """
        return tuple(column for column in self.columns if column.name not in _LEDGER_COLUMNS)


_IDENTITY = (
    Column("id", "VARCHAR", "the block's content address, sha256:..."),
    Column("memory_type", "VARCHAR", "which module holds it"),
    Column("schema_version", "INTEGER", "its payload schema version; null when unreadable"),
    Column("resolvable", "BOOLEAN", "whether its bytes could be read; false for redacted or uninstalled blocks"),
)
_LEDGER = (
    Column("superseded", "BOOLEAN", "a newer block supersedes it; hidden unless superseded blocks are included"),
    Column("demoted", "BOOLEAN", "its retrieval priority was lowered; hidden unless superseded blocks are included"),
)
_LEDGER_COLUMNS = frozenset(column.name for column in _LEDGER)


def _table(name: str, memory_type: MemoryType | None, doc: str, *columns: Column) -> TableSpec:
    return TableSpec(name, memory_type, (*_IDENTITY, *columns, *_LEDGER), doc)


TABLES: dict[str, TableSpec] = {
    spec.name: spec
    for spec in (
        _table(
            "semantic",
            MemoryType.SEMANTIC,
            "a concept, fact, formula, relation, constraint, scheme or class",
            Column("kind", "VARCHAR", "concept, fact, formula, relation, constraint, scheme or class"),
            Column("label", "VARCHAR", "its short name"),
            Column("statement", "VARCHAR", "what it asserts"),
            Column("subject", "VARCHAR", "the subject it belongs to, as written"),
            Column("aliases", "VARCHAR[]", "other names it goes by"),
            Column("evidence", "VARCHAR[]", "the canonical blocks it cites"),
            Column("relations", 'STRUCT(predicate VARCHAR, "target" VARCHAR)[]', "typed edges to other blocks"),
            Column("scheme", "VARCHAR", "the catalog scheme, for schema-version-3 catalog structure"),
            Column("exclusive", "BOOLEAN", "whether a catalog scheme's classes are exclusive"),
            Column("content", _CONTENT, "the bytes it names out of line, if any"),
        ),
        _table(
            "episodic",
            MemoryType.EPISODIC,
            "something that happened, at a time",
            Column("summary", "VARCHAR", "what happened"),
            Column("occurred_at", "TIMESTAMP", "when it started, UTC"),
            Column("ended_at", "TIMESTAMP", "when it ended, UTC"),
            Column("context", "VARCHAR", "the circumstances"),
            Column("participants", "VARCHAR[]", "who took part"),
            Column("outcome", "VARCHAR", "how it ended"),
            Column("evidence", "VARCHAR[]", "the canonical blocks it cites"),
            Column("tags", "VARCHAR[]", "its tags"),
            Column("content", _CONTENT, "the bytes it names out of line, if any"),
        ),
        _table(
            "procedural",
            MemoryType.PROCEDURAL,
            "how to do something, as steps",
            Column("label", "VARCHAR", "its short name"),
            Column("goal", "VARCHAR", "what it achieves"),
            Column("subject", "VARCHAR", "the subject it belongs to, as written"),
            Column(
                "steps",
                'STRUCT("action" VARCHAR, condition VARCHAR, alternatives VARCHAR[], uses VARCHAR[])[]',
                "its steps, in order; `uses` names the blocks a step relies on",
            ),
            Column("preconditions", "VARCHAR[]", "what must hold before it starts"),
            Column("success_criteria", "VARCHAR[]", "how to tell it worked"),
            Column("evidence", "VARCHAR[]", "the canonical blocks it cites"),
            Column("content", _CONTENT, "the bytes it names out of line, if any"),
        ),
        _table(
            "canonical",
            MemoryType.CANONICAL,
            "registered source bytes",
            Column("blob", "VARCHAR", "the digest of its bytes"),
            Column("media_type", "VARCHAR", "what kind of bytes they are"),
            Column("size", "BIGINT", "how many bytes"),
            Column("normalized_view", _CONTENT, "its extracted text, if one was made"),
        ),
        _table(
            "provenance",
            MemoryType.PROVENANCE,
            "one provenance record: registration, derivation, supersession, demotion, removal, ...",
            Column("record_type", "VARCHAR", "registration, derivation, normalization, supersession, ..."),
            Column("block", "VARCHAR", "the block the record is about"),
            Column("blocks", "VARCHAR[]", "the blocks a removal names"),
            Column("recorded_at", "TIMESTAMP", "when the record says it happened, UTC"),
            Column("actor_id", "VARCHAR", "who did it"),
            Column("actor_kind", "VARCHAR", "human, agent or system"),
            Column("producer_kind", "VARCHAR", "what kind of producer derived the block"),
            Column("producer_id", "VARCHAR", "which producer derived the block"),
            Column("producer_version", "VARCHAR", "which version of it"),
            Column("derived_from", "VARCHAR[]", "what a derivation was derived from"),
            Column("supersedes", "VARCHAR", "the block a supersession replaces"),
            Column("origin", "VARCHAR", "where a registered source came from"),
            Column("task", "VARCHAR", "the task a derivation ran under"),
            Column("locator", "VARCHAR", "where in its source a derived block came from"),
            Column("reason", "VARCHAR", "why, when the record says"),
            Column("mechanism", "VARCHAR", "how a removal was carried out"),
            Column("record", "VARCHAR", "the whole record, as JSON text"),
        ),
    )
}
"""The per-module tables, by name. ``blocks`` is not here: it is a view over their shared columns."""

BLOCKS_TABLE = TableSpec("blocks", None, (*_IDENTITY, *_LEDGER), "every member of every module, identity only")
"""The union over every module, for questions about the brain as a whole."""


def table_for(memory_type: MemoryType) -> TableSpec:
    """The derived table that projects one module."""
    return TABLES[memory_type.value]


def _timestamp(value: Any) -> str | None:
    """An RFC3339 ``Z`` timestamp as DuckDB reads a naive UTC ``TIMESTAMP``."""
    if not isinstance(value, str):
        return None
    return value.removesuffix("Z").replace("T", " ")


def _record(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A provenance record, flattened onto the columns every record type shares where it has them."""
    record = payload.get("record")
    if not isinstance(record, dict):
        return {}
    actor: dict[str, Any] = record["actor"] if isinstance(record.get("actor"), dict) else {}
    producer: dict[str, Any] = record["producer"] if isinstance(record.get("producer"), dict) else {}
    return {
        "record_type": record.get("record_type"),
        "block": record.get("block"),
        "blocks": record.get("blocks"),
        "recorded_at": _timestamp(record.get("at")),
        "actor_id": actor.get("id"),
        "actor_kind": actor.get("kind"),
        "producer_kind": producer.get("kind"),
        "producer_id": producer.get("id"),
        "producer_version": producer.get("version"),
        "derived_from": record.get("derived_from"),
        "supersedes": record.get("supersedes"),
        "origin": record.get("origin"),
        "task": record.get("task"),
        "locator": record.get("locator"),
        "reason": record.get("reason"),
        "mechanism": record.get("mechanism"),
        "record": json.dumps(record, sort_keys=True, ensure_ascii=False),
    }


def project_block(
    memory_type: MemoryType,
    block_id: str,
    payload: Mapping[str, Any] | None,
    *,
    schema_version: int | None = None,
) -> dict[str, Any]:
    """
    One member, as a row of its module's table.

    Args:
        memory_type (MemoryType): Which module holds it.
        block_id (str): Its content address.
        payload (Mapping[str, Any] | None): What ``Block.payload()`` returned, or ``None`` when the bytes could not
            be read -- in which case the row still exists, marked unresolvable.
        schema_version (int | None): The block's payload schema version.

    Returns:
        dict[str, Any]: Every stored column of the table, present and possibly null.
    """
    spec = table_for(memory_type)
    row: dict[str, Any] = dict.fromkeys((column.name for column in spec.stored), None)
    row.update(
        id=block_id,
        memory_type=memory_type.value,
        schema_version=schema_version if payload is not None else None,
        resolvable=payload is not None,
    )
    if payload is None:
        return row
    if memory_type is MemoryType.PROVENANCE:
        row.update(_record(payload))
        return row
    for column in spec.stored:
        if column.name in row and row[column.name] is not None:
            continue
        value = payload.get(column.name)
        row[column.name] = _timestamp(value) if column.type == "TIMESTAMP" else value
    return row


__all__ = [
    "BLOCKS_TABLE",
    "SQL_PROJECTION_ID",
    "TABLES",
    "Column",
    "TableSpec",
    "project_block",
    "table_for",
]
