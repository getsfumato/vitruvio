"""Evidence on its way in: what is accepted, what is refused, and what is recorded about it.

The refusals are the interesting half. Three of the four have never applied to a manual registration -- a declared
source has refused symlinks, escapes and FIFOs since ADR-0011, while `source register` checked only that the file
existed -- which is the wrong way round, because the path with a symlink in it is the one a person types.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from vitruvio.ingest.evidence import Evidence, contain
from vitruvio.kernel import EvidenceRefusedError


class TestInMemory:
    def test_evidence_can_be_built_with_no_file_anywhere(self) -> None:
        evidence = Evidence.from_bytes(b"# Fourier\n", origin="https://example.org/fourier", media_type="text/markdown")

        assert evidence.data == b"# Fourier\n"
        assert evidence.origin == "https://example.org/fourier"

    def test_a_filename_is_enough_to_name_the_bytes(self) -> None:
        """A caller elsewhere often knows what its upload was called and nothing else about it."""
        evidence = Evidence.from_bytes(b"x", origin="upload://1", filename="apuntes.md")

        assert evidence.media_type == "text/markdown"

    def test_bytes_with_no_name_at_all_are_octet_stream(self) -> None:
        assert Evidence.from_bytes(b"x", origin="upload://1").media_type == "application/octet-stream"

    def test_a_declared_type_beats_the_filename(self) -> None:
        evidence = Evidence.from_bytes(b"x", origin="upload://1", filename="a.md", media_type="text/plain")

        assert evidence.media_type == "text/plain"


class TestTheRefusals:
    def test_a_symlink_is_refused(self, tmp_path: Path) -> None:
        real = tmp_path / "real.md"
        real.write_text("x", encoding="utf-8")
        link = tmp_path / "link.md"
        link.symlink_to(real)

        with pytest.raises(EvidenceRefusedError, match="symlink"):
            Evidence.from_path(link)

    def test_a_path_outside_the_declared_root_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "inside"
        root.mkdir()
        outside = tmp_path / "secret.pem"
        outside.write_text("-----BEGIN PRIVATE KEY-----", encoding="utf-8")

        with pytest.raises(EvidenceRefusedError, match="outside"):
            Evidence.from_path(outside, root=root)

    def test_something_that_is_not_a_regular_file_is_refused(self, tmp_path: Path) -> None:
        """`read_bytes()` on a FIFO blocks forever, which is a hang rather than an error."""
        fifo = tmp_path / "pipe"
        os.mkfifo(fifo)

        with pytest.raises(EvidenceRefusedError, match="not a regular file"):
            Evidence.from_path(fifo)

    def test_a_missing_file_is_refused_before_anything_is_opened(self, tmp_path: Path) -> None:
        with pytest.raises(EvidenceRefusedError, match="does not exist"):
            Evidence.from_path(tmp_path / "absent.md")


class TestTheCeiling:
    def test_a_file_over_the_ceiling_is_refused_before_it_is_read(self, tmp_path: Path) -> None:
        """Checked against `stat()` rather than `len()`: the difference between a refusal and an OOM kill."""
        big = tmp_path / "big.md"
        big.write_bytes(b"x" * 100)

        with pytest.raises(EvidenceRefusedError, match="over the declared max_bytes"):
            Evidence.from_path(big, max_bytes=10)

    def test_bytes_over_the_ceiling_are_refused_too(self) -> None:
        """Otherwise a caller holding bytes could exceed what a caller holding a file may not."""
        with pytest.raises(EvidenceRefusedError, match="over the declared max_bytes"):
            Evidence.from_bytes(b"x" * 100, origin="upload://1", max_bytes=10)

    def test_bounded_is_where_the_runtime_checks_the_same_thing(self) -> None:
        evidence = Evidence.from_bytes(b"x" * 100, origin="upload://1")

        assert evidence.bounded(None) is evidence
        assert evidence.bounded(100) is evidence
        with pytest.raises(EvidenceRefusedError, match="over the declared max_bytes"):
            evidence.bounded(99)


class TestFromAPath:
    def test_the_media_type_comes_from_the_name_when_it_is_not_declared(self, tmp_path: Path) -> None:
        notes = tmp_path / "apuntes.md"
        notes.write_text("# Fourier", encoding="utf-8")

        assert Evidence.from_path(notes).media_type == "text/markdown"

    def test_the_origin_defaults_to_the_path_a_person_named(self, tmp_path: Path) -> None:
        notes = tmp_path / "apuntes.md"
        notes.write_text("# Fourier", encoding="utf-8")

        assert Evidence.from_path(notes).origin == str(notes.resolve())

    def test_contain_returns_the_resolved_path(self, tmp_path: Path) -> None:
        notes = tmp_path / "apuntes.md"
        notes.write_text("# Fourier", encoding="utf-8")

        assert contain(notes) == notes.resolve()
