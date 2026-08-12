"""Rendering an Evidence Bundle, a snapshot and a module for a human.

One rule governs everything here: **the renderer never joins matches into a sentence.** The brain returns
evidence and the caller writes the answer -- a CLI that summarised for you would be a CLI that had quietly
become the model, and the summary would carry none of the verification the bundle exists to provide.

The other rule is quieter but matters as much: ``Match.score`` is a *string* in the protocol, and it stays a
string here. Parsing it to a float to reformat it would invent precision the protocol deliberately does not
carry, and a score is agreement between retrieval strategies rather than a probability.

What a table adds over the aligned columns this used to print is not decoration. A bundle's rows carry a score,
a memory type, an identity and a flag, and how wide each of those needs to be depends on the results -- so a
fixed width either truncated an identity or wasted half the line on one. Rich measures the rows it was given.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from rich.console import RenderableType
from rich.text import Text

from vitruvio.cli.render import theme

SHORT = theme.SHORT
"""Re-exported so the digest width has one definition. See :data:`vitruvio.cli.render.theme.SHORT`."""


def short(digest: str | None) -> str:
    """
    Abbreviate a ``sha256:...`` digest for display.

    Args:
        digest (str | None): The digest, or ``None``.

    Returns:
        str: An abbreviated form, or ``-``.
    """
    return theme.short(digest)


def _identifying(payload: Mapping[str, Any]) -> str:
    """
    The one field that says what a block is, without printing the whole payload.

    Which field that is depends on the memory type, and picking it per type rather than dumping everything is
    what makes a result list scannable. ``--content`` prints the full payload for anyone who wants it.

    Args:
        payload (Mapping[str, Any]): The block's payload.

    Returns:
        str: A one-line identification.
    """
    for field in ("label", "summary", "statement", "goal", "media_type"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    record = payload.get("record")
    if isinstance(record, Mapping):
        return f"{record.get('record_type', 'provenance')} record"
    return "(no identifying field)"


def bundle(data: Mapping[str, Any], *, content: bool = False) -> list[RenderableType]:
    """
    Render an Evidence Bundle.

    Args:
        data (Mapping[str, Any]): What ``wire.evidence`` produced.
        content (bool): Print each block's full payload rather than one identifying line.

    Returns:
        list[RenderableType]: What to print.
    """
    matches: Sequence[Mapping[str, Any]] = data.get("matches", [])
    verified_against: Mapping[str, str] = data.get("verified_against", {})

    header = Text.assemble(
        (str(len(matches)), "count"),
        f" match{'' if len(matches) == 1 else 'es'}",
        ("  (truncated -- there may be more)", "warn") if data.get("truncated") else "",
    )

    roots = None
    if verified_against:
        roots = theme.fields(
            [
                (
                    "verified against",
                    Text("  ").join(
                        Text.assemble(theme.kind(name), " ", theme.digest(root))
                        for name, root in sorted(verified_against.items())
                    ),
                )
            ]
        )

    unverified = None
    if matches and not data.get("all_verified", True):
        # Should be unreachable: a conforming planner drops what it cannot verify rather than returning it
        # unverified. Reported loudly rather than assumed away, because silence here would hide corruption.
        unverified = Text("WARNING: not every match verified against the installed snapshot", style="bad")

    if not matches:
        return theme.stack(
            header,
            roots,
            unverified,
            "",
            theme.empty("The brain holds nothing matching. That is an answer, not an error."),
        )

    rows = theme.table(("#", "right"), ("score", "right"), "memory", "block", "identity")
    detail: list[RenderableType] = []
    for position, match in enumerate(matches, start=1):
        flags = []
        if match.get("superseded_by"):
            flags.append(f"superseded by {short(match['superseded_by'])}")
        if not match.get("resolvable", True):
            # A redacted block is a verifiable member whose bytes were destroyed under policy. A caller has to
            # be able to tell that from corruption, so it is named rather than hidden.
            flags.append("not resolvable (redacted or not installed)")

        payload_block: Mapping[str, Any] = match.get("content") or {}
        identity = Text(_identifying(payload_block))
        if flags:
            identity.append_text(Text(f"   [{'; '.join(flags)}]", style="flag"))
        for source in match.get("sources", []):
            locator = f" #{source['locator']}" if source.get("locator") else ""
            identity.append_text(Text(f"\nsource: {short(source.get('block_id'))}{locator}", style="muted"))

        rows.add_row(
            str(position),
            Text(str(match.get("score", "-")), style="score"),
            theme.kind(match.get("memory_type")),
            theme.digest(match.get("block_id")),
            identity,
        )
        if content:
            detail.append(
                theme.fields(
                    [("block", theme.digest(match.get("block_id"), full=True))],
                    title=f"[{position}]",
                )
            )
            detail.append(payload(payload_block))

    return theme.stack(header, roots, unverified, "", rows, *detail)


def payload(value: Any) -> RenderableType:
    """
    Any JSON-able value, printed as JSON and syntax-highlighted when colour is available.

    Used wherever a command's honest answer is the document itself: a resolved block's payload, a task
    definition, a candidate set. Reformatting those into prose would be inventing structure the protocol
    deliberately left as data.

    Args:
        value (Any): The value.

    Returns:
        RenderableType: The rendering.
    """
    from rich.syntax import Syntax

    body = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    # `background_color="default"` keeps the terminal's own background: a syntax block that paints its own is a
    # block that looks wrong in half the themes people use, and unreadable in the other half.
    return Syntax(body, "json", theme="ansi_dark", background_color="default", word_wrap=True)


def snapshot(data: Mapping[str, Any]) -> list[RenderableType]:
    """
    Render a snapshot's module table.

    Args:
        data (Mapping[str, Any]): What ``wire.snapshot`` produced.

    Returns:
        list[RenderableType]: What to print.
    """
    head: list[tuple[str, Any]] = [
        ("snapshot", theme.digest(data.get("digest"), full=True)),
        ("created", data.get("created_at", "-")),
    ]
    if parent := data.get("parent"):
        head.append(("parent", theme.digest(parent)))

    modules_held: Mapping[str, Mapping[str, Any]] = data.get("modules", {})
    if not modules_held:
        return theme.stack(
            theme.fields(head),
            "",
            theme.empty("No modules installed. A brain with no canonical evidence is empty, not broken."),
        )

    rows = theme.table("module", ("blocks", "right"), "root")
    for name, reference in sorted(modules_held.items()):
        rows.add_row(theme.kind(name), str(reference.get("block_count", 0)), theme.digest(reference.get("root")))
    return theme.stack(theme.fields(head), "", rows)


def modules(entries: Sequence[Mapping[str, Any]]) -> list[RenderableType]:
    """
    Render the per-module table ``brain info`` prints.

    Args:
        entries (Sequence[Mapping[str, Any]]): What ``wire.module`` produced, one per module.

    Returns:
        list[RenderableType]: What to print.
    """
    if not entries:
        return [theme.empty("No modules installed.")]
    rows = theme.table("module", ("blocks", "right"), "root", "flags", "indices")
    for entry in entries:
        flags = []
        if entry.get("append_only"):
            flags.append("append-only")
        if not entry.get("droppable"):
            flags.append("no-drop")
        rows.add_row(
            theme.kind(entry["memory_type"]),
            str(entry["block_count"]),
            theme.digest(entry["root"]),
            Text(",".join(flags) or "-", style="muted" if not flags else "flag"),
            Text(
                ", ".join(entry.get("indices", [])) or "(none)", style="muted" if not entry.get("indices") else "value"
            ),
        )
    return [rows]


def rows(result: Mapping[str, Any]) -> list[RenderableType]:
    """
    Render a page of blocks from ``service.blocks`` -- one module, in its own order.

    This is the list ``vitruvio inspect blocks`` prints and the same data the TUI puts in its table. No score
    column, because there is no ranking: these rows are the module's own order, and a reader who wants
    relevance is running a query.

    Args:
        result (Mapping[str, Any]): What ``service.blocks`` produced.

    Returns:
        list[RenderableType]: What to print.
    """
    entries: Sequence[Mapping[str, Any]] = result.get("rows", [])
    head: list[tuple[str, Any]] = [("module", theme.kind(result.get("memory_type")))]
    if result.get("installed", True):
        head += [
            ("root", theme.digest(result.get("root"), full=True)),
            ("blocks", str(result.get("block_count", 0))),
        ]
    if result.get("filter"):
        head.append(("filter", Text(f"{result['filter']!r} -- {result.get('matched', 0)} matching")))

    if not result.get("installed", True):
        # Said as its own sentence rather than as an empty table. "Nothing matched" and "this module was never
        # installed" are different facts, and a reader who cannot tell them apart will go looking for blocks
        # that are not missing at all -- they are somewhere else, in the brain this one was pulled from.
        return theme.stack(
            theme.fields(head),
            "",
            theme.empty("This module is not installed. A selective pull leaves the others missing, not broken."),
        )
    if not entries:
        return theme.stack(theme.fields(head), "", theme.empty("Nothing in this module matches."))

    table = theme.table("block", "title", "detail", ("size", "right"), "type")
    for entry in entries:
        title = Text(str(entry.get("title", "")), style="value" if entry.get("resolvable", True) else "bad")
        size = entry.get("size")
        table.add_row(
            theme.digest(entry.get("block_id")),
            title,
            Text(str(entry.get("detail", "")), style="muted"),
            Text(_bytes(size) if isinstance(size, int) else "-", style="muted"),
            Text(str(entry.get("media_type") or entry.get("kind") or ""), style="muted"),
        )
    footer = None
    if result.get("truncated"):
        remaining = result.get("matched", 0) - result.get("offset", 0) - len(entries)
        footer = Text(f"... {remaining} more -- raise --limit or pass --offset", style="muted")
    return theme.stack(theme.fields(head), "", table, footer)


def _bytes(size: int) -> str:
    """
    A byte count a person can read.

    Args:
        size (int): The count.

    Returns:
        str: e.g. ``1.4 MiB``.
    """
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"  # pragma: no cover -- the loop above always returns


def records(result: Mapping[str, Any]) -> list[RenderableType]:
    """
    Render the provenance records that name a block: where it came from, and what has been done to it.

    Args:
        result (Mapping[str, Any]): What ``service.related`` produced.

    Returns:
        list[RenderableType]: What to print.
    """
    found: Sequence[Mapping[str, Any]] = result.get("records", [])
    head = theme.fields(
        [("block", theme.digest(result.get("block"), full=True)), ("records", str(result.get("count", 0)))]
    )
    if not found:
        return theme.stack(
            head, "", theme.empty("No provenance names this block. In a pulled brain, provenance may not be installed.")
        )
    table = theme.table("record", "kind", "actor", "at", "names")
    for entry in found:
        record = entry.get("record", {})
        actor = record.get("actor") or {}
        names = [
            value
            for key, value in record.items()
            if key != "block" and isinstance(value, str) and value.startswith("sha256:")
        ]
        table.add_row(
            theme.digest(entry.get("block_id")),
            Text(str(record.get("record_type", "?")), style="provenance"),
            Text(str(actor.get("id", "-"))),
            Text(str(record.get("at", "-")), style="muted"),
            Text(", ".join(short(name) for name in names) or "-", style="digest"),
        )
    return theme.stack(head, "", table)
