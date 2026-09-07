"""The diagnosis operation: every row declared once, no model loaded, and the registry only when asked.

`inspect doctor` promised stale indices, missing modules, model-tag mismatches, tombstoned blocks and an unreachable
registry, and checked none of them -- it was sixty lines of CLI probing imports. The checks live in the runtime now,
and these tests pin the three properties that made the old shape a trap: the rows have one shape and one vocabulary,
nothing here loads an embedder, and a broken probe is a row rather than a crash.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from vitruvio.kernel import resolve
from vitruvio.runtime import BrainService
from vitruvio.runtime.ops.diagnosis import CHECKS, SEVERITIES, row

CODES = {check.code for check in CHECKS}
SHAPE = {"check", "code", "ok", "severity", "detail", "remedy", "data"}


def by_code(report: dict[str, Any], code: str) -> list[dict[str, Any]]:
    return [item for item in report["checks"] if item["code"] == code]


class TestRows:
    def test_every_emitted_row_uses_a_declared_code_and_one_shape(
        self, service: BrainService, source_file: Path
    ) -> None:
        """A machine branches on `code` and `severity`; a code that is not in CHECKS is a code the docs cannot list."""
        service.register(source_file, media_type="text/markdown")
        report = service.doctor()
        assert report["checks"]
        for item in report["checks"]:
            assert item["code"] in CODES, item
            assert item["severity"] in SEVERITIES, item
            assert set(item) == SHAPE, item
            assert item["ok"] is (item["severity"] in {"ok", "skip"}), item
            assert isinstance(item["data"], dict), item
            if item["severity"] in {"warn", "fail"}:
                assert item["remedy"], f"a tripped row must say what to do: {item}"
            else:
                assert item["remedy"] is None, f"advice on a healthy row reads as an instruction: {item}"
        assert report["failures"] == sum(1 for item in report["checks"] if item["severity"] == "fail")
        assert report["warnings"] == sum(1 for item in report["checks"] if item["severity"] == "warn")
        assert report["verdict"] in {"ok", "warn", "fail"}

    def test_the_declaration_is_the_only_vocabulary(self) -> None:
        with pytest.raises(KeyError):
            row("nope.nothing", "ok", "x")
        with pytest.raises(ValueError, match="severity"):
            row("cache.models", "meh", "x")

    def test_a_healthy_brain_has_no_failures_and_probes_no_registry(
        self, service: BrainService, source_file: Path
    ) -> None:
        service.register(source_file, media_type="text/markdown", normalize_with="markdown")
        service.index_build()
        report = service.doctor()
        assert [item["code"] for item in report["checks"] if item["severity"] == "fail"] == []
        assert report["registry_probed"] is False
        (reachable,) = by_code(report, "registry.reachable")
        assert reachable["severity"] == "skip"
        (fresh,) = by_code(report, "indices.stale")
        assert fresh["severity"] == "ok"
        assert "canonical.hash_map" in fresh["data"]["fresh"]


class TestOffline:
    def test_doctor_never_imports_an_embedder(self, service: BrainService, source_file: Path) -> None:
        """Asserted on sys.modules rather than by timing, so it cannot pass by being fast on a good day. The vector
        sidecar is on disk and its tag is checked -- from the header, without constructing what produced it."""
        service.register(source_file, media_type="text/markdown", normalize_with="markdown")
        service.index_build()
        for module in ("torch", "sentence_transformers"):
            sys.modules.pop(module, None)
        report = service.doctor()
        assert "torch" not in sys.modules
        assert "sentence_transformers" not in sys.modules
        (tags,) = by_code(report, "indices.model_mismatch")
        assert tags["severity"] == "ok"

    def test_a_stale_sidecar_is_reported_with_the_remedy(
        self, service: BrainService, source_file: Path, tmp_path: Path
    ) -> None:
        """Register after building and every canonical sidecar describes yesterday's composition."""
        service.register(source_file, media_type="text/markdown")
        service.index_build()
        other = tmp_path / "otra.md"
        other.write_text("# Otra nota\n\nUn parrafo mas.\n", encoding="utf-8")
        service.register(other, media_type="text/markdown")

        (stale,) = by_code(service.doctor(), "indices.stale")
        assert stale["severity"] == "warn"
        assert "canonical.hash_map" in stale["data"]["stale"]
        assert "index build" in stale["remedy"]

    def test_a_brain_that_does_not_exist_is_one_failing_row_and_nothing_opens(self, tmp_path: Path) -> None:
        """The probes that open the brain are skipped, not run into a wall one by one."""
        service = BrainService(resolve(brain=tmp_path / "absent", actor_id="t@example.com", require_layout=False))
        report = service.doctor()
        (layout,) = by_code(report, "brain.layout")
        assert layout["severity"] == "fail"
        assert "does not exist" in layout["detail"]
        assert by_code(report, "brain.integrity") == []
        assert by_code(report, "indices.stale") == []
        assert by_code(report, "cache.models"), "the environment is still reported"

    def test_a_partial_pull_is_reported(self, service: BrainService, source_file: Path, tmp_path: Path) -> None:
        service.register(source_file, media_type="text/markdown")
        service.index_build()
        registry_root = tmp_path / "registry"
        registry_root.mkdir()
        service.push("demo/brain", tag="v1", local=registry_root)

        consumer = BrainService(resolve(brain=tmp_path / "consumer", actor_id="c@example.com", require_layout=False))
        consumer.init()
        consumer.pull("demo/brain", tag="v1", modules=["canonical"], local=registry_root)

        report = consumer.doctor()
        (partial,) = by_code(report, "modules.partial")
        assert partial["severity"] == "warn"
        assert "selective" in partial["detail"]
        assert "dist pull" in partial["remedy"]


class TestProbe:
    """The one network request doctor makes, classified through fake clients that honour the SDK's `resolve`."""

    @staticmethod
    def _probe(client: Any) -> dict[str, Any]:
        from vitruvio.runtime.distribution import probe_registry

        return asyncio.run(probe_registry("demo/brain", client, "latest"))

    def test_a_published_repository_is_reachable(self) -> None:
        class Client:
            async def resolve(self, reference: str, tag: str) -> Any:
                return type("Manifest", (), {"layers": [1, 2, 3]})()

        outcome = self._probe(Client())
        assert outcome == {
            "reachable": True,
            "published": True,
            "detail": "demo/brain:latest resolves (3 layers)",
            "error": None,
        }

    def test_an_empty_repository_is_reachable_but_unpublished(self) -> None:
        from boltzmann.exceptions import ReferenceNotFoundError

        class Client:
            async def resolve(self, reference: str, tag: str) -> Any:
                raise ReferenceNotFoundError("nothing there")

        outcome = self._probe(Client())
        assert outcome["reachable"] is True
        assert outcome["published"] is False

    def test_a_transport_failure_is_unreachable(self) -> None:
        from boltzmann.exceptions import DistributionError

        class Client:
            async def resolve(self, reference: str, tag: str) -> Any:
                raise DistributionError("connection refused")

        outcome = self._probe(Client())
        assert outcome["reachable"] is False
        assert "connection refused" in outcome["detail"]


class TestAttribution:
    def test_registered_evidence_and_declared_catalogs_are_attributed(
        self, service: BrainService, source_file: Path
    ) -> None:
        source = service.register(source_file, media_type="text/markdown")["block_id"]
        service.catalog_apply(
            {
                "schema": "vitruvio.catalog/v1",
                "schemes": [{"name": "topic"}],
                "classes": [{"scheme": "topic", "label": "Science"}],
                "placements": [{"source": source, "classes": ["topic/Science"]}],
            }
        )
        (item,) = by_code(service.doctor(), "blocks.unattributed")
        assert item["severity"] == "ok"
        assert item["data"]["attributed"] == 4

    def test_a_block_no_record_names_is_a_warning_with_the_module_that_holds_it(
        self, service: BrainService, source_file: Path
    ) -> None:
        """The words a reader gets for such a block are the words a genuinely unknown creator gets."""
        from boltzmann.blocks.memory_type import MemoryType
        from boltzmann.catalog import SchemeDeclaration

        service.register(source_file, media_type="text/markdown")
        with service.session.write() as writable:
            writable._write(blocks={MemoryType.SEMANTIC: [SchemeDeclaration(scheme="topic").to_block()]}, provenance=[])

        (item,) = by_code(service.doctor(), "blocks.unattributed")
        assert item["severity"] == "warn"
        assert item["data"] == {"unattributed": {"semantic": 1}}
        assert "catalog apply" in item["remedy"]
