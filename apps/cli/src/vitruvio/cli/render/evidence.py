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


def _identity(match: Mapping[str, Any]) -> Text:
    """
    The one-line identification of a match: what the block is, its flags, and the sources it cites.

    Args:
        match (Mapping[str, Any]): One match of a bundle.

    Returns:
        Text: The cell.
    """
    flags = []
    if match.get("superseded_by"):
        flags.append(f"superseded by {short(match['superseded_by'])}")
    if not match.get("resolvable", True):
        # A redacted block is a verifiable member whose bytes were destroyed under policy. A caller has to
        # be able to tell that from corruption, so it is named rather than hidden.
        flags.append("not resolvable (redacted or not installed)")

    identity = Text(_identifying(match.get("content") or {}))
    if flags:
        identity.append_text(Text(f"   [{'; '.join(flags)}]", style="flag"))
    for source in match.get("sources", []):
        locator = f" #{source['locator']}" if source.get("locator") else ""
        identity.append_text(Text(f"\nsource: {short(source.get('block_id'))}{locator}", style="muted"))
    return identity


def _unverified(data: Mapping[str, Any]) -> Text | None:
    """
    The warning every view prints when a bundle says it holds something unverified.

    Should be unreachable: a conforming planner drops what it cannot verify rather than returning it unverified.
    Reported loudly rather than assumed away, and from one place -- the fused compound view once omitted it, which
    is exactly how corruption or a non-conforming member comes to look like a clean, slightly smaller result.
    """
    if data.get("matches") and not data.get("all_verified", True):
        return Text("WARNING: not every match verified against the installed snapshot", style="bad")
    return None


def _rows(matches: Sequence[Mapping[str, Any]], *, content: bool, origins: bool = False) -> list[RenderableType]:
    """
    The match table, followed by each match's full payload when ``--content`` asked for it.

    One builder for the single-brain bundle and the fused compound, differing only in whether a ``brains`` column
    is drawn. The two views used to assemble their rows separately, and two copies of "position, score, memory,
    block, identity" is how a change to the row format reaches one table and not the other.

    Args:
        matches (Sequence[Mapping[str, Any]]): The matches, in the order to print.
        content (bool): Append each block's full payload after the table.
        origins (bool): Draw the ``brains`` column, for a ranking that spans brains.
    """
    columns: list[str | tuple[str, str]] = [("#", "right"), ("score", "right")]
    if origins:
        columns.append("brains")
    columns.extend(["memory", "block", "identity"])
    rows = theme.table(*columns)
    detail: list[RenderableType] = []
    for position, match in enumerate(matches, start=1):
        cells: list[RenderableType] = [str(position), Text(str(match.get("score", "-")), style="score")]
        if origins:
            cells.append(_origins(match))
        cells.extend([theme.kind(match.get("memory_type")), theme.digest(match.get("block_id")), _identity(match)])
        rows.add_row(*cells)
        if content:
            detail.append(
                theme.fields([("block", theme.digest(match.get("block_id"), full=True))], title=f"[{position}]")
            )
            detail.append(payload(match.get("content") or {}))
    return [rows, *detail]


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

    unverified = _unverified(data)
    if not matches:
        return theme.stack(
            header,
            roots,
            unverified,
            "",
            theme.empty("The brain holds nothing matching. That is an answer, not an error."),
        )
    return theme.stack(header, roots, unverified, "", *_rows(matches, content=content))


def _member_roots(members: Sequence[Mapping[str, Any]]) -> RenderableType | None:
    """
    One ``verified against`` line per brain, never one merged line.

    Two brains holding semantic memory have two semantic roots. A single line keyed by memory type could carry only
    one of them, and a citation drawn from a fused match has to name the root of the brain it was verified in.
    """
    pairs: list[tuple[str, Any]] = []
    for member in members:
        roots: Mapping[str, str] = member.get("verified_against") or {}
        if roots:
            pairs.append(
                (
                    str(member["brain"]),
                    Text("  ").join(
                        Text.assemble(theme.kind(name), " ", theme.digest(root)) for name, root in sorted(roots.items())
                    ),
                )
            )
    return theme.fields(pairs, title="verified against") if pairs else None


def _origins(match: Mapping[str, Any]) -> Text:
    """
    ``metrica-a#1  metrica-b#3``: each brain that returned the match, with its rank there.

    Rank rather than each brain's score, because rank is the quantity fusion actually used -- and printing two
    per-brain scores side by side would invite comparing numbers that were each normalised to a different ``1.00``.
    """
    return Text("  ".join(f"{item['brain']}#{item['rank']}" for item in match.get("brains", [])), style="muted")


def _grouped(data: Mapping[str, Any], *, content: bool) -> list[RenderableType]:
    """
    One section per brain, each drawn by :func:`bundle`.

    A compound section and a ``search`` result are then the same table: a reader who has learnt one has learnt the
    other, and the two cannot drift, because there is one renderer rather than a copy of it.
    """
    matches: Sequence[Mapping[str, Any]] = data.get("matches", [])
    sections: list[RenderableType] = []
    for member in data.get("members", []):
        name = str(member["brain"])
        own = [match for match in matches if match.get("brains", [{}])[0].get("brain") == name]
        sections.append("")
        sections.append(Text(name, style="heading"))
        sections.extend(
            bundle(
                {
                    "matches": own,
                    "verified_against": member.get("verified_against") or {},
                    "truncated": member.get("truncated", False),
                    "all_verified": member.get("all_verified", True),
                },
                content=content,
            )
        )
    return sections


def _fused(data: Mapping[str, Any], *, content: bool) -> list[RenderableType]:
    """
    One table across brains, with a ``brains`` column, rather than one section per brain.

    The ranking spans brains, so per-brain sections would misstate it -- the whole point of ``--fuse`` is a single
    order. The verification warning is drawn here too: a member that returned something unverified is not less
    alarming for having been fused with others.
    """
    matches: Sequence[Mapping[str, Any]] = data.get("matches", [])
    if not matches:
        return theme.stack("", theme.empty("No brain holds anything matching. That is an answer, not an error."))
    return theme.stack(_unverified(data), "", *_rows(matches, content=content, origins=True))


def compound(data: Mapping[str, Any], *, content: bool = False) -> list[RenderableType]:
    """
    Render a compound: several brains' evidence for one query.

    Grouped output reuses :func:`bundle` per brain, so a compound section and a single-brain result are the same
    table -- one shape to learn. Fused output is one table with a ``brains`` column, because the ranking is across
    brains and a per-brain section would misstate it.

    Args:
        data (Mapping[str, Any]): What ``CompoundOps.compound_search`` produced.
        content (bool): Print each block's full payload rather than one identifying line.

    Returns:
        list[RenderableType]: What to print.
    """
    matches: Sequence[Mapping[str, Any]] = data.get("matches", [])
    members: Sequence[Mapping[str, Any]] = data.get("members", [])
    header = Text.assemble(
        (str(len(matches)), "count"),
        f" match{'' if len(matches) == 1 else 'es'} across ",
        (str(len(members)), "count"),
        f" brain{'' if len(members) == 1 else 's'}",
        ("  (truncated -- there may be more)", "warn") if data.get("truncated") else "",
    )
    skipped = data.get("skipped") or []
    note = Text(f"skipped: {', '.join(str(item['brain']) for item in skipped)}", style="muted") if skipped else None
    if data.get("fused"):
        return theme.stack(header, _member_roots(members), note, *_fused(data, content=content))
    return theme.stack(header, note, *_grouped(data, content=content))


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
    # `parents`, plural, and not `parent`. A reconciliation names more than one, so history is a DAG; the
    # singular field this read until the protocol grew reconciliation no longer exists, and asking for it
    # printed nothing rather than failing -- every snapshot looked parentless.
    #
    # The first parent is labelled, because it is not merely first: it is the history a reconciliation was
    # performed *onto*, it is what every rule meaning "the parent" means, and it is the chain an audit walks.
    parents = data.get("parents") or []
    for position, parent in enumerate(parents):
        label = "parent" if len(parents) == 1 else ("first parent" if position == 0 else "merged parent")
        head.append((label, theme.digest(parent)))

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

    from vitruvio.cli.render.audit import creator

    table = theme.table("block", "title", "creator", "identity", "detail", ("size", "right"), "type")
    for entry in entries:
        title = Text(str(entry.get("title", "")), style="value" if entry.get("resolvable", True) else "bad")
        size = entry.get("size")
        actor, verified = creator(entry.get("authorship"))
        table.add_row(
            theme.digest(entry.get("block_id")),
            title,
            actor,
            verified,
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


def graph(snapshots: Sequence[Mapping[str, Any]], *, ancestry: Sequence[str] = ()) -> list[RenderableType]:
    """
    Render a snapshot history as the DAG it is.

    A flat list was the honest rendering while a snapshot had one parent. Reconciliation made history a graph,
    and a list of digests ordered by time cannot show the one thing a reader is now looking for: where two
    lines of work parted and where they came back together.

    The glyphs carry the whole reading. ``*`` is on the first-parent chain -- the line the protocol reads as
    *what this brain is*, and the one an audit follows. ``o`` is reachable but off it: real history, arrived by
    being merged. ``M`` is where a reconciliation joined, and the extra digests on that row are the parents it
    joined besides the first.

    Args:
        snapshots (Sequence[Mapping[str, Any]]): What ``wire.snapshot`` produced, newest first.
        ancestry (Sequence[str]): The first-parent chain, which the glyph depends on and cannot be derived
            from the snapshots alone -- being *a* parent of something does not put a snapshot on it.

    Returns:
        list[RenderableType]: What to print.
    """
    if not snapshots:
        return [theme.empty("No snapshots yet. A brain with no canonical evidence has no version to retain.")]

    trunk = set(ancestry)
    rows = theme.table("", "snapshot", "created", "actor", "auth", ("blocks", "right"), "joined")
    for item in snapshots:
        digest_value = str(item.get("digest", ""))
        parents = item.get("parents") or []
        merged = len(parents) > 1
        if merged:
            glyph = Text("M", style="ok")
        elif digest_value in trunk:
            glyph = Text("*", style="count")
        else:
            # Reachable, off the first-parent chain. Someone else's version, kept because a merge named it.
            glyph = Text("o", style="muted")
        actors = item.get("actors") or ()
        actor = ", ".join(str(value.get("id", "unknown")) for value in actors) or "unknown"
        rows.add_row(
            glyph,
            theme.digest(digest_value),
            str(item.get("created_at", "-")),
            Text(actor, style="value" if actors else "muted"),
            theme.authenticity(item.get("authenticity")),
            str(item.get("block_count", 0)),
            Text(", ".join(theme.short(parent) for parent in parents[1:]) or "", style="muted"),
        )
    legend = Text.assemble(
        ("*", "count"),
        (" first-parent chain   ", "muted"),
        ("o", "muted"),
        (" merged in   ", "muted"),
        ("M", "ok"),
        (" a reconciliation", "muted"),
    )
    return theme.stack(rows, "", legend)


def divergence(data: Mapping[str, Any]) -> list[RenderableType]:
    """
    Render where two histories parted, and what each has added since.

    Deliberately not a graph. The other side's snapshots are held locally after a fetch but their *shape* is
    not what the question is about -- what a person deciding a reconciliation needs is the fork point and the
    per-module arithmetic, which is what Equation 1 will act on.

    Args:
        data (Mapping[str, Any]): What ``ReconcileOps.tree`` produced.

    Returns:
        list[RenderableType]: What to print.
    """
    head: list[tuple[str, Any]] = [
        ("ours", theme.digest(data.get("ours"), full=True)),
        ("theirs", theme.digest(data.get("theirs"), full=True)),
        ("parted at", theme.digest(data.get("ancestor"), full=True)),
        ("their versions since", theme.count(data.get("collapsed", 0))),
    ]
    if data.get("reconciling"):
        head.append(("state", Text("a reconciliation is open", style="warn")))

    if data.get("is_noop"):
        return theme.stack(theme.fields(head), "", theme.empty("this brain already contains that history"))

    rows = theme.table("module", ("ours", "right"), ("theirs", "right"), ("leaving", "right"), ("result", "right"))
    for name, module in sorted((data.get("modules") or {}).items()):
        rows.add_row(
            theme.kind(name),
            str(len(module.get("added_by_us") or ())),
            str(len(module.get("added_by_them") or ())),
            Text(str(len(module.get("removed") or ())), style="warn" if module.get("removed") else ""),
            str(len(module.get("block_ids") or ())),
        )
    return theme.stack(theme.fields(head), "", rows)
