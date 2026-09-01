"""Shared TOML/JSON document loading at the CLI boundary."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from vitruvio.kernel import UsageError, VitruvioError


def load_document(
    path: Path,
    *,
    label: str,
    error_type: type[VitruvioError] = UsageError,
) -> dict[str, Any]:
    """Read one object-shaped TOML or JSON document with consistent diagnostics."""
    if not path.is_file():
        raise error_type(f"{label} {path} is not a file")
    try:
        text = path.read_text(encoding="utf-8")
        value = json.loads(text) if path.suffix.lower() == ".json" else tomllib.loads(text)
    except (OSError, ValueError) as error:
        raise error_type(f"{label} {path} could not be read: {error}") from error
    if not isinstance(value, dict):
        raise error_type(f"{label} {path} must contain an object/table")
    return value


__all__ = ["load_document"]
