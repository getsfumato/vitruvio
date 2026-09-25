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
        assert result["verified_against"] == {
            "provenance": written.module("provenance")["root"],
            "semantic": written.module("semantic")["root"],
        }

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


class TestSimilarity:
    """``about`` and ``similarity`` against the brain's own vector indices, always reported approximate."""

    def test_similarity_scores_every_block_with_the_brains_model(self, written: BrainService) -> None:
        result = written.sql(
            "SELECT label, similarity(id, 'fourier sines') AS score FROM semantic ORDER BY score DESC, label"
        )
        labels = [row[0] for row in result["rows"]]
        assert labels[0].startswith("Fourier")
        assert labels[-1] == "Primes are infinite"
        assert result["exact"] is False
        (entry,) = result["approximate"]
        assert entry["kind"] == "similarity"
        assert set(entry["models"]) == {"semantic"}

    def test_about_counts_every_block_that_clears_the_threshold(self, written: BrainService) -> None:
        scores = written.sql("SELECT similarity(id, 'fourier') FROM semantic")["rows"]
        above = sum(1 for (score,) in scores if score >= 0.2)
        result = written.sql("SELECT count(*) FROM semantic WHERE about(id, 'fourier', 0.2)")
        assert result["rows"] == [[above]]
        assert result["approximate"][0]["min_score"] == [0.2]

    def test_a_module_without_vectors_is_reported_when_read(self, written: BrainService) -> None:
        result = written.sql("SELECT count(*) FROM provenance WHERE about(id, 'fourier', 0.2)")
        assert result["rows"] == [[0]]
        assert "provenance" in result["approximate"][0]["unscored"]
        assert any(item["kind"] == "similarity_unscored" for item in result["degradations"])

    def test_a_plain_query_does_not_open_the_retrieve_brain(
        self, written: BrainService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from vitruvio.runtime.assembly import Capability

        opened: list[Capability] = []
        real = written.session.brain

        def spy(capability: Capability = Capability.INSPECT) -> Any:
            opened.append(capability)
            return real(capability)

        monkeypatch.setattr(written.session, "brain", spy)
        written.sql("SELECT count(*) FROM semantic")
        assert Capability.RETRIEVE not in opened


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


class TestRetentionPurgesTheCache:
    """The SQL cache is a copy of payloads, so every mechanism that removes knowledge removes it too."""

    def _label(self, service: BrainService, label: str) -> str:
        (row,) = service.sql(f"SELECT id FROM semantic WHERE label = '{label}'")["rows"]
        return str(row[0])

    def test_a_redacted_block_leaves_no_readable_copy(self, tmp_path: Path, source_file: Path) -> None:
        """Redaction exists to make bytes unreadable even from retained history; a cached table must not outlive it."""
        from vitruvio.kernel import resolve

        config_file = tmp_path / "vitruvio.toml"
        config_file.write_text(
            '[brain]\npath = "./brain"\n\n[actor]\nid = "tester@example.com"\n\n'
            '[policy]\nprofile = "conservative"\nredactable_media_types = ["text/markdown"]\n',
            encoding="utf-8",
        )
        BrainService(resolve(brain=tmp_path / "brain", config=config_file, require_layout=False)).init()
        service = BrainService(resolve(brain=tmp_path / "brain", config=config_file, assisted_by=[]))
        target = str(service.register(Evidence.from_path(source_file, media_type="text/markdown"))["block_id"])
        assert service.sql("SELECT count(*) FROM canonical WHERE resolvable")["rows"] == [[1]]
        cache = service.config.derived / "sql"
        assert list(cache.glob("canonical-*.parquet"))

        service.redact(target, memory_type="canonical", reason="personal data")

        assert not list(cache.glob("*.parquet")), "a table projected before the redaction is still on disk"
        after = service.sql(f"SELECT resolvable, media_type FROM canonical WHERE id = '{target}'")["rows"]
        assert after == [[False, None]]

    def test_a_drop_purges_the_cache(self, written: BrainService) -> None:
        target = self._label(written, "Primes are infinite")
        written.drop([target], memory_type="semantic", reason="wrong")
        assert not list((written.config.derived / "sql").glob("*.parquet"))
        assert written.sql("SELECT count(*) FROM semantic WHERE label = 'Primes are infinite'")["rows"] == [[0]]

    def test_purging_an_absent_cache_is_a_no_op(self, config: Any) -> None:
        from vitruvio.runtime.ops.sql import purge_sql_cache

        assert purge_sql_cache(config) == 0
