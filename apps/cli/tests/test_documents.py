"""TOML/JSON command inputs share one predictable boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from vitruvio.cli.documents import load_document
from vitruvio.kernel import ConfigError, UsageError


def test_document_loader_reads_toml_and_json_objects(tmp_path: Path) -> None:
    toml = tmp_path / "document.toml"
    toml.write_text('name = "value"\n', encoding="utf-8")
    json = tmp_path / "document.json"
    json.write_text('{"name": "value"}\n', encoding="utf-8")

    assert load_document(toml, label="input") == {"name": "value"}
    assert load_document(json, label="input") == {"name": "value"}


def test_document_loader_parameterizes_the_command_error_semantics(tmp_path: Path) -> None:
    malformed = tmp_path / "document.json"
    malformed.write_text("[]\n", encoding="utf-8")

    with pytest.raises(UsageError, match="object/table"):
        load_document(malformed, label="manifest")
    with pytest.raises(ConfigError, match="object/table"):
        load_document(malformed, label="trust root", error_type=ConfigError)
