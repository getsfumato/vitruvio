"""Reading a brain: what ``blocks``, ``content``, ``export_content`` and ``related`` promise.

These four are the interface ``vitruvio browse`` and ``vitruvio inspect blocks`` are both built on, so what is
pinned here is the *contract*, not the rendering: which field is a row's title, that an unreadable block still
produces a row, that a filter is bounded by the same limit an unfiltered page is, and that content comes back
verified or not at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from vitruvio.kernel import VitruvioError

if TYPE_CHECKING:
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
