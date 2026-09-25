"""Registered data files as tables: ``data."ventas.csv"``, read from the store and never from a path."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from boltzmann.blocks.canonical import CanonicalBlock
from boltzmann.blocks.memory_type import MemoryType
from boltzmann.blocks.provenance import Actor, ActorKind, ProvenanceBlock, RegistrationRecord, SupersessionRecord
from boltzmann.blocks.semantic import SemanticBlock, SemanticKind
from boltzmann.module.composition import Composition
from boltzmann.module.module import Module
from boltzmann.store.memory import MemoryBlockStore

from vitruvio.kernel import UsageError
from vitruvio.sql import SqlBrain, SqlEngine, guard

ACTOR = Actor(id="tester@example.com", kind=ActorKind.HUMAN)

SALES = b"region,amount\nnorth,10\nsouth,5\nnorth,7\n"
OLD_SALES = b"region,amount\nnorth,1\n"


def _parquet(tmp_path: Path) -> bytes:
    import duckdb

    path = tmp_path / "grades.parquet"
    duckdb.execute(
        f"COPY (SELECT * FROM (VALUES ('ana', 9), ('juan', 7)) v(student, grade)) TO '{path}' (FORMAT parquet)"
    )
    return path.read_bytes()


@dataclass
class Data:
    modules: dict[MemoryType, Module]
    ids: dict[str, str]


def _canonical(store: MemoryBlockStore, data: bytes, media_type: str) -> CanonicalBlock:
    return CanonicalBlock(blob=store.put_bytes(data), media_type=media_type, size=len(data))


def _registered(block: CanonicalBlock, origin: str) -> ProvenanceBlock:
    record = RegistrationRecord(block=block.block_id, actor=ACTOR, at="2025-01-01T00:00:00Z", origin=origin)
    return ProvenanceBlock(record=record)


@pytest.fixture
def data(tmp_path: Path) -> Data:
    """Sales as CSV (and an older version it superseded), grades as Parquet, one note that is not data, and one
    concept whose label names a region -- so knowledge and data can be joined."""
    store = MemoryBlockStore()
    sales = _canonical(store, SALES, "text/csv")
    old = _canonical(store, OLD_SALES, "text/csv")
    grades = _canonical(store, _parquet(tmp_path), "application/vnd.apache.parquet")
    note = _canonical(store, b"# notes\n", "text/markdown")
    concept = SemanticBlock(kind=SemanticKind.CONCEPT, label="north", statement="the northern region")
    provenance = [
        _registered(sales, "/home/ana/exports/ventas.csv"),
        _registered(old, "exports/ventas.csv"),
        _registered(grades, "notas.parquet"),
        _registered(note, "notes.md"),
        ProvenanceBlock(
            record=SupersessionRecord(
                block=sales.block_id, supersedes=old.block_id, actor=ACTOR, at="2025-02-01T00:00:00Z"
            )
        ),
    ]

    def module(kind: MemoryType, blocks: list[Any]) -> Module:
        return Module(kind, store, Composition(kind, [store.put_block(block) for block in blocks]))

    modules = {
        MemoryType.CANONICAL: module(MemoryType.CANONICAL, [sales, old, grades, note]),
        MemoryType.SEMANTIC: module(MemoryType.SEMANTIC, [concept]),
        MemoryType.PROVENANCE: module(MemoryType.PROVENANCE, provenance),
    }
    ids = {"sales": str(sales.block_id), "old": str(old.block_id), "grades": str(grades.block_id)}
    return Data(modules, ids)


def _query(data: Data, sql: str, **options: Any) -> Any:
    with SqlEngine(data.modules) as engine:
        return engine.query(sql, **options)


class TestReading:
    def test_a_csv_is_a_table_named_for_its_file(self, data: Data) -> None:
        outcome = _query(data, 'SELECT region, sum(amount) FROM data."ventas.csv" GROUP BY region')
        assert outcome.rows == [["north", 17], ["south", 5]]

    def test_a_parquet_file_is_a_table_with_its_own_types(self, data: Data) -> None:
        outcome = _query(data, 'SELECT student, grade FROM data."notas.parquet" WHERE grade > 8')
        assert outcome.rows == [["ana", 9]]
        assert [column.type for column in outcome.columns] == ["VARCHAR", "INTEGER"]

    def test_data_and_knowledge_join(self, data: Data) -> None:
        sql = 'SELECT s.statement, sum(v.amount) FROM data."ventas.csv" v JOIN semantic s ON s.label = v.region GROUP BY ALL'
        assert _query(data, sql).rows == [["the northern region", 17]]

    def test_a_dataset_can_be_named_by_id_or_a_unique_prefix(self, data: Data) -> None:
        prefix = data.ids["grades"][:15]
        assert _query(data, f'SELECT count(*) FROM data."{data.ids["grades"]}"').rows == [[2]]
        assert _query(data, f'SELECT count(*) FROM data."{prefix}"').rows == [[2]]

    def test_the_outcome_names_what_was_read_and_the_roots_it_came_from(self, data: Data) -> None:
        outcome = _query(data, 'SELECT count(*) FROM data."ventas.csv"')
        assert outcome.datasets == [
            {
                "reference": "data.ventas.csv",
                "id": data.ids["sales"],
                "name": "ventas.csv",
                "media_type": "text/csv",
                "size": len(SALES),
            }
        ]
        assert outcome.tables == ["data.ventas.csv"]
        assert set(outcome.verified_against) == {"canonical", "provenance"}
        assert outcome.exact is True

    def test_one_file_named_twice_is_loaded_once(self, data: Data) -> None:
        outcome = _query(
            data, 'SELECT count(*) FROM data."ventas.csv" a JOIN data."ventas.csv" b ON a.region = b.region'
        )
        assert outcome.rows == [[5]]
        assert len(outcome.datasets) == 1


class TestVisibility:
    def test_a_superseded_version_is_not_reached_by_name(self, data: Data) -> None:
        """Both versions were registered as ventas.csv; only the current one is accessible, so the name is not
        ambiguous and reads the new data."""
        assert _query(data, 'SELECT count(*) FROM data."ventas.csv"').rows == [[3]]

    def test_with_superseded_included_the_name_is_ambiguous_and_refused(self, data: Data) -> None:
        with pytest.raises(UsageError, match="more than one dataset") as raised:
            _query(data, 'SELECT count(*) FROM data."ventas.csv"', include_superseded=True)
        assert raised.value.hint is not None
        assert "sha256:" in raised.value.hint

    def test_a_superseded_version_is_still_reached_by_id(self, data: Data) -> None:
        assert _query(data, f'SELECT sum(amount) FROM data."{data.ids["old"]}"').rows == [[1]]


class TestListing:
    def test_datasets_lists_every_data_file_and_reads_none(self, data: Data) -> None:
        outcome = _query(data, "SELECT name, format, size FROM datasets ORDER BY name")
        assert outcome.rows == [["notas.parquet", "parquet", outcome.rows[0][2]], ["ventas.csv", "csv", len(SALES)]]
        assert outcome.datasets == []
        assert outcome.hidden == {"datasets": 1}

    def test_datasets_with_superseded_shows_both_versions(self, data: Data) -> None:
        rows = _query(data, "SELECT name, superseded FROM datasets ORDER BY size", include_superseded=True).rows
        assert rows == [["ventas.csv", True], ["ventas.csv", False], ["notas.parquet", False]]


class TestRefusals:
    def test_an_unknown_dataset_lists_the_known_ones(self, data: Data) -> None:
        with pytest.raises(UsageError, match="no dataset called") as raised:
            _query(data, 'SELECT * FROM data."compras.csv"')
        assert raised.value.hint is not None
        assert "ventas.csv" in raised.value.hint

    def test_a_file_that_is_not_data_is_not_a_dataset(self, data: Data) -> None:
        with pytest.raises(UsageError, match="no dataset called"):
            _query(data, 'SELECT * FROM data."notes.md"')

    def test_a_prefix_that_is_too_short_is_not_an_id(self, data: Data) -> None:
        with pytest.raises(UsageError, match="no dataset called"):
            _query(data, 'SELECT * FROM data."sha256:ab"')

    def test_a_redacted_dataset_says_so(self, data: Data) -> None:
        canonical = data.modules[MemoryType.CANONICAL]
        from boltzmann.identity.digest import BlockId

        tombstoned = Module(
            MemoryType.CANONICAL, canonical.store, canonical.composition, tombstones=[BlockId.parse(data.ids["grades"])]
        )
        modules = {**data.modules, MemoryType.CANONICAL: tombstoned}
        with SqlEngine(modules) as engine, pytest.raises(UsageError, match="not resolvable"):
            engine.query(f'SELECT * FROM data."{data.ids["grades"]}"')

    def test_a_file_registered_as_csv_that_is_not_csv_is_a_usage_error(self, tmp_path: Path) -> None:
        store = MemoryBlockStore()
        broken = _canonical(store, b"\x00\x01\x02 not a csv \xff\xfe", "application/vnd.apache.parquet")
        modules = {
            MemoryType.CANONICAL: Module(
                MemoryType.CANONICAL, store, Composition(MemoryType.CANONICAL, [store.put_block(broken)])
            ),
            MemoryType.PROVENANCE: Module(
                MemoryType.PROVENANCE,
                store,
                Composition(MemoryType.PROVENANCE, [store.put_block(_registered(broken, "broken.parquet"))]),
            ),
        }
        with SqlEngine(modules) as engine, pytest.raises(UsageError, match="could not be read as parquet"):
            engine.query('SELECT * FROM data."broken.parquet"')


class TestSandbox:
    def test_a_dataset_reference_cannot_become_a_path(self, data: Data) -> None:
        """The reference is looked up in the catalog; a path-shaped name is just a name nothing matches."""
        with pytest.raises(UsageError, match="no dataset called"):
            _query(data, 'SELECT * FROM data."/etc/passwd"')

    def test_the_guard_rewrites_a_dataset_onto_an_internal_table(self) -> None:
        admitted = guard('SELECT * FROM data."ventas.csv"')
        assert admitted.datasets == ((None, "ventas.csv"),)
        assert "ventas.csv" not in admitted.executed.split(" AS ")[0]


class TestCompound:
    def test_a_compound_reads_one_brains_dataset_by_qualifying_it(self, data: Data) -> None:
        engine = SqlEngine(brains={"shop": SqlBrain(data.modules), "empty": SqlBrain({})})
        with engine:
            outcome = engine.query('SELECT sum(amount) FROM shop.data."ventas.csv"')
        assert outcome.rows == [[22]]
        assert outcome.datasets[0]["reference"] == "shop.data.ventas.csv"
        assert "shop.canonical" in outcome.verified_against

    def test_a_compound_lists_every_brains_datasets(self, data: Data) -> None:
        engine = SqlEngine(brains={"shop": SqlBrain(data.modules), "other": SqlBrain(data.modules)})
        with engine:
            rows = engine.query("SELECT brain, count(*) FROM datasets GROUP BY brain").rows
        assert rows == [["other", 2], ["shop", 2]]

    def test_an_unqualified_dataset_in_a_compound_is_refused(self, data: Data) -> None:
        engine = SqlEngine(brains={"shop": SqlBrain(data.modules), "other": SqlBrain(data.modules)})
        with engine, pytest.raises(UsageError, match="which brain"):
            engine.query('SELECT * FROM data."ventas.csv"')


class TestReviewRegressions:
    """The cases the review of this PR reproduced, each pinned."""

    def test_a_cte_cannot_shadow_a_loaded_dataset(self, data: Data) -> None:
        forged = 'WITH __vitruvio_data_0 AS (SELECT 999 AS amount) SELECT amount FROM data."ventas.csv"'
        with pytest.raises(UsageError, match="reserved"):
            _query(data, forged)

    def test_datasets_without_provenance_is_not_exact(self, data: Data) -> None:
        modules = {MemoryType.CANONICAL: data.modules[MemoryType.CANONICAL]}
        with SqlEngine(modules) as engine:
            outcome = engine.query("SELECT count(*) FROM datasets")
        assert outcome.exact is False
        assert outcome.not_installed == ["provenance"]
        assert outcome.approximate[0]["kind"] == "visibility_unknown"

    def test_datasets_without_provenance_is_exact_when_nothing_is_to_be_hidden(self, data: Data) -> None:
        modules = {MemoryType.CANONICAL: data.modules[MemoryType.CANONICAL]}
        with SqlEngine(modules) as engine:
            outcome = engine.query("SELECT count(*) FROM datasets", include_superseded=True)
        assert outcome.exact is True

    def test_verify_keeps_a_datasets_own_id_column(self, tmp_path: Path) -> None:
        store = MemoryBlockStore()
        table = _canonical(store, b"id,value\n1,a\n2,b\n", "text/csv")
        modules = {
            MemoryType.CANONICAL: Module(
                MemoryType.CANONICAL, store, Composition(MemoryType.CANONICAL, [store.put_block(table)])
            ),
            MemoryType.PROVENANCE: Module(
                MemoryType.PROVENANCE,
                store,
                Composition(MemoryType.PROVENANCE, [store.put_block(_registered(table, "ids.csv"))]),
            ),
        }
        with SqlEngine(modules) as engine:
            outcome = engine.query('SELECT id, value FROM data."ids.csv"', verify=True)
        assert outcome.rows == [[1, "a"], [2, "b"]], "a CSV's own id column is not a claim about the brain"
        assert outcome.verified_rows == 0
        assert [item["kind"] for item in outcome.degradations] == ["verification_skipped"]

    def test_verify_proves_the_dataset_block_itself(self, data: Data) -> None:
        with SqlEngine(data.modules) as engine:
            outcome = engine.query('SELECT count(*) FROM data."ventas.csv"', verify=True)
        assert all(item["kind"] != "verification_failed" for item in outcome.degradations)

    def test_verify_still_drops_a_block_identity_that_is_not_a_member(self, data: Data) -> None:
        with SqlEngine(data.modules) as engine:
            outcome = engine.query("SELECT 'sha256:' || repeat('0', 64) AS id", verify=True)
        assert outcome.rows == []
        assert outcome.degradations[0]["kind"] == "verification_failed"
