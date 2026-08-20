"""Reading a brain: what ``blocks``, ``content``, ``export_content`` and ``related`` promise.

These four are the interface ``vitruvio browse`` and ``vitruvio inspect blocks`` are both built on, so what is
pinned here is the *contract*, not the rendering: which field is a row's title, that an unreadable block still
produces a row, that a filter is bounded by the same limit an unfiltered page is, and that content comes back
verified or not at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from vitruvio.kernel import VitruvioError
from vitruvio.runtime import BrainService


@pytest.fixture
def registered(service: BrainService, source_file: Path) -> dict[str, str]:
    """Two canonical blocks with different origins, which is what a row's title comes from."""
    first = service.register(source_file, media_type="text/markdown")
    other = source_file.parent / "notas.txt"
    other.write_text("una nota corta", encoding="utf-8")
    second = service.register(other, media_type="text/plain")
    return {"markdown": first["block_id"], "text": second["block_id"]}


class TestBlocks:
    def test_a_canonical_row_is_titled_by_the_origin_its_registration_recorded(
        self, service: BrainService, registered: dict[str, str]
    ) -> None:
        """A canonical block carries no name -- its identity must not depend on what anyone called the file --
        so the name a reader recognises has to come from provenance or not at all."""
        titles = {row["title"] for row in service.blocks("canonical")["rows"]}
        assert titles == {"fourier.md", "notas.txt"}

    def test_a_row_carries_the_content_address_of_the_bytes_it_names(
        self, service: BrainService, registered: dict[str, str]
    ) -> None:
        """Without `blob` a caller cannot ask for the content, which is most of what browsing a canonical
        module is for."""
        rows = {row["block_id"]: row for row in service.blocks("canonical")["rows"]}
        assert rows[registered["markdown"]]["blob"].startswith("sha256:")
        assert rows[registered["markdown"]]["media_type"] == "text/markdown"
        assert rows[registered["markdown"]]["size"] > 0

    def test_the_page_reports_what_it_did_not_return(self, service: BrainService, registered: dict[str, str]) -> None:
        """`truncated` is what tells a caller to ask for the next page; a reader who cannot see it has silently
        been shown half a module."""
        page = service.blocks("canonical", limit=1)
        assert len(page["rows"]) == 1
        assert page["block_count"] == 2
        assert page["truncated"] is True
        assert service.blocks("canonical", limit=1, offset=1)["truncated"] is False

    def test_a_filter_narrows_the_rows_and_says_how_many_matched(
        self, service: BrainService, registered: dict[str, str]
    ) -> None:
        """The filter is over rows already read, so `matched` is a count of rows and not an index's estimate."""
        page = service.blocks("canonical", contains="NOTAS")
        assert [row["title"] for row in page["rows"]] == ["notas.txt"]
        assert page["matched"] == 1
        assert page["block_count"] == 2, "the module's size does not change because a filter was applied"

    def test_a_module_nobody_installed_lists_as_empty_rather_than_failing(self, service: BrainService) -> None:
        """A selectively pulled brain is missing modules on purpose, and browsing one is not an error."""
        page = service.blocks("procedural")
        assert page["rows"] == []
        assert page["block_count"] == 0

    def test_a_memory_type_that_does_not_exist_is_a_usage_error(self, service: BrainService) -> None:
        with pytest.raises(VitruvioError, match="not a memory type"):
            service.blocks("episodical")

    def test_a_provenance_row_is_named_by_the_record_it_holds(
        self, service: BrainService, registered: dict[str, str]
    ) -> None:
        """A provenance block has no name of its own: what identifies it is the kind of record it is."""
        titles = {row["title"] for row in service.blocks("provenance")["rows"]}
        assert titles == {"registration"}


class TestContent:
    def test_content_returns_the_bytes_the_block_names(self, service: BrainService, source_file: Path) -> None:
        registration = service.register(source_file, media_type="text/markdown")
        row = next(row for row in service.blocks("canonical")["rows"] if row["block_id"] == registration["block_id"])
        assert service.content(row["blob"]) == source_file.read_bytes()

    def test_a_digest_the_store_does_not_hold_is_an_error_rather_than_empty_bytes(self, service: BrainService) -> None:
        """Empty bytes would be indistinguishable from an empty file, and a viewer would draw nothing and say
        nothing."""
        with pytest.raises(VitruvioError):
            service.content("sha256:" + "0" * 64)

    def test_export_writes_the_bytes_and_reports_where(
        self, service: BrainService, source_file: Path, tmp_path: Path
    ) -> None:
        registration = service.register(source_file, media_type="text/markdown")
        row = next(row for row in service.blocks("canonical")["rows"] if row["block_id"] == registration["block_id"])
        target = tmp_path / "out" / "copy.md"
        result = service.export_content(row["blob"], target)
        assert target.read_bytes() == source_file.read_bytes()
        assert result["path"] == str(target)
        assert result["size"] == len(source_file.read_bytes())

    def test_exporting_into_a_directory_names_the_file_after_the_digest(
        self, service: BrainService, source_file: Path, tmp_path: Path
    ) -> None:
        """So that two exports of different content into one directory cannot overwrite each other."""
        registration = service.register(source_file, media_type="text/markdown")
        row = next(row for row in service.blocks("canonical")["rows"] if row["block_id"] == registration["block_id"])
        directory = tmp_path / "exports"
        directory.mkdir()
        result = service.export_content(row["blob"], directory)
        assert Path(result["path"]).parent == directory
        assert ":" not in Path(result["path"]).name, "a colon is not a filename on every platform"


class TestRelated:
    def test_the_registration_record_is_found_from_the_block_it_registered(
        self, service: BrainService, source_file: Path
    ) -> None:
        registration = service.register(source_file, media_type="text/markdown")
        result = service.related(registration["block_id"])
        assert result["count"] == 1
        assert result["records"][0]["record"]["record_type"] == "registration"
        assert result["records"][0]["record"]["block"] == registration["block_id"]

    def test_a_block_nothing_records_comes_back_empty_rather_than_failing(self, service: BrainService) -> None:
        """Normal in a selectively pulled brain, where provenance may not be installed at all."""
        assert service.related("sha256:" + "1" * 64) == {
            "block": "sha256:" + "1" * 64,
            "records": [],
            "count": 0,
            "truncated": False,
        }


class TestOriginsDegradeHonestly:
    """A brain with no provenance is a shape; a provenance module that half-read is not."""

    def test_a_brain_without_provenance_lists_by_media_type(self, service: BrainService, source_file: Path) -> None:
        """The documented empty case: a selectively pulled brain can hold canonical evidence and no provenance."""
        service.register(source_file, media_type="text/markdown")
        rows = service.blocks("canonical")["rows"]
        assert rows, "a brain with canonical evidence must still list"

    def test_one_unreadable_record_does_not_cost_the_others_their_origin(
        self, service: BrainService, source_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`suppress` wrapped the whole walk, so a store error partway through returned a partial map that looked
        exactly like the documented empty one -- some rows with an origin, the rest without, and nothing saying
        which had been skipped."""
        from boltzmann.blocks.memory_type import MemoryType

        from vitruvio.runtime.assembly import Capability

        service.register(source_file, media_type="text/markdown")
        second = source_file.parent / "laplace.md"
        second.write_text("# Laplace\n\nDe lo diferencial a lo algebraico.\n", encoding="utf-8")
        service.register(second, media_type="text/markdown")

        brain = service.brain(Capability.INSPECT)
        module = brain.module(MemoryType.PROVENANCE)
        original = module.get
        seen: list[Any] = []

        def flaky(identity: Any) -> Any:
            seen.append(identity)
            if len(seen) == 1:
                raise RuntimeError("this one blob will not read")
            return original(identity)

        monkeypatch.setattr(module, "get", flaky)
        monkeypatch.setattr(
            brain, "module", lambda kind: module if kind is MemoryType.PROVENANCE else brain.module(kind)
        )

        origins = service.browsing_ops._origins(brain)
        assert len(seen) > 1, "the walk stopped at the first unreadable record instead of continuing"
        assert origins, "every record after the unreadable one lost its origin too"


class TestPagingReadsOnlyThePage:
    """`blocks` resolved every identity in the module and then threw away all but one page of rows."""

    def _fill(self, service: BrainService, source_file: Path, count: int) -> None:
        for index in range(count):
            path = source_file.parent / f"nota-{index}.md"
            path.write_text(f"# Nota {index}\n\nContenido {index}.\n", encoding="utf-8")
            service.register(path, media_type="text/markdown")

    def test_an_unfiltered_page_reads_only_its_own_rows(
        self, service: BrainService, source_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a filter every row matches, so `matched` is the module's own count and the rest is waste.

        Counted rather than timed: on a module of fifty thousand blocks this was fifty thousand store reads to
        return a hundred rows, and a timing assertion would pass on a fast machine.
        """
        from boltzmann.blocks.memory_type import MemoryType

        from vitruvio.runtime.assembly import Capability

        self._fill(service, source_file, 12)
        brain = service.brain(Capability.INSPECT)
        module = brain.module(MemoryType.CANONICAL)
        original = module.get
        reads: list[Any] = []

        def counted(identity: Any) -> Any:
            reads.append(identity)
            return original(identity)

        # Only the canonical module's `get` is counted. `brain.module` caches, so patching the instance is enough --
        # and patching `brain.module` itself made `_origins` walk canonical instead of provenance, which is how the
        # first version of this test measured 15 reads and blamed the code.
        monkeypatch.setattr(module, "get", counted)

        result = service.browsing_ops.blocks("canonical", limit=3)
        assert len(result["rows"]) == 3
        assert result["matched"] == result["block_count"], "matched is the module's count when nothing is filtered"
        assert len(reads) == 3, f"read {len(reads)} blocks to return 3 rows"

    def test_a_filtered_page_still_reports_the_whole_match_count(
        self, service: BrainService, source_file: Path
    ) -> None:
        """The scan is the answer here, not waste: `matched` is how many rows match in the module, which a page
        cannot tell you. That cost is what the docstring means by "a filter is not a query"."""
        self._fill(service, source_file, 12)
        result = service.browsing_ops.blocks("canonical", limit=2, contains="Nota")
        assert len(result["rows"]) == 2
        assert result["matched"] == 12
        assert result["truncated"] is True
