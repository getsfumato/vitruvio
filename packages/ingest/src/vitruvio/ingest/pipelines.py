"""Normalization pipelines: observed bytes to a deterministic view.

A normalized view is **evidence**, so it is content-addressed, and that puts one requirement above every other:
given the same original and the same pipeline at the same version, any client must produce the same bytes. Not
similar bytes. The same ones, or the view's digest differs between clients and stops being citable.

Everything in this module is shaped by that requirement, and it rules out most of the obvious implementations:

* **No HTML library.** ``BeautifulSoup`` and ``lxml`` change their whitespace and entity handling between releases,
  which would silently change a view's digest on a dependency bump. The extractor here is ``html.parser`` from the
  standard library with explicit whitespace rules -- versioned with Python, and versioned *in the pipeline id*.
* **No prose reflowing, no smart quotes, no language-aware anything.** Every transform is a pure function of the
  bytes, and the transforms that are not (a model summarising, a spell-checker) are proposals for semantic memory,
  not views of canonical evidence. That line is the whole point of Section 7.1 and it is not blurry.
* **Version in the name, never implicit.** A pipeline's ``version`` is recorded in provenance. Changing behaviour
  without changing the version is how two clients come to disagree about what a digest means, so the version is
  bumped by the same edit that changes behaviour, and the tests assert on the digest of a fixed input.

Raster images deliberately have no pipeline. A "normalized" PNG would be a re-encode, and re-encoding is not
reproducible across Pillow or libpng versions -- the one thing a view cannot be. Vision embeddings read the
original blob instead, which is the right arrangement anyway: the bytes that were observed are the evidence. SVG is
the exception, and only because it is text: its labels are the signal, and extracting them is a string operation.
"""

from __future__ import annotations

import json
import re
import unicodedata
from html.parser import HTMLParser
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from collections.abc import Iterator

TEXT_MEDIA_TYPE = "text/plain; charset=utf-8"
"""What every text-producing pipeline emits. Named once so the views are interchangeable to a reader."""

_TRAILING = re.compile(r"[ \t]+$", re.MULTILINE)
_BLANK_RUN = re.compile(r"\n{3,}")


def fold(text: str) -> str:
    """
    The shared tail of every text pipeline: NFC, LF line endings, no trailing spaces, at most one blank line.

    Deliberately *not* case folding and not NFKC. A normalized view is evidence a human may read and a citation may
    point into, so it keeps the original's casing and its distinguishable characters. The aggressive folding belongs
    in the analyzer, where it affects matching rather than the artifact.

    Args:
        text (str): Decoded text.

    Returns:
        str: The normalized form.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING.sub("", text)
    text = _BLANK_RUN.sub("\n\n", text)
    # Newlines only, never `strip()`. A plain strip also removes the *indentation of the first line*, and in Markdown
    # four leading spaces is a code block -- so folding would silently turn a code sample into a paragraph. Trailing
    # spaces are already gone line by line, so there is nothing else left to strip.
    return text.strip("\n") + "\n"


def decode(data: bytes) -> str:
    """
    Decode bytes as UTF-8, replacing what cannot be decoded.

    Replacement rather than an exception, and this is a real decision: a source that is 99% clean UTF-8 with one
    bad byte is still evidence worth citing, and refusing it would mean the brain cannot hold the document at all.
    Replacement is deterministic, which is the property that actually matters here -- every client replaces the same
    byte with the same character.

    Args:
        data (bytes): The original bytes.

    Returns:
        str: The decoded text.
    """
    return data.decode("utf-8", errors="replace")


class _TextPipeline:
    """Shared plumbing: a name, a version, an accepted-media-type set, and the text media type out."""

    NAME: ClassVar[str] = ""
    VERSION: ClassVar[str] = ""
    MEDIA_TYPES: ClassVar[frozenset[str]] = frozenset()
    SUFFIXES: ClassVar[frozenset[str]] = frozenset()

    @property
    def name(self) -> str:
        """The registered name, recorded in provenance."""
        return self.NAME

    @property
    def version(self) -> str:
        """The version, recorded in provenance. Bumped by whatever edit changes the output."""
        return self.VERSION

    @property
    def output_media_type(self) -> str:
        """Always UTF-8 text: a view exists to be read."""
        return TEXT_MEDIA_TYPE

    def accepts(self, media_type: str) -> bool:
        """
        Whether this pipeline applies to a media type.

        Compared on the type alone, with parameters stripped: ``text/plain; charset=utf-8`` and ``text/plain`` are
        the same input. Suffix matching covers the ``+xml`` and ``+json`` structured-syntax families.

        Args:
            media_type (str): The original's media type.

        Returns:
            bool: Whether the pipeline applies.
        """
        base = media_type.split(";", 1)[0].strip().lower()
        return base in self.MEDIA_TYPES or any(base.endswith(suffix) for suffix in self.SUFFIXES)

    def normalize(self, data: bytes) -> bytes:
        """
        Produce the normalized view.

        Args:
            data (bytes): The original bytes.

        Returns:
            bytes: UTF-8 text.
        """
        return fold(self.extract(decode(data))).encode("utf-8")

    def extract(self, text: str) -> str:
        """
        The per-format part. Overridden; the base is the identity.

        Args:
            text (str): Decoded text.

        Returns:
            str: Text to fold.
        """
        return text

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.NAME}/{self.VERSION}>"


class TextPipeline(_TextPipeline):
    """Plain text: decode, fold, done.

    Not a no-op, and not pointless. It fixes line endings and trailing whitespace, which is what makes a citation
    into a line range mean the same thing on every machine that pulled the brain.
    """

    NAME = "text"
    VERSION = "1"
    MEDIA_TYPES = frozenset({"text/plain", "text/csv", "text/tab-separated-values"})


class MarkdownPipeline(_TextPipeline):
    """Markdown to text, keeping the structure that a locator points at.

    Headings keep their ``#`` markers and fenced code blocks keep their fences. That looks like *not* normalising,
    and it is the point: the heading level is how a proposer finds section boundaries, and a code fence is how it
    knows not to read a snippet as prose. What gets removed is only what carries no information for a reader --
    trailing whitespace, redundant blank lines, and the invisible characters that survive a copy-paste.
    """

    NAME = "markdown"
    VERSION = "1"
    MEDIA_TYPES = frozenset({"text/markdown", "text/x-markdown"})

    _INVISIBLE = re.compile("[\\u200b-\\u200f\\u202a-\\u202e\\ufeff]")
    """Zero-width and bidi-override characters, written as escapes so this line is reviewable.

    They survive a copy-paste from a browser or a PDF, carry no information for a reader, and would otherwise make
    two visually identical documents produce two different digests."""

    def extract(self, text: str) -> str:
        """
        Strip invisible characters and normalise indentation to spaces.

        Args:
            text (str): Decoded Markdown.

        Returns:
            str: The cleaned Markdown.
        """
        text = self._INVISIBLE.sub("", text)
        return "\n".join(line.replace("\t", "    ") for line in text.split("\n"))


class _Extractor(HTMLParser):
    """Collects text, dropping script and style, and keeping block boundaries as newlines."""

    SKIP: ClassVar[frozenset[str]] = frozenset({"script", "style", "noscript", "template"})
    BLOCK: ClassVar[frozenset[str]] = frozenset(
        {
            "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "fieldset", "figcaption",
            "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav",
            "ol", "p", "pre", "section", "table", "td", "th", "tr", "ul",
        }
    )  # fmt: skip

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skipping += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        # The alt text of an image is often the only description of a figure, and a brain that drops it cannot find
        # the figure at all.
        if tag == "img":
            alt = next((value for name, value in attrs if name == "alt" and value), None)
            if alt:
                self.parts.append(f"\n{alt}\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skipping:
            self._skipping -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


class HtmlPipeline(_TextPipeline):
    """HTML to text with ``html.parser`` from the standard library.

    The library choice is the decision. ``BeautifulSoup`` and ``lxml`` are better parsers and both change their
    whitespace and entity handling between releases, which would move a view's digest on a dependency bump -- the
    exact failure a content-addressed view cannot tolerate. ``html.parser`` moves with Python, and the Python
    version that produced a view is recoverable from provenance.
    """

    NAME = "html-text"
    VERSION = "1"
    MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})

    def extract(self, text: str) -> str:
        """
        Extract the text content.

        Args:
            text (str): Decoded HTML.

        Returns:
            str: The text, with block elements separated by newlines.
        """
        parser = _Extractor()
        parser.feed(text)
        parser.close()
        collapsed = "".join(parser.parts)
        # Intra-line whitespace collapses; newlines are load-bearing, because they are what `fold` turns into
        # paragraph boundaries and what a line-range locator counts.
        return "\n".join(" ".join(line.split()) for line in collapsed.split("\n"))


class SvgTextPipeline(_TextPipeline):
    """The text inside an SVG.

    Present for a reason that generalises: a diagram's labels are usually the only searchable description of what it
    shows, and an SVG is text, so extracting them is a string operation rather than a model call. This is why SVG
    gets a pipeline and PNG does not.
    """

    NAME = "svg-text"
    VERSION = "1"
    MEDIA_TYPES = frozenset({"image/svg+xml"})

    _TAG = re.compile(r"<[^>]+>")
    _TITLE = re.compile(r"<(title|desc|text|tspan)\b[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE)

    def extract(self, text: str) -> str:
        """
        Collect title, desc and text nodes, in document order.

        Args:
            text (str): The SVG source.

        Returns:
            str: One label per line.
        """
        labels = (" ".join(self._TAG.sub(" ", match.group(2)).split()) for match in self._TITLE.finditer(text))
        return "\n".join(label for label in labels if label)


class JsonPipeline(_TextPipeline):
    """Canonical JSON: sorted keys, fixed separators, UTF-8 preserved.

    A view of a JSON document that reorders its keys is what makes two exports of the same record produce one
    digest instead of two -- which is the difference between citing a record and citing a serialisation of it.
    Invalid JSON is passed through as text rather than rejected: the bytes are still evidence, and a pipeline is
    not a validator.
    """

    NAME = "json-canonical"
    VERSION = "1"
    MEDIA_TYPES = frozenset({"application/json"})
    SUFFIXES = frozenset({"+json"})

    def extract(self, text: str) -> str:
        """
        Re-serialise with sorted keys.

        Args:
            text (str): The decoded document.

        Returns:
            str: Canonical JSON, or the input unchanged when it does not parse.
        """
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text
        return json.dumps(parsed, sort_keys=True, ensure_ascii=False, indent=2, separators=(",", ": "))


class PdfTextPipeline:
    """Text from a PDF, via ``pypdfium2``.

    Behind the ``[vision]`` extra rather than a hard dependency, and unavailable is reported rather than raised at
    import: a brain that never registers a PDF should not need a rendering library installed.

    ``pypdfium2`` over ``pdfminer`` and ``PyPDF2`` for one reason that matters here -- it wraps PDFium, which is a
    single pinned native library, so its extraction is stable across versions of *Python*. A pure-Python extractor
    whose layout heuristics drift between releases would move every view's digest with it.

    Page boundaries are marked explicitly, because a citation into a PDF that cannot name a page is not much of a
    citation.
    """

    NAME = "pdf-text"
    VERSION = "1"
    MAX_PAGES = 2048
    """A guard, not a policy. A malformed PDF claiming millions of pages should fail fast rather than swap."""

    @property
    def name(self) -> str:
        """The registered name."""
        return self.NAME

    @property
    def version(self) -> str:
        """The version, including the PDFium build, because that is what decides the extraction."""
        return f"{self.VERSION}+pdfium{self._pdfium_version()}"

    @property
    def output_media_type(self) -> str:
        """UTF-8 text."""
        return TEXT_MEDIA_TYPE

    @property
    def available(self) -> bool:
        """Whether ``pypdfium2`` is importable. False means the extra is not installed, which is not an error."""
        try:
            import pypdfium2  # noqa: F401
        except ImportError:
            return False
        return True

    def accepts(self, media_type: str) -> bool:
        """
        Whether this is a PDF.

        Args:
            media_type (str): The original's media type.

        Returns:
            bool: Whether the pipeline applies.
        """
        return media_type.split(";", 1)[0].strip().lower() == "application/pdf"

    def normalize(self, data: bytes) -> bytes:
        """
        Extract the text, one marked section per page.

        Args:
            data (bytes): The PDF.

        Returns:
            bytes: UTF-8 text.

        Raises:
            RuntimeError: If ``pypdfium2`` is not installed. Raised rather than returning empty bytes: a view that
                silently contains nothing is indistinguishable from a PDF that contains no text, and the two need
                different responses from whoever is watching.
        """
        try:
            import pypdfium2
        except ImportError as error:  # pragma: no cover - depends on the installed extras
            raise RuntimeError(
                "the pdf-text pipeline needs pypdfium2; install the [vision] extra, or register the PDF without "
                "--normalize-with and let a proposer read the original"
            ) from error

        document = pypdfium2.PdfDocument(data)
        try:
            return fold("\n\n".join(self._pages(document))).encode("utf-8")
        finally:
            document.close()

    def _pages(self, document: object) -> Iterator[str]:
        """Yield one marked section per page."""
        count = min(len(document), self.MAX_PAGES)  # type: ignore[arg-type]
        for number in range(count):
            page = document[number]  # type: ignore[index]
            try:
                text = page.get_textpage().get_text_range()
            finally:
                page.close()
            yield f"[page {number + 1}]\n{text.strip()}"

    @staticmethod
    def _pdfium_version() -> str:
        """The PDFium build, or ``unavailable``. Part of the version because it decides the output."""
        try:
            import pypdfium2

            return str(getattr(pypdfium2, "V_PDFIUM", "unknown"))
        except ImportError:
            return "unavailable"

    def __repr__(self) -> str:
        return f"<PdfTextPipeline {self.NAME}/{self.VERSION} available={self.available}>"


BUILTIN: tuple[object, ...] = (
    TextPipeline(),
    MarkdownPipeline(),
    HtmlPipeline(),
    SvgTextPipeline(),
    JsonPipeline(),
    PdfTextPipeline(),
)
"""Every pipeline vitruvio ships, as singletons.

Singletons because the SDK's registry refuses a second, *different* pipeline under an existing name -- a name and
version must identify exactly one transform -- and re-registering the same instance has to stay idempotent so that
opening two brains in one process is not an error.
"""


def bootstrap() -> dict[str, str]:
    """
    Register every built-in pipeline with the SDK.

    Idempotent, and called from the runtime's assembly rather than at import: registering as a side effect of an
    import means the set of available pipelines depends on which modules happened to be loaded, which is a
    reproducibility hazard in exactly the same way a nondeterministic transform is.

    Returns:
        dict[str, str]: Pipeline name to ``name/version``, for a caller that wants to report what it installed.
    """
    from boltzmann.ingest.pipelines import register_pipeline

    installed: dict[str, str] = {}
    for pipeline in BUILTIN:
        register_pipeline(pipeline)  # type: ignore[arg-type]
        installed[pipeline.name] = f"{pipeline.name}/{pipeline.version}"  # type: ignore[attr-defined]
    return installed


def describe() -> list[dict[str, object]]:
    """
    What each built-in pipeline is, for ``vitruvio ingest pipelines``.

    Returns:
        list[dict[str, object]]: One record per pipeline, ordered by name.
    """
    records = [
        {
            "name": pipeline.name,  # type: ignore[attr-defined]
            "version": pipeline.version,  # type: ignore[attr-defined]
            "output_media_type": pipeline.output_media_type,  # type: ignore[attr-defined]
            "available": bool(getattr(pipeline, "available", True)),
            "accepts": sorted(
                getattr(pipeline, "MEDIA_TYPES", frozenset()) or {"application/pdf"},
            ),
        }
        for pipeline in BUILTIN
    ]
    return sorted(records, key=lambda record: str(record["name"]))


def suggest(media_type: str) -> str | None:
    """
    The pipeline that would normalise this media type, if any.

    Args:
        media_type (str): The original's media type.

    Returns:
        str | None: A pipeline name, or ``None`` when no view applies -- which is the correct answer for a raster
        image, not a gap.
    """
    for pipeline in BUILTIN:
        if pipeline.accepts(media_type) and bool(getattr(pipeline, "available", True)):  # type: ignore[attr-defined]
            return str(pipeline.name)  # type: ignore[attr-defined]
    return None
