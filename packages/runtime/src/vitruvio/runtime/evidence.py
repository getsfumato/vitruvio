"""Construct remote evidence without making an HTTP adapter import the ingest package."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vitruvio.ingest.evidence import Evidence


def evidence_from_bytes(
    data: bytes,
    *,
    origin: str,
    media_type: str,
    license: str | None = None,
    retention_policy: str | None = None,
    normalize_with: str | None = None,
    max_bytes: int | None = None,
) -> Evidence:
    from vitruvio.ingest.evidence import Evidence

    return Evidence.from_bytes(
        data,
        origin=origin,
        media_type=media_type,
        license=license,
        retention_policy=retention_policy,
        normalize_with=normalize_with,
        max_bytes=max_bytes,
    )
