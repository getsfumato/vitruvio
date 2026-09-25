"""Registered data files, as tables: ``data."ventas.csv"``.

A CSV, TSV or Parquet file registered into a brain is a canonical block like any other. Its bytes are content
addressed and verified, and provenance records where it came from. This module lets a query read those bytes as a
table, so the knowledge a brain derived and the data it was derived from sit side by side in one query.

Four rules:

- **By name or by identity.** A dataset is named for the file it was registered from: the last path segment of its
  registration's ``origin``. It can also be named by its block id, or by a prefix of that id long enough to be
  unique. Two accessible datasets with the same file name make the name ambiguous, and it is refused rather than
  guessed.
- **Visibility as everywhere.** A superseded or demoted dataset is not reachable by name unless superseded blocks
  are included. It stays reachable by id, because an id names exactly one version.
- **Bytes from the store, verified.** The engine reads a dataset through the module's store, which checks the bytes
  against their digest, and only writes them to a temporary file of its own before the database is sealed. A query
  can name a dataset; it can never name a path.
- **Only what is named is loaded.** Parsing a file costs as much as the file, so a query pays for the datasets it
  names and nothing else. ``datasets`` lists them all without reading any.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from boltzmann.blocks.memory_type import MemoryType

from vitruvio.kernel import UsageError
from vitruvio.sql.tables import Column, TableSpec

if TYPE_CHECKING:
    from boltzmann.module.module import Module

DATA_SCHEMA = "data"
"""The schema a dataset is read from: ``data."ventas.csv"``, or ``brain.data."ventas.csv"`` in a compound."""

FORMATS: dict[str, str] = {
    "text/csv": "csv",
    "application/csv": "csv",
    "text/tab-separated-values": "tsv",
    "application/vnd.apache.parquet": "parquet",
    "application/parquet": "parquet",
    "application/x-parquet": "parquet",
}
"""The media types read as tables, and the reader each needs. The media type is part of the block's identity, so this
is decided at registration: a CSV registered as ``text/plain`` is text, not data."""

MIN_PREFIX = 8
"""The fewest hex digits an id prefix may have. Short enough to type, long enough that a collision is a surprise."""

DATASETS_TABLE = TableSpec(
    "datasets",
    None,
    (
        Column("id", "VARCHAR", "the canonical block's content address"),
        Column("name", "VARCHAR", 'the file it was registered from; what `data."<name>"` reads'),
        Column("origin", "VARCHAR", "where it was registered from, as recorded"),
        Column("media_type", "VARCHAR", "what kind of data it is"),
        Column("format", "VARCHAR", "csv, tsv or parquet"),
        Column("size", "BIGINT", "how many bytes"),
        Column("resolvable", "BOOLEAN", "whether its bytes can be read"),
        Column("superseded", "BOOLEAN", "a newer block supersedes it"),
        Column("demoted", "BOOLEAN", "its retrieval priority was lowered"),
    ),
    "every canonical block a query can read as a table, without reading any",
)


@dataclass(frozen=True, slots=True)
class Dataset:
    """
    One registered data file.

    Attributes:
        id (str): Its canonical block id.
        name (str | None): The last segment of its registration origin, when one was recorded.
        origin (str | None): The origin itself.
        media_type (str): What kind of data it is.
        format (str): Which reader it needs: ``csv``, ``tsv`` or ``parquet``.
        size (int): How many bytes.
        resolvable (bool): Whether its bytes can be read.
        hidden (tuple[bool, bool]): Whether it is superseded, and whether it is demoted.
    """

    id: str
    name: str | None
    origin: str | None
    media_type: str
    format: str
    size: int
    resolvable: bool
    hidden: tuple[bool, bool] = (False, False)

    @property
    def accessible(self) -> bool:
        return not any(self.hidden)

    def row(self) -> dict[str, Any]:
        """This dataset as a row of the ``datasets`` table."""
        return {
            "id": self.id,
            "name": self.name,
            "origin": self.origin,
            "media_type": self.media_type,
            "format": self.format,
            "size": self.size,
            "resolvable": self.resolvable,
            "superseded": self.hidden[0],
            "demoted": self.hidden[1],
        }


def _name(origin: str | None) -> str | None:
    if not origin:
        return None
    last = re.split(r"[\\/]", origin.rstrip("/\\"))[-1]
    return last or None


def _origins(provenance: Module | None, wanted: set[str]) -> dict[str, str]:
    """Where each wanted block was registered from, read from its registration records."""
    origins: dict[str, str] = {}
    if provenance is None or not wanted:
        return origins
    resolvable = provenance.resolvable()
    for identity in sorted(provenance.block_ids, key=str):
        if not resolvable.get(identity, True):
            continue
        record = provenance.get(identity).payload().get("record")
        if not isinstance(record, dict) or record.get("record_type") != "registration":
            continue
        block, origin = record.get("block"), record.get("origin")
        if isinstance(block, str) and block in wanted and isinstance(origin, str):
            origins.setdefault(block, origin)
    return origins


def catalog(modules: Mapping[MemoryType, Module], superseded: set[str], demoted: set[str]) -> list[Dataset]:
    """
    Every canonical block of this brain that a query can read as a table, in identity order.

    Args:
        modules (Mapping[MemoryType, Module]): The brain's installed modules.
        superseded (set[str]): The ids the ledger records as superseded.
        demoted (set[str]): The ids the ledger records as demoted.

    Returns:
        list[Dataset]: The datasets, hidden ones included and marked.
    """
    canonical = modules.get(MemoryType.CANONICAL)
    if canonical is None:
        return []
    resolvable = canonical.resolvable()
    found: list[tuple[str, dict[str, Any], bool]] = []
    for identity in sorted(canonical.block_ids, key=str):
        readable = resolvable.get(identity, True)
        payload = canonical.get(identity).payload() if readable else None
        if payload is None or payload.get("media_type") not in FORMATS:
            continue
        found.append((str(identity), payload, readable))
    origins = _origins(modules.get(MemoryType.PROVENANCE), {identity for identity, _, _ in found})
    return [
        Dataset(
            id=identity,
            name=_name(origins.get(identity)),
            origin=origins.get(identity),
            media_type=str(payload["media_type"]),
            format=FORMATS[str(payload["media_type"])],
            size=int(payload.get("size", 0)),
            resolvable=readable,
            hidden=(identity in superseded, identity in demoted),
        )
        for identity, payload, readable in found
    ]


def unreadable(modules: Mapping[MemoryType, Module]) -> list[str]:
    """Canonical members whose bytes are gone. Their media type went with them, so whether one was data is unknowable
    -- but a reference by id to one of them deserves "redacted", not "no such dataset"."""
    canonical = modules.get(MemoryType.CANONICAL)
    if canonical is None:
        return []
    return sorted(str(identity) for identity, readable in canonical.resolvable().items() if not readable)


def resolve(
    datasets: Iterable[Dataset],
    reference: str,
    *,
    include_superseded: bool,
    where: str = "",
    unresolvable: Iterable[str] = (),
) -> Dataset:
    """
    The one dataset a reference names: an exact file name, a full block id, or a unique id prefix.

    Raises:
        UsageError: When nothing matches, when a name is ambiguous, or when the dataset cannot be read.
    """
    every = list(datasets)
    reachable = [dataset for dataset in every if include_superseded or dataset.accessible]
    by_name = [dataset for dataset in reachable if dataset.name == reference]
    if len(by_name) > 1:
        ids = ", ".join(dataset.id for dataset in by_name)
        raise UsageError(
            f"{reference!r} names more than one dataset{where}: {ids}",
            hint=f'name one by its id instead: data."{by_name[0].id[:19]}"',
        )
    match = by_name[0] if by_name else None
    if match is None and reference.startswith("sha256:") and len(reference) - len("sha256:") >= MIN_PREFIX:
        # An id names exactly one version, so it reaches a hidden dataset too: that is what an id is for.
        by_id = [dataset for dataset in every if dataset.id.startswith(reference)]
        if len(by_id) > 1:
            raise UsageError(f"{reference!r} is a prefix of more than one dataset{where}", hint="give more of the id")
        match = by_id[0] if by_id else None
    looks_like_id = reference.startswith("sha256:") and len(reference) - len("sha256:") >= MIN_PREFIX
    if match is None and looks_like_id and any(identity.startswith(reference) for identity in unresolvable):
        raise UsageError(
            f"{reference!r}{where} is not resolvable: its bytes were redacted or never installed",
            hint="`vitruvio inspect resolvability` says which",
        )
    if match is None:
        names = sorted({dataset.name for dataset in reachable if dataset.name})
        hint = ("the datasets are: " + ", ".join(names)) if names else "register a .csv, .tsv or .parquet file first"
        raise UsageError(
            f"there is no dataset called {reference!r}{where}", hint=hint + "; `SELECT * FROM datasets` lists them"
        )
    if not match.resolvable:
        raise UsageError(
            f"dataset {reference!r}{where} is not resolvable: its bytes were redacted or never installed",
            hint="`vitruvio inspect resolvability` says which",
        )
    return match


__all__ = ["DATASETS_TABLE", "DATA_SCHEMA", "FORMATS", "MIN_PREFIX", "Dataset", "catalog", "resolve", "unreadable"]
