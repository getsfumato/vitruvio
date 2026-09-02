"""The house style: one theme, one console factory, and the four shapes every command draws with.

Before this module, every command aligned its own columns with ``f"{name:<12}"`` and drew its own rules out of
hyphens. That works until two commands disagree about the width of the same field, which they did -- ``root``
was eighteen characters in ``brain info`` and twelve in ``inspect roots``, and a reader comparing the two was
comparing two layouts rather than two brains. A shared vocabulary of shapes is what makes the output of forty
commands look like one program.

**Colour is meaning, never decoration.** The palette is small on purpose: a memory type always gets the same
colour, a digest is always dim, a verdict is green or red and nothing else is. That is what makes a red word
worth looking at. The theme is declared once here so that "what colour is canonical" has one answer, including
inside the TUI, which loads the same styles.

**Nothing here is reached in ``--json`` mode**, and nothing here decides *what* to print. The renderers take
data the service produced and return a Rich renderable; the decision to render at all belongs to
:class:`~vitruvio.cli.output.Console`, which is also where ``--no-color`` is honoured. A renderer that wrote to
a console of its own would be a renderer that could print into a JSON stream.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from rich.box import SIMPLE
from rich.console import Console as RichConsole
from rich.console import RenderableType
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from vitruvio.cli.render import brand

MEMORY_STYLES = {
    "canonical": "canonical",
    "episodic": "episodic",
    "semantic": "semantic",
    "procedural": "procedural",
    "provenance": "provenance",
}
"""Memory type to style name. The five modules keep their colours everywhere they appear, in the CLI and in the
TUI, because a reader learns them once."""

THEME = Theme(
    {
        # Structure.
        "label": "dim",
        "value": "default",
        "heading": f"bold {brand.GOLD}",
        "muted": "dim",
        "digest": brand.GOLD_DIM,
        "count": "bold",
        # Verdicts. Three states, and the third one matters: a degraded answer that looks like a clean one is
        # the failure mode the whole output contract exists to prevent.
        "ok": brand.SUCCESS,
        "bad": f"bold {brand.ERROR}",
        "warn": brand.WARNING,
        "info": brand.INFO,
        # The five modules.
        "canonical": brand.GOLD,
        "episodic": brand.ERROR,
        "semantic": brand.INFO,
        "procedural": brand.SUCCESS,
        "provenance": brand.GOLD_DIM,
        # Evidence.
        "score": "bold",
        "flag": brand.WARNING,
        # Rich's own table furniture, toned down so a header never competes with a value.
        "table.header": f"bold {brand.IVORY_DIM}",
        "table.footer": "dim",
    }
)
"""The whole palette. Adding a style here is cheap; adding a colour to a call site is how a palette stops
being one."""


def console(*, color: bool = True, stderr: bool = False, width: int | None = None) -> RichConsole:
    """
    A console with the house theme.

    Args:
        color (bool): Whether colour is permitted. ``--no-color`` and a non-terminal stdout both end here.
        stderr (bool): Write to stderr rather than stdout.
        width (int | None): Force a width. Only tests pass this; leaving it ``None`` lets Rich read the terminal.

    Returns:
        RichConsole: The console.
    """
    return RichConsole(
        theme=THEME,
        stderr=stderr,
        no_color=not color,
        # Rich's automatic highlighting colours numbers, paths and anything that looks like a URL inside plain
        # strings. In a tool whose output is digests, media types and file paths, that means most of a line is
        # coloured for no reason -- and colour that carries no meaning is what makes the colour that does carry
        # meaning invisible. Every style here is applied deliberately.
        highlight=False,
        emoji=False,
        markup=False,
        width=width,
    )


SHORT = 10
"""How many hex characters of a digest to show. Enough to recognise, short enough to scan a column of them."""


def short(digest: str | None) -> str:
    """
    Abbreviate a ``sha256:...`` digest for display.

    Args:
        digest (str | None): The digest, or ``None``.

    Returns:
        str: An abbreviated form, or ``-``.
    """
    if not digest:
        return "-"
    algorithm, _, hexadecimal = digest.partition(":")
    return f"{algorithm}:{hexadecimal[:SHORT]}" if hexadecimal else digest[:SHORT]


def digest(value: str | None, *, full: bool = False) -> Text:
    """
    A digest, styled.

    Args:
        value (str | None): The digest.
        full (bool): Print every character. For a value someone will copy -- a root, a snapshot -- rather than
            one they will only recognise.

    Returns:
        Text: The styled digest.
    """
    return Text(value or "-", style="digest") if full else Text(short(value), style="digest")


def kind(memory_type: str | None) -> Text:
    """
    A memory type in its own colour.

    Args:
        memory_type (str | None): canonical, episodic, semantic, procedural or provenance.

    Returns:
        Text: The styled name.
    """
    name = memory_type or "?"
    return Text(name, style=MEMORY_STYLES.get(name, "value"))


def count(value: Any) -> Text:
    """
    A number a reader came for.

    Args:
        value (Any): The number, or anything with a ``str``.

    Returns:
        Text: The value, weighted.
    """
    return Text(str(value), style="count")


def verdict(ok: bool, *, yes: str = "yes", no: str = "no") -> Text:
    """
    A boolean a reader acts on.

    Args:
        ok (bool): The state.
        yes (str): What to print when true.
        no (str): What to print when false.

    Returns:
        Text: Green or red, and nothing in between.
    """
    return Text(yes, style="ok") if ok else Text(no, style="bad")


def table(*columns: str | tuple[str, str], box: Any = SIMPLE, title: str | None = None) -> Table:
    """
    A table in the house style.

    Args:
        *columns: Column headers. A ``(header, justify)`` pair right- or centre-aligns that column; a bare
            string is left-aligned.
        box (Any): The box style. Defaults to Rich's ``SIMPLE``: a rule under the header and nothing else, so a
            wide table stays readable in a narrow terminal and copies cleanly out of one.
        title (str | None): An optional title above the table.

    Returns:
        Table: The table, with no rows yet.
    """
    built = Table(box=box, title=title, title_justify="left", title_style="heading", pad_edge=False, padding=(0, 1))
    for column in columns:
        header, justify = column if isinstance(column, tuple) else (column, "left")
        built.add_column(header, justify=justify, overflow="fold")  # type: ignore[arg-type]
    return built


def fields(pairs: Mapping[str, Any] | Sequence[tuple[str, Any]], *, title: str | None = None) -> Table:
    """
    The label-and-value block that opens most commands.

    Two columns, no borders, labels dim: ``snapshot  sha256:3ba21fde53``. It exists because that shape was
    hand-aligned in nineteen places with three different label widths.

    Args:
        pairs (Mapping[str, Any] | Sequence[tuple[str, Any]]): Label to value, in the order to print. A value
            that is already a Rich renderable is passed through, so a caller can style one field without
            styling all of them. ``None`` renders as ``-``.
        title (str | None): An optional heading.

    Returns:
        Table: The block.
    """
    built = Table(box=None, show_header=False, title=title, title_justify="left", title_style="heading", pad_edge=False)
    built.add_column(style="label", no_wrap=True)
    built.add_column(style="value", overflow="fold")
    items = pairs.items() if hasattr(pairs, "items") else pairs
    for label, value in items:
        built.add_row(label, value if isinstance(value, Text) else Text(str(value) if value is not None else "-"))
    return built


def empty(message: str) -> Text:
    """
    What a command prints when the honest answer is "nothing".

    Not an error and not silence. "The brain holds nothing matching" is a result, and a tool that printed
    nothing at all would leave a reader unable to tell it from a failure.

    Args:
        message (str): The sentence.

    Returns:
        Text: The styled line.
    """
    return Text(message, style="muted")


def lines(items: Iterable[str], *, style: str = "value") -> Text:
    """
    Several plain lines as one renderable.

    Args:
        items (Iterable[str]): The lines.
        style (str): The style to apply to all of them.

    Returns:
        Text: The joined lines.
    """
    return Text("\n".join(items), style=style)


def stack(*parts: RenderableType | None) -> list[RenderableType]:
    """
    Several renderables, printed in order, with ``None`` dropped.

    Lets a command body compose its view conditionally without building a list by hand and without a blank
    line where a section was skipped.

    Args:
        *parts: The renderables. ``None`` is omitted; a bare ``""`` is a deliberate blank line.

    Returns:
        list[RenderableType]: What :meth:`~vitruvio.cli.output.Console.emit` prints.
    """
    return [part for part in parts if part is not None]


__all__ = [
    "MEMORY_STYLES",
    "SHORT",
    "THEME",
    "console",
    "count",
    "digest",
    "empty",
    "fields",
    "kind",
    "lines",
    "short",
    "stack",
    "table",
    "verdict",
]
