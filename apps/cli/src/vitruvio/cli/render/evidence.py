"""Rendering an Evidence Bundle, a snapshot and a module for a human.

One rule governs everything here: **the renderer never joins matches into a sentence.** The brain returns
evidence and the caller writes the answer -- a CLI that summarised for you would be a CLI that had quietly
become the model, and the summary would carry none of the verification the bundle exists to provide.

The other rule is quieter but matters as much: ``Match.score`` is a *string* in the protocol, and it stays a
string here. Parsing it to a float to reformat it would invent precision the protocol deliberately does not
carry, and a score is agreement between retrieval strategies rather than a probability.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

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


def bundle(payload: Mapping[str, Any], *, content: bool = False) -> list[str]:
    """
    Render an Evidence Bundle.

    Args:
        payload (Mapping[str, Any]): What ``wire.evidence`` produced.
        content (bool): Print each block's full payload rather than one identifying line.

    Returns:
        list[str]: Lines to print.
    """
    import json

    matches: Sequence[Mapping[str, Any]] = payload.get("matches", [])
    verified_against: Mapping[str, str] = payload.get("verified_against", {})

    roots = "  ".join(f"{kind} {short(root)}" for kind, root in sorted(verified_against.items()))
    header = (
        f"{len(matches)} match{'' if len(matches) == 1 else 'es'}"
        f"{'  (truncated -- there may be more)' if payload.get('truncated') else ''}"
    )
    lines = [header]
    if roots:
        lines.append(f"verified against  {roots}")
    if matches and not payload.get("all_verified", True):
        # Should be unreachable: a conforming planner drops what it cannot verify rather than returning it
        # unverified. Reported loudly rather than assumed away, because silence here would hide corruption.
        lines.append("WARNING: not every match verified against the installed snapshot")
    lines.append("")

    for position, match in enumerate(matches, start=1):
        flags = []
        if match.get("superseded_by"):
            flags.append(f"superseded by {short(match['superseded_by'])}")
        if not match.get("resolvable", True):
            # A redacted block is a verifiable member whose bytes were destroyed under policy. A caller has to
            # be able to tell that from corruption, so it is named rather than hidden.
            flags.append("not resolvable (redacted or not installed)")
        suffix = f"   [{'; '.join(flags)}]" if flags else ""

        payload_block: Mapping[str, Any] = match.get("content") or {}
        lines.append(
            f"[{position}] {match.get('score', '-')}  {match.get('memory_type', '?'):<10} "
            f"{short(match.get('block_id'))}{suffix}"
        )
        if content:
            for line in json.dumps(payload_block, indent=2, ensure_ascii=False).splitlines():
                lines.append(f"      {line}")
        else:
            lines.append(f"      {_identifying(payload_block)}")

        for source in match.get("sources", []):
            locator = f" #{source['locator']}" if source.get("locator") else ""
            lines.append(f"      source: {short(source.get('block_id'))}{locator}")

    if not matches:
        lines.append("The brain holds nothing matching. That is an answer, not an error.")
    return lines


def snapshot(payload: Mapping[str, Any]) -> list[str]:
    """
    Render a snapshot's module table.

    Args:
        payload (Mapping[str, Any]): What ``wire.snapshot`` produced.

    Returns:
        list[str]: Lines to print.
    """
    lines = [f"snapshot   {short(payload.get('digest'))}", f"created    {payload.get('created_at', '-')}"]
    if parent := payload.get("parent"):
        lines.append(f"parent     {short(parent)}")

    modules: Mapping[str, Mapping[str, Any]] = payload.get("modules", {})
    if not modules:
        lines.append("")
        lines.append("No modules installed. A brain with no canonical evidence is empty, not broken.")
        return lines

    lines.extend(["", f"{'module':<12} {'blocks':>7}  root", f"{'-' * 12} {'-' * 7}  {'-' * 17}"])
    for kind, reference in sorted(modules.items()):
        lines.append(f"{kind:<12} {reference.get('block_count', 0):>7}  {short(reference.get('root'))}")
    return lines


def modules(entries: Sequence[Mapping[str, Any]]) -> list[str]:
    """
    Render the per-module table ``brain info`` prints.

    Args:
        entries (Sequence[Mapping[str, Any]]): What ``wire.module`` produced, one per module.

    Returns:
        list[str]: Lines to print.
    """
    if not entries:
        return ["No modules installed."]
    lines = [
        f"{'module':<12} {'blocks':>7} {'root':<18} {'flags':<16} indices",
        f"{'-' * 12} {'-' * 7} {'-' * 18} {'-' * 16} {'-' * 30}",
    ]
    for entry in entries:
        flags = []
        if entry.get("append_only"):
            flags.append("append-only")
        if not entry.get("droppable"):
            flags.append("no-drop")
        lines.append(
            f"{entry['memory_type']:<12} {entry['block_count']:>7} {short(entry['root']):<18} "
            f"{','.join(flags) or '-':<16} {', '.join(entry.get('indices', [])) or '(none)'}"
        )
    return lines
