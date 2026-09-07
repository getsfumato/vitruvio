"""Drawing content in a terminal: text, images, PDF pages, and the honest refusal for everything else.

A brain's canonical module holds whatever was registered -- lecture notes, a scanned PDF, a photograph of a
blackboard, a recording. The point of browsing one is to *see* that, so this module answers "what can a
terminal show of these bytes" in one place, for the TUI and for the CLI alike.

Three decisions worth stating.

**Route on the media type the block carries, not on sniffed bytes.** The block says what it is; that claim is
part of its identity and part of what was verified. Guessing from a magic number would mean a preview that
disagrees with the evidence, which is the one thing a viewer of a verifiable store must not do.

**An image is drawn with half-blocks, and never with a protocol.** Two rows of pixels per character cell, using
the upper-half-block glyph with a foreground and background colour, works in every terminal and inside a
Textual widget. Sixel and the iTerm2 and Kitty image protocols each look better in the one terminal that
implements them and print garbage in the rest, and a viewer that shows garbage half the time is not a viewer.

**Absence is reported, not worked around.** Pillow renders the images and pypdfium2 rasterizes the pages, both
behind ``vitruvio[vision]``. Without them, a preview says which extra would draw it and shows the metadata --
that is a smaller answer, not a broken one. Silently falling back to "no preview available" would leave a
reader thinking the brain held nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rich.console import RenderableType
from rich.text import Text

from vitruvio.cli.render import theme

TEXTUAL_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
    "application/toml",
    "application/javascript",
)
"""Media types whose bytes are meant to be read as characters."""

MARKDOWN_TYPES = ("text/markdown", "text/x-markdown")
"""Media types rendered as Markdown rather than shown as source."""

CODE_LEXERS = {
    "application/json": "json",
    "application/xml": "xml",
    "text/xml": "xml",
    "application/x-yaml": "yaml",
    "application/yaml": "yaml",
    "application/toml": "toml",
    "text/csv": "text",
    "text/html": "html",
    "text/x-python": "python",
    "application/javascript": "javascript",
}
"""Media type to Pygments lexer, for the types worth highlighting."""

MAX_TEXT_BYTES = 512 * 1024
"""How much of a text blob to decode. A registered corpus can be a hundred megabytes, and a preview that tries
to lay all of it out stops being a preview. The cut is reported rather than silent."""

PREVIEW_TEXT_BYTES = 64 * 1024
"""How much of a text blob the browser's preview pane shows.

Smaller than :data:`MAX_TEXT_BYTES` because the pane lays the text out on the event loop -- once per paint and
again on every resize -- where the CLI lays it out once into a pipe. Sixty-four kibibytes is a few hundred
screens of reading; what the pane does not show, ``o`` and ``e`` hand to something that can.
"""

PREVIEW_TEXT_LINES = 2_000
"""The most lines the preview pane shows. A byte cap alone lets a file of one-character lines produce sixty
thousand strips, and Textual builds one per wrapped line."""

TEXT_FIRST_TYPES = ("text/html", "application/xhtml+xml")
"""Media types whose extracted text is what a reader wants first. Markup is a wrapper around its text, so the
browser opens such a block on its normalized view when it has one, and ``t`` shows the source."""


def is_text(media_type: str) -> bool:
    """
    Whether these bytes are characters.

    Args:
        media_type (str): The block's media type.

    Returns:
        bool: Whether to decode rather than rasterize.
    """
    return media_type.startswith(TEXTUAL_TYPES)


def is_image(media_type: str) -> bool:
    """Whether these bytes are a raster image Pillow can open."""
    return media_type.startswith("image/")


def is_pdf(media_type: str) -> bool:
    """Whether these bytes are a PDF, whose pages have to be rasterized before they can be drawn."""
    return media_type == "application/pdf"


def is_playable(media_type: str) -> bool:
    """Whether these bytes are audio or video, which a terminal cannot show and a player can."""
    return media_type.startswith(("video/", "audio/"))


def prefers_text(media_type: str) -> bool:
    """
    Whether a block of this type should open on its normalized text rather than on its bytes.

    Args:
        media_type (str): The block's media type, with or without parameters.

    Returns:
        bool: ``True`` for markup, whose source is the wrapper and whose text is the content.
    """
    return media_type.split(";", 1)[0].strip().lower() in TEXT_FIRST_TYPES


def clip(data: bytes, *, limit: int, max_lines: int | None = None) -> tuple[str, int]:
    """
    The head of a text blob, decoded, with how much of it was kept.

    Args:
        data (bytes): The content.
        limit (int): The most bytes to keep.
        max_lines (int | None): The most lines to keep, or ``None`` for no line cap. The cut falls between lines,
            never inside one.

    Returns:
        tuple[str, int]: The decoded head and how many bytes it came from, so a caller can say what was cut.
        Undecodable bytes are replaced rather than raised on: a mislabelled blob is still worth looking at, and
        the alternative is a preview that fails where a hex dump would have told the reader what happened.
    """
    head = data[:limit]
    if max_lines is not None:
        parts = head.split(b"\n", max_lines)
        # More parts than lines means a remainder past the cap. A remainder that is empty is a trailing newline
        # on exactly `max_lines` lines, and cutting it would report a cut that removed nothing.
        if len(parts) > max_lines and parts[-1]:
            head = b"\n".join(parts[:max_lines])
    return head.decode("utf-8", errors="replace"), len(head)


def text(
    data: bytes,
    media_type: str,
    *,
    limit: int = MAX_TEXT_BYTES,
    max_lines: int | None = None,
    highlight: bool = True,
    hint: str | None = None,
) -> RenderableType:
    """
    Render bytes that are characters.

    Markdown is rendered as Markdown, the types with a lexer are highlighted while that is affordable, and
    everything else is shown as it is. Whatever the caps cut is reported under the text rather than silently
    absent, because "the file ends here" and "the preview ends here" are different facts.

    Takes no width: wrapping is the console's decision, and a renderable that wrapped itself would wrap to the
    wrong width the moment it was printed into a TUI pane rather than into a terminal.

    Args:
        data (bytes): The content.
        media_type (str): What the block says it is.
        limit (int): The most bytes to show. The CLI keeps :data:`MAX_TEXT_BYTES`; the browser passes
            :data:`PREVIEW_TEXT_BYTES`, because its pane lays the text out on the event loop.
        max_lines (int | None): The most lines to show, or ``None`` for no line cap.
        highlight (bool): Whether the types with a lexer may be highlighted. Even then the highlight stops past
            :data:`~vitruvio.cli.render.theme.HIGHLIGHT_BYTES`; see :func:`~vitruvio.cli.render.theme.code`.
        hint (str | None): What to say under the text -- the keys that act on it, or the flag that fetches the
            rest. Joined to the cut report when there is one, shown alone when there is not.

    Returns:
        RenderableType: The text alone when all of it is shown and there is no hint; otherwise the text over a
        muted footer saying what was cut and what to do about it.
    """
    from rich.console import Group
    from rich.markdown import Markdown

    body, shown = clip(data, limit=limit, max_lines=max_lines)

    renderable: RenderableType
    if media_type in MARKDOWN_TYPES:
        renderable = Markdown(body, hyperlinks=False)
    elif lexer := CODE_LEXERS.get(media_type):
        renderable = theme.code(body, lexer, highlight=highlight)
    else:
        renderable = Text(body)

    footer: str | None = None
    if shown < len(data):
        footer = f"showing {theme.filesize(shown)} of {theme.filesize(len(data))}"
        if hint:
            footer += f" -- {hint}"
    elif hint:
        footer = hint
    if footer is None:
        return renderable
    return Group(renderable, "", Text(footer, style="muted"))


def image(data: bytes, *, width: int = 80, height: int | None = None) -> RenderableType:
    """
    Draw a raster image with half-block glyphs.

    Each character cell carries two pixels: the upper one as the foreground of ``▀`` and the lower one as the
    background. The aspect ratio therefore needs no correction -- a cell is about twice as tall as it is wide,
    and it holds two pixels stacked -- which is why this looks like the picture rather than a stretched one.

    Args:
        data (bytes): The image bytes.
        width (int): Cells to draw across. The image is scaled to fit and never upscaled past its own width.
        height (int | None): A cap on cells down, for a pane that must not scroll.

    Returns:
        RenderableType: The drawing, a note naming the extra that would draw it, or -- when the bytes are not an
        image at all -- a note saying so. That last case is not hypothetical: a block's media type is a *claim*
        about its bytes, and a preview that let the decoder's exception escape would take the whole interface down
        over one mislabelled blob. Reporting the disagreement is the correct outcome, because the disagreement is
        the finding.
    """
    try:
        from PIL import Image
    except ImportError:
        return _absent("image/*", "vitruvio[vision]")

    import io

    try:
        opened = Image.open(io.BytesIO(data))
    except Exception as error:
        return _undecodable("an image", error)

    with opened:
        picture = opened.convert("RGB")
        columns = max(1, min(width, picture.width))
        rows = max(2, round(picture.height * columns / picture.width))
        if height is not None:
            rows = min(rows, max(2, height * 2))
        rows += rows % 2  # two pixel rows per cell, so an odd height would lose the bottom row
        picture = picture.resize((columns, rows), Image.Resampling.LANCZOS)
        # Raw bytes, three per pixel, row-major -- because the image was converted to RGB above, that is exactly
        # what `tobytes` returns. The two obvious alternatives are worse in ways that only show up later:
        # `load()` is typed as an optional accessor over `float | tuple[int, ...]`, so every read needs narrowing
        # that tells a reader nothing; and `getdata()` is deprecated in Pillow 12 and gone in 14, which this
        # suite turns into a test failure rather than a warning nobody reads.
        band = picture.tobytes()

        def pixel(column: int, row: int) -> tuple[int, int, int]:
            offset = (row * columns + column) * 3
            return band[offset], band[offset + 1], band[offset + 2]

        drawing = Text(no_wrap=True, end="")
        for row in range(0, rows, 2):
            for column in range(columns):
                top = pixel(column, row)
                bottom = pixel(column, row + 1) if row + 1 < rows else top
                drawing.append(
                    "▀", style=f"rgb({top[0]},{top[1]},{top[2]}) on rgb({bottom[0]},{bottom[1]},{bottom[2]})"
                )
            drawing.append("\n")
        return drawing


def pdf_page(data: bytes, *, page: int = 0, width: int = 80, scale: float = 2.0) -> RenderableType:
    """
    Rasterize one page of a PDF and draw it.

    Args:
        data (bytes): The PDF bytes.
        page (int): Which page, zero-based.
        width (int): Cells to draw across.
        scale (float): Rasterization scale before downsampling. Above 1.0 so that the downsample averages real
            detail rather than sampling a page rendered at exactly the size of the drawing.

    Returns:
        RenderableType: The page, a note naming the extra that would draw it, or a note that these bytes are not a
        PDF. Bytes that do not parse are reported rather than raised on, for the reason :func:`image` gives.
    """
    try:
        import pypdfium2
    except ImportError:
        return _absent("application/pdf", "vitruvio[vision]")

    import io

    try:
        document = pypdfium2.PdfDocument(io.BytesIO(data))
    except Exception as error:
        return _undecodable("a PDF", error)

    try:
        index = max(0, min(page, len(document) - 1))
        rendered = document[index].render(scale=scale).to_pil()
        buffer = io.BytesIO()
        rendered.save(buffer, format="PNG")
        return image(buffer.getvalue(), width=width)
    except Exception as error:
        return _undecodable("a PDF", error)
    finally:
        document.close()


def pdf_pages(data: bytes) -> int:
    """
    How many pages a PDF has, or ``0`` when it cannot be opened.

    Args:
        data (bytes): The PDF bytes.

    Returns:
        int: The page count.
    """
    try:
        import io

        import pypdfium2
    except ImportError:
        return 0
    try:
        document = pypdfium2.PdfDocument(io.BytesIO(data))
    except Exception:
        return 0
    try:
        return len(document)
    finally:
        document.close()


def _undecodable(claimed: str, error: Exception) -> Text:
    """
    The note for bytes that are not what the block says they are.

    A media type is a claim, and this is the claim failing. Worth stating plainly rather than swallowing: the
    block still verifies -- it hashes to its identity -- so what is wrong is the description of the bytes, which
    is a thing about the brain a reader wants to know.

    Args:
        claimed (str): What the block said the bytes were, in prose.
        error (Exception): What the decoder said.

    Returns:
        Text: The note.
    """
    return Text(
        f"these bytes do not decode as {claimed}, which is what the block says they are\n{type(error).__name__}: {error}",
        style="warn",
    )


def _absent(what: str, extra: str) -> Text:
    """
    The note that stands in for a preview this build cannot draw.

    Args:
        what (str): The media type family.
        extra (str): The extra that would supply the renderer.

    Returns:
        Text: The note.
    """
    return Text(f"cannot draw {what} here -- install {extra}", style="warn")


def unsupported(media_type: str, size: int) -> Text:
    """
    The note for bytes a terminal genuinely cannot show.

    Args:
        media_type (str): What the block says it is.
        size (int): How many bytes there are.

    Returns:
        Text: The note, naming what to do instead.
    """
    verb = "play" if is_playable(media_type) else "show"
    return Text(
        f"{size} bytes of {media_type} -- a terminal cannot {verb} this.\n"
        f"Export it with `vitruvio inspect content DIGEST --out FILE` and open it with something that can.",
        style="muted",
    )


def preview(
    data: bytes,
    media_type: str,
    *,
    width: int = 80,
    height: int | None = None,
    page: int = 0,
    limit: int = MAX_TEXT_BYTES,
    max_lines: int | None = None,
    highlight: bool = True,
    hint: str | None = None,
) -> RenderableType:
    """
    Whatever this content can be shown as, chosen by its media type.

    Args:
        data (bytes): The content.
        media_type (str): What the block says it is.
        width (int): Cells across, for the drawings.
        height (int | None): A cap on cells down.
        page (int): Which PDF page.
        limit (int): The most bytes of text to show; see :func:`text`.
        max_lines (int | None): The most lines of text to show; see :func:`text`.
        highlight (bool): Whether text with a lexer may be highlighted; see :func:`text`.
        hint (str | None): What to say under text; see :func:`text`. The drawings take no footer.

    Returns:
        RenderableType: The best available rendering, or a note saying why there is none.
    """
    if is_text(media_type):
        return text(data, media_type, limit=limit, max_lines=max_lines, highlight=highlight, hint=hint)
    if is_image(media_type):
        return image(data, width=width, height=height)
    if is_pdf(media_type):
        return pdf_page(data, page=page, width=width)
    return unsupported(media_type, len(data))


def describe(entry: Mapping[str, Any]) -> RenderableType:
    """
    The metadata block that sits above a preview.

    Args:
        entry (Mapping[str, Any]): A row from ``service.blocks``.

    Returns:
        RenderableType: The label-and-value block.
    """
    pairs: list[tuple[str, Any]] = [
        ("block", theme.digest(entry.get("block_id"), full=True)),
        ("module", theme.kind(entry.get("memory_type"))),
    ]
    for label, field in (
        ("origin", "origin"),
        ("media type", "media_type"),
        ("subject", "subject"),
        ("at", "occurred_at"),
    ):
        if entry.get(field):
            pairs.append((label, str(entry[field])))
    if isinstance(entry.get("size"), int):
        pairs.append(("size", f"{entry['size']} bytes"))
    if blob := entry.get("blob"):
        pairs.append(("content", theme.digest(blob, full=True)))
    if content := entry.get("content"):
        # A derived block's named bytes. The type and size ride along because they are what a reader uses to
        # decide whether `o` is worth pressing, and this row has no flat `media_type`/`size` of its own.
        pairs.append(("content", theme.digest(content.get("blob"), full=True)))
        if content.get("media_type"):
            pairs.append(("media type", str(content["media_type"])))
        if isinstance(content.get("size"), int):
            pairs.append(("size", f"{content['size']} bytes"))
    if view := entry.get("normalized_view"):
        pairs.append(("normalized", theme.digest(view.get("blob"), full=True)))
    if tags := entry.get("tags"):
        pairs.append(("tags", ", ".join(str(tag) for tag in tags)))
    if not entry.get("resolvable", True):
        pairs.append(("state", Text("not resolvable -- redacted under policy, or not installed", style="bad")))
    return theme.fields(pairs)


__all__ = [
    "MAX_TEXT_BYTES",
    "PREVIEW_TEXT_BYTES",
    "PREVIEW_TEXT_LINES",
    "TEXT_FIRST_TYPES",
    "clip",
    "describe",
    "image",
    "is_image",
    "is_pdf",
    "is_playable",
    "is_text",
    "pdf_page",
    "pdf_pages",
    "prefers_text",
    "preview",
    "text",
    "unsupported",
]
