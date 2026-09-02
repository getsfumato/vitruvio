"""Rich views for catalog navigation, governance, authorship, and historical audit."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from rich.console import RenderableType
from rich.text import Text
from rich.tree import Tree

from vitruvio.cli.render import theme


def authenticity(value: Any) -> Text:
    """Style an authenticity state while keeping the state in text."""
    state = str(value or "unknown")
    style = {
        "authorized": "ok",
        "unsigned": "warn",
        "attributable": "info",
        "unauthorized": "bad",
        "unknown": "muted",
    }.get(state, "muted")
    return Text(state, style=style)


def creator(authorship: Mapping[str, Any] | None) -> tuple[Text, Text]:
    """The compact creator and verification cells used by both CLI and TUI tables."""
    claims = list((authorship or {}).get("claims") or ())
    if not claims:
        return Text("unknown", style="muted"), Text("unknown", style="muted")
    first = claims[0]
    actor = first.get("actor") or {}
    label = str(actor.get("id") or "unknown")
    if len(claims) > 1:
        label += f" +{len(claims) - 1}"
    verified = first.get("actor_verified")
    if verified is True:
        state = Text("verified", style="ok")
    elif verified is False:
        state = Text("asserted", style="warn")
    else:
        state = Text("unknown", style="muted")
    return Text(label), state


def authorship(data: Mapping[str, Any] | None) -> list[RenderableType]:
    """Render every creation claim and the snapshot evidence behind it."""
    if not data or not data.get("claims"):
        complete = bool(data and data.get("complete"))
        message = "No creation provenance names this block." if complete else "Creation provenance is incomplete."
        return [theme.empty(message)]

    parts: list[RenderableType] = []
    for index, claim in enumerate(data["claims"], start=1):
        actor = claim.get("actor") or {}
        assisted = claim.get("assisted_by") or []
        actor_state = claim.get("actor_verified")
        verified = (
            Text("verified", style="ok")
            if actor_state is True
            else Text("asserted", style="warn")
            if actor_state is False
            else Text("unknown", style="muted")
        )
        parts.append(
            theme.fields(
                [
                    ("actor", str(actor.get("id") or "unknown")),
                    ("actor kind", str(actor.get("kind") or "unknown")),
                    ("actor identity", verified),
                    (
                        "assisted by",
                        ", ".join(str(item.get("id", "unknown")) for item in assisted) or "(none)",
                    ),
                    ("provenance", theme.digest(claim.get("provenance"), full=True)),
                    ("snapshot", theme.digest(claim.get("snapshot"), full=True)),
                    ("snapshot auth", authenticity(claim.get("snapshot_authenticity"))),
                    ("signature subjects", ", ".join(claim.get("signature_subjects") or ()) or "(none)"),
                    ("trust root", theme.digest(claim.get("trust_root"), full=True)),
                    ("pinned", theme.verdict(bool(claim.get("pinned")))),
                ],
                title=f"creation claim {index}" if len(data["claims"]) > 1 else "authorship",
            )
        )
    if not data.get("complete"):
        parts.extend(("", theme.empty("The provenance or historical evidence for this attribution is incomplete.")))
    return parts


def source_rows(rows: Sequence[Mapping[str, Any]]) -> RenderableType:
    """Canonical source rows with human names and stable identities."""
    if not rows:
        return theme.empty("No canonical sources.")
    table = theme.table("source", "media type", "creator", "identity", "block")
    for row in rows:
        actor, verified = creator(row.get("authorship"))
        table.add_row(
            str(row.get("title") or row.get("origin") or "(unnamed)"),
            str(row.get("media_type") or "-"),
            actor,
            verified,
            theme.digest(row.get("block_id")),
        )
    return table


def catalog(data: Mapping[str, Any]) -> Tree:
    """Draw the portable class hierarchy as folders and sources as leaves."""
    root = Tree(Text("catalog", style="heading"), guide_style="muted")

    def add_class(parent: Tree, node: Mapping[str, Any]) -> None:
        label = Text(str(node.get("label") or "(unnamed)"), style="semantic")
        label.append(f"  {node.get('effective_source_count', 0)} sources", style="muted")
        branch = parent.add(label)
        for source in node.get("direct_sources") or ():
            title = str(source.get("title") or source.get("origin") or theme.short(source.get("block_id")))
            leaf = Text(title, style="canonical")
            actor, verified = creator(source.get("authorship"))
            leaf.append("  ")
            leaf.append_text(actor)
            leaf.append("  ")
            leaf.append_text(verified)
            branch.add(leaf)
        for child in node.get("children") or ():
            add_class(branch, child)

    for scheme in data.get("schemes") or ():
        suffix = "exclusive" if scheme.get("exclusive") else "multi-valued"
        branch = root.add(Text.assemble((str(scheme.get("name")), "heading"), (f"  {suffix}", "muted")))
        for node in scheme.get("roots") or ():
            add_class(branch, node)

    unclassified = data.get("unclassified") or ()
    if unclassified:
        branch = root.add(Text.assemble(("unclassified", "warn"), (f"  {len(unclassified)} sources", "muted")))
        for source in unclassified:
            branch.add(Text(str(source.get("title") or source.get("block_id")), style="canonical"))
    if not data.get("schemes") and not unclassified:
        root.add(Text("empty", style="muted"))
    return root


def trust_root(data: Mapping[str, Any]) -> list[RenderableType]:
    """Draw governance as a summary followed by a key-permission tree."""
    summary = theme.fields(
        [
            ("snapshot", theme.digest(data.get("snapshot"), full=True)),
            ("governed", theme.verdict(bool(data.get("governed")))),
            ("authenticity", authenticity(data.get("authenticity"))),
            ("pinned", theme.verdict(bool(data.get("pinned")))),
        ],
        title="trust root",
    )
    root_data = data.get("trust_root")
    if not root_data:
        return theme.stack(summary, "", theme.empty("This brain is ungoverned and carries no authorized keys."))

    summary.add_row("digest", theme.digest(root_data.get("digest"), full=True))
    summary.add_row("revision", str(root_data.get("revision", "-")))
    summary.add_row("namespace", str(root_data.get("namespace", "-")))
    summary.add_row("govern quorum", str(root_data.get("govern_quorum", "-")))

    keys = Tree(Text("authorized keys", style="heading"), guide_style="muted")
    for key in data.get("keys") or ():
        status = "active" if key.get("active") else "inactive"
        style = "ok" if key.get("active") else "bad"
        label = Text.assemble(
            (str(key.get("subject") or "(no subject)"), "value"),
            (f"  {status}", style),
            (f"  {key.get('fingerprint')}", "digest"),
        )
        branch = keys.add(label)
        branch.add(Text(f"scopes  {', '.join(key.get('scopes') or ()) or '(none)'}"))
        branch.add(Text(f"since revision  {key.get('since', '-')}", style="muted"))
        if key.get("retired_from") is not None:
            branch.add(Text(f"retired from  {key['retired_from']}", style="warn"))
        if key.get("compromised_from"):
            branch.add(Text(f"compromised from  {key['compromised_from']}", style="bad"))
    return theme.stack(summary, "", keys)


def history(data: Mapping[str, Any]) -> RenderableType:
    """Draw the enriched audit fields for every reachable snapshot."""
    snapshots = data.get("snapshots") or ()
    if not snapshots:
        return theme.empty("No snapshots yet. A brain with no canonical evidence has no version to retain.")
    table = theme.table("", "snapshot", "created", "actor", "auth", "integrity", ("blocks", "right"))
    for item in snapshots:
        marker = "HEAD" if item.get("head") else "*" if item.get("on_ancestry") else "o"
        actors = item.get("actors") or ()
        actor = ", ".join(str(value.get("id", "unknown")) for value in actors) or "unknown"
        integrity = item.get("integrity")
        table.add_row(
            Text(marker, style="ok" if item.get("head") else "count" if item.get("on_ancestry") else "muted"),
            theme.digest(item.get("digest")),
            str(item.get("created_at") or "unresolved"),
            Text(actor, style="value" if actors else "muted"),
            authenticity(item.get("authenticity")),
            Text("ok", style="ok") if integrity is True else Text("failed", style="bad") if integrity is False else Text("unknown", style="muted"),
            str(item.get("block_count", "-")),
        )
    return table


__all__ = ["authenticity", "authorship", "catalog", "creator", "history", "source_rows", "trust_root"]
