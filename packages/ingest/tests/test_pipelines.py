"""The pipelines, tested on the one property that matters: the same input yields the same bytes.

Every other assertion here is downstream of that. A pipeline's output is content-addressed evidence, so a change in
its bytes is a change in what a citation points at -- which means the interesting tests are not "does it extract
text" but "does it extract *these* bytes, on every machine, at this version".
"""

from __future__ import annotations

import pytest

from vitruvio.ingest import (
    BUILTIN,
    HtmlPipeline,
    JsonPipeline,
    MarkdownPipeline,
    PdfTextPipeline,
    SvgTextPipeline,
    TextPipeline,
    bootstrap,
    describe,
    suggest,
)


class TestDeterminism:
    @pytest.mark.parametrize("pipeline", [TextPipeline(), MarkdownPipeline(), HtmlPipeline(), JsonPipeline()])
    def test_the_same_bytes_come_out_every_time(self, pipeline: object) -> None:
        """The whole requirement. A view whose digest varies between runs is not evidence."""
        data = b'<p>Uno</p>\n<p>Dos</p>\n{"b": 1, "a": 2}\n# Heading\n\nbody\n'
        first = pipeline.normalize(data)  # type: ignore[attr-defined]
        second = pipeline.normalize(data)  # type: ignore[attr-defined]
        assert first == second

    def test_a_pipeline_name_and_version_identify_one_transform(self) -> None:
        """Two pipelines under one name would make a recorded view unreproducible, so names are unique."""
        names = [pipeline.name for pipeline in BUILTIN]  # type: ignore[attr-defined]
        assert len(names) == len(set(names))

    def test_bootstrap_is_idempotent(self) -> None:
        """Opening two brains in one process must not be an error, and the SDK refuses a *different* pipeline."""
        first = bootstrap()
        assert bootstrap() == first
        assert "markdown" in first


class TestFolding:
    def test_line_endings_and_trailing_whitespace_are_normalised(self) -> None:
        """This is what makes a line-range locator mean the same thing on every machine that pulled the brain."""
        assert TextPipeline().normalize(b"a  \r\nb\t\r\n") == b"a\nb\n"

    def test_runs_of_blank_lines_collapse_to_one(self) -> None:
        assert TextPipeline().normalize(b"a\n\n\n\n\nb") == b"a\n\nb\n"

    def test_case_is_preserved(self) -> None:
        """Deliberately not case folding: a view is evidence a human reads and a citation points into. The
        aggressive folding belongs in the analyzer, where it affects matching rather than the artifact."""
        assert TextPipeline().normalize(b"CUIT Art. 3") == b"CUIT Art. 3\n"

    def test_undecodable_bytes_are_replaced_rather_than_refused(self) -> None:
        """A document that is 99% clean UTF-8 with one bad byte is still evidence worth citing."""
        assert TextPipeline().normalize(b"a\xffb") == "a�b\n".encode()


class TestMarkdown:
    def test_headings_and_fences_survive(self) -> None:
        """Structure is what a proposer reads to find section boundaries, so removing it would defeat the point."""
        out = MarkdownPipeline().normalize(b"# Title\n\ntext\n\n```py\ncode\n```\n").decode()
        assert out.startswith("# Title")
        assert "```py" in out

    def test_invisible_characters_are_stripped(self) -> None:
        """They survive a copy-paste, carry nothing for a reader, and would make two identical documents differ."""
        assert MarkdownPipeline().normalize("a\u200bb\ufeff".encode()) == b"ab\n"

    def test_tabs_become_spaces(self) -> None:
        assert MarkdownPipeline().normalize(b"\tindented") == b"    indented\n"

    def test_the_first_line_keeps_its_indentation(self) -> None:
        """Four leading spaces is a Markdown code block, so a plain `strip()` here would turn a code sample into a
        paragraph -- silently, and only when it happened to be the first thing in the file."""
        assert MarkdownPipeline().normalize(b"    code = 1\n\ntext\n") == b"    code = 1\n\ntext\n"


class TestHtml:
    def test_script_and_style_are_dropped(self) -> None:
        html = b"<style>p{color:red}</style><p>Visible</p><script>alert(1)</script>"
        assert HtmlPipeline().normalize(html) == b"Visible\n"

    def test_block_elements_become_line_breaks(self) -> None:
        assert HtmlPipeline().normalize(b"<p>Uno</p><p>Dos</p>") == b"Uno\n\nDos\n"

    def test_image_alt_text_is_kept(self) -> None:
        """Often the only description of a figure. A brain that drops it cannot find the figure at all."""
        out = HtmlPipeline().normalize(b'<p>See</p><img src="x.png" alt="Fourier spectrum">').decode()
        assert "Fourier spectrum" in out

    def test_entities_are_resolved(self) -> None:
        assert HtmlPipeline().normalize(b"<p>a&amp;b</p>") == b"a&b\n"


class TestSvg:
    def test_titles_and_labels_are_collected(self) -> None:
        """A diagram's labels are usually its only searchable description, which is why SVG has a pipeline and PNG
        does not."""
        svg = b'<svg><title>Spectrum</title><text x="1">amplitude</text><rect/></svg>'
        assert SvgTextPipeline().normalize(svg) == b"Spectrum\namplitude\n"


class TestJson:
    def test_keys_are_sorted(self) -> None:
        """Two exports of one record then produce one digest instead of two."""
        first = JsonPipeline().normalize(b'{"b":1,"a":2}')
        second = JsonPipeline().normalize(b'{"a":2,"b":1}')
        assert first == second

    def test_non_ascii_survives(self) -> None:
        assert "señales" in JsonPipeline().normalize('{"k":"señales"}'.encode()).decode()

    def test_invalid_json_passes_through_as_text(self) -> None:
        """The bytes are still evidence, and a pipeline is not a validator."""
        assert JsonPipeline().normalize(b"{not json") == b"{not json\n"


class TestDispatch:
    @pytest.mark.parametrize(
        ("media_type", "expected"),
        [
            ("text/markdown", "markdown"),
            ("text/plain; charset=utf-8", "text"),
            ("text/html", "html-text"),
            ("image/svg+xml", "svg-text"),
            ("application/vnd.api+json", "json-canonical"),
            ("application/pdf", None),
            ("image/png", None),
        ],
    )
    def test_the_suggested_pipeline(self, media_type: str, expected: str | None) -> None:
        """`application/pdf` suggests a view only when the [vision] extra is installed -- so what it suggests is
        derived from that rather than pinned. `image/png` suggests nothing anywhere: a re-encode is not
        reproducible, so a raster image has no view and vision embeddings read the original blob.

        The guard used to read `if expected == "pdf-text"`, which no row ever set, so it never fired: the case was
        pinned to `None` and the suite failed on any machine that *did* have the extra. A conditional expectation
        that cannot fire is the same as no conditional at all.
        """
        if media_type == "application/pdf":
            expected = "pdf-text" if PdfTextPipeline().available else None
        assert suggest(media_type) == expected

    def test_media_type_parameters_are_ignored(self) -> None:
        assert TextPipeline().accepts("text/plain; charset=iso-8859-1")

    def test_describe_reports_an_unavailable_pipeline_rather_than_hiding_it(self) -> None:
        """ "Why did my PDF not get a view" needs an answer that names the install."""
        records = {str(item["name"]): item for item in describe()}
        assert "pdf-text" in records
        assert records["pdf-text"]["available"] is PdfTextPipeline().available


class TestPdf:
    def test_the_version_names_the_pdfium_build(self) -> None:
        """It is what decides the extraction, so it belongs in the identity recorded in provenance."""
        assert "pdfium" in PdfTextPipeline().version

    def test_normalizing_without_the_extra_says_which_extra(self) -> None:
        pipeline = PdfTextPipeline()
        if pipeline.available:  # pragma: no cover - depends on extras
            pytest.skip("the [vision] extra is installed, so there is nothing to refuse")
        with pytest.raises(RuntimeError, match=r"\[vision\]"):
            pipeline.normalize(b"%PDF-1.4")

    def test_a_document_over_the_page_guard_is_refused_rather_than_truncated(self) -> None:
        """What `MAX_PAGES` says it does: fail fast rather than swap. Truncating is neither.

        A view holding the first `MAX_PAGES` pages is indistinguishable from a PDF that genuinely ends there, so a
        citation into the tail resolves to nothing while provenance records the truncation as the document's
        normalized form. Driven through `_pages` with a stand-in document rather than a real PDF, so the guard is
        covered whether or not the [vision] extra is installed.
        """

        class Enormous:
            def __len__(self) -> int:
                return PdfTextPipeline.MAX_PAGES + 1

            def __getitem__(self, index: int) -> object:  # pragma: no cover - the guard must fire first
                raise AssertionError("refused documents must not have their pages read")

        with pytest.raises(RuntimeError, match=rf"caps at {PdfTextPipeline.MAX_PAGES}"):
            list(PdfTextPipeline()._pages(Enormous()))

    def test_a_document_at_the_page_guard_is_still_normalized(self) -> None:
        """The cap is inclusive: exactly `MAX_PAGES` pages is a large document, not a malformed claim."""

        class Page:
            def get_textpage(self) -> Page:
                return self

            def get_text_range(self) -> str:
                return "senos y cosenos"

            def close(self) -> None:
                return None

        class AtTheCap:
            def __len__(self) -> int:
                return PdfTextPipeline.MAX_PAGES

            def __getitem__(self, index: int) -> Page:
                return Page()

        assert next(PdfTextPipeline()._pages(AtTheCap())).startswith("[page 1]")
