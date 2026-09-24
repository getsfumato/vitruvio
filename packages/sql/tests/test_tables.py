"""The projection is the contract: every column a query may name, and what a block puts in it."""

from __future__ import annotations

from typing import Any

import pytest
from boltzmann.blocks.memory_type import MemoryType

from vitruvio.sql import BLOCKS_TABLE, TABLES, SqlEngine, project_block
from vitruvio.sql.guard import every_name
from vitruvio.sql.tables import table_for

# The `brain` fixture's type lives in conftest.py, which a test module cannot import by name.
Brain = Any


class TestSchema:
    def test_every_memory_type_has_a_table(self) -> None:
        assert {spec.memory_type for spec in TABLES.values()} == set(MemoryType)

    @pytest.mark.parametrize("name", [*TABLES, BLOCKS_TABLE.name])
    def test_every_table_carries_identity_and_visibility(self, name: str) -> None:
        spec = TABLES.get(name, BLOCKS_TABLE)
        columns = [column.name for column in spec.columns]
        assert columns[:4] == ["id", "memory_type", "schema_version", "resolvable"]
        assert columns[-2:] == ["superseded", "demoted"]

    def test_the_ledger_columns_are_not_stored(self) -> None:
        """They are joined on at load time, which is what keeps a cached table from freezing supersession."""
        assert {"superseded", "demoted"}.isdisjoint(column.name for column in table_for(MemoryType.SEMANTIC).stored)

    def test_the_schema_the_engine_lists_is_the_schema_it_loads(self, brain: Brain) -> None:
        with SqlEngine(brain.modules) as engine:
            for spec in SqlEngine.schema():
                connection = engine._open((spec.name,))
                described = connection.execute(f"DESCRIBE SELECT * FROM {every_name(spec.name)}").fetchall()
                declared = [(column.name, column.type) for column in spec.columns]
                assert [(row[0], row[1]) for row in described] == declared, spec.name


class TestProjection:
    def test_an_unreadable_block_keeps_its_row_and_loses_its_fields(self) -> None:
        row = project_block(MemoryType.EPISODIC, "sha256:" + "a" * 64, None, schema_version=1)
        assert row["resolvable"] is False
        assert row["schema_version"] is None
        assert row["summary"] is None

    def test_a_timestamp_is_stored_as_naive_utc(self) -> None:
        row = project_block(
            MemoryType.EPISODIC, "sha256:" + "a" * 64, {"summary": "s", "occurred_at": "2025-01-02T03:04:05Z"}
        )
        assert row["occurred_at"] == "2025-01-02 03:04:05"

    def test_a_provenance_record_is_flattened_and_kept_whole(self) -> None:
        record = {
            "record_type": "registration",
            "block": "sha256:" + "b" * 64,
            "actor": {"id": "ana", "kind": "human"},
            "at": "2025-01-02T03:04:05Z",
            "origin": "notes.md",
        }
        row = project_block(MemoryType.PROVENANCE, "sha256:" + "a" * 64, {"record": record})
        assert (row["record_type"], row["actor_id"], row["origin"]) == ("registration", "ana", "notes.md")
        assert row["recorded_at"] == "2025-01-02 03:04:05"
        assert '"origin": "notes.md"' in row["record"]
