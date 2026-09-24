"""SQL through the service: the same answer any interface would get, over a brain written the ordinary way."""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

import pytest

from vitruvio.ingest.evidence import Evidence
from vitruvio.kernel import UsageError
from vitruvio.runtime import BrainService


def _facts(service: BrainService, source_file: Path, facts: list[dict[str, Any]]) -> str:
    source = service.register(Evidence.from_path(source_file, media_type="text/markdown"))["block_id"]
    task = service.define_task(source, allowed=["semantic"])
    service.commit_candidates(
        {
            "candidates": [
                {"memory_type": "semantic", "payload": payload, "evidence": [source], "confidence": "0.9"}
                for payload in facts
            ]
        },
        task,
    )
    return str(source)


@pytest.fixture
def written(service: BrainService, source_file: Path) -> BrainService:
    _facts(
        service,
        source_file,
        [
            {"kind": "fact", "label": "Fourier decomposes", "statement": "into sines", "subject": "analysis"},
            {"kind": "fact", "label": "Sines are periodic", "statement": "with period 2pi", "subject": "analysis"},
            {"kind": "concept", "label": "Fourier series", "statement": "a sum of sines", "subject": "analysis"},
            {"kind": "fact", "label": "Primes are infinite", "statement": "Euclid", "subject": "number theory"},
        ],
    )
    return service


class TestSql:
    def test_a_count_over_a_written_brain(self, written: BrainService) -> None:
        result = written.sql("SELECT subject, count(*) AS n FROM semantic WHERE kind = 'fact' GROUP BY subject")
        assert result["rows"] == [["analysis", 2], ["number theory", 1]]
        assert [column["name"] for column in result["columns"]] == ["subject", "n"]
        assert result["exact"] is True
        assert not result["truncated"]

    def test_the_result_names_the_root_it_was_computed_over(self, written: BrainService) -> None:
        result = written.sql("SELECT count(*) FROM semantic")
        assert result["verified_against"] == {"semantic": written.module("semantic")["root"]}

    def test_evidence_joins_back_to_the_registered_source(self, written: BrainService) -> None:
        result = written.sql(
            "SELECT c.media_type, count(*) FROM semantic s, UNNEST(s.evidence) AS u(e) "
            "JOIN canonical c ON c.id = e GROUP BY ALL"
        )
        assert result["rows"] == [["text/markdown", 4]]

    def test_registration_is_readable_from_provenance(self, written: BrainService) -> None:
        result = written.sql("SELECT record_type, count(*) FROM provenance GROUP BY ALL")
        counts = dict(map(tuple, result["rows"]))
        assert counts["registration"] == 1
        assert counts["derivation"] == 4

    def test_a_replaced_source_is_hidden_until_asked_for(self, service: BrainService, source_file: Path) -> None:
        old = service.register(Evidence.from_path(source_file, media_type="text/markdown"))["block_id"]
        successor = source_file.parent / "fourier-v2.md"
        successor.write_text("# Series de Fourier, revisadas\n", encoding="utf-8")
        service.replace(Evidence.from_path(successor, media_type="text/markdown"), supersedes=old)

        hidden = service.sql("SELECT count(*) FROM canonical")
        assert hidden["rows"] == [[1]]
        assert hidden["hidden"] == {"canonical": 1}
        every = service.sql("SELECT count(*) FROM canonical", include_superseded=True)
        assert every["rows"] == [[2]]

    def test_tables_are_cached_under_the_brains_derived_state(self, written: BrainService) -> None:
        written.sql("SELECT count(*) FROM semantic")
        assert list((written.config.derived / "sql").glob("semantic-*.parquet"))

    def test_a_write_after_a_query_is_seen_by_the_next_one(self, written: BrainService, source_file: Path) -> None:
        assert written.sql("SELECT count(*) FROM semantic")["rows"] == [[4]]
        other = source_file.parent / "other.md"
        other.write_text("otra fuente\n", encoding="utf-8")
        _facts(written, other, [{"kind": "fact", "label": "New", "statement": "s"}])
        assert written.sql("SELECT count(*) FROM semantic")["rows"] == [[5]]

    def test_verification_proves_every_returned_row(self, written: BrainService) -> None:
        result = written.sql("SELECT id FROM semantic", verify=True)
        assert result["verified_rows"] == 4

    def test_a_write_is_refused_as_usage(self, written: BrainService) -> None:
        with pytest.raises(UsageError):
            written.sql("DELETE FROM semantic")

    def test_explain_runs_nothing_and_returns_a_plan(self, written: BrainService) -> None:
        result = written.sql_explain("SELECT count(*) FROM semantic")
        assert result["rows"] == []
        assert result["plan"]

    def test_the_schema_opens_no_brain(self, config: Any) -> None:
        """Before `init` there is no brain at all, and the schema is still the schema."""
        schema = BrainService(config).sql_schema()
        assert [table["name"] for table in schema["tables"]] == [
            "semantic",
            "episodic",
            "procedural",
            "canonical",
            "provenance",
            "blocks",
        ]
        assert schema["projection"].startswith("vitruvio-sql-projection/")


class TestWithoutTheExtra:
    def test_a_missing_engine_is_a_usage_error_naming_the_extra(
        self, written: BrainService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real = builtins.__import__

        def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "vitruvio.sql" or name.startswith("vitruvio.sql."):
                raise ImportError(name)
            return real(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse)
        with pytest.raises(UsageError) as raised:
            written.sql("SELECT 1")
        assert raised.value.hint == "install vitruvio[sql]"
