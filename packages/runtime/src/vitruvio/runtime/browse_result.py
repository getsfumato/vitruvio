"""Typed browse results: the row a module's blocks are listed as, and the page around it.

The fourth vertical slice, and the one that finishes what ADR-0023 started. Retrieval and browsing describe the
same blocks, and until now they described them twice: a match had a type and a row was a ``dict[str, Any]`` whose
keys two readers knew by heart. The naming rule already has one owner, :func:`vitruvio.runtime.browse.identify`;
this gives the row itself one.

A row follows the payload regime of :mod:`vitruvio.runtime.block_result`, for the same reason: it is projected from
``Block.payload()``, and a field that memory type does not have is *absent* rather than ``null`` -- a ``media_type``
of ``None`` on a semantic block would suggest the field means something there. So everything but the five keys every
row carries is ``NotRequired``, and presence stays a fact to read.

``cast`` sits where mypy cannot follow the construction, as ADR-0023 says: ``browse.row`` accretes its optional keys
in a loop over field names, which no type checker can relate to a ``TypedDict``. That is the cost of a projection
whose shape depends on the memory type, and it falls on one function rather than on every reader of the result.

No ``from __future__ import annotations`` here, for the reason ``block_result`` states: under PEP 563 a ``TypedDict``
reads ``NotRequired`` as a string and files every optional key as required, silently, on 3.11.
"""

from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict

UNNAMED = "(unnamed)"
"""What a block is called when nothing in its payload names it. One sentinel, so every interface says the same.

Here rather than beside :func:`vitruvio.runtime.browse.identify`, which decides the name, because this is the
module that declares the field it fills -- and because the rule has to be able to import the shape it builds.
``browse`` re-exports it, so every existing reader is unaffected.
"""


class RowContentResult(TypedDict):
    """Bytes a row names: a canonical block's normalized view, or the datum a derived block cites.

    Nested rather than flattened into the row because a canonical block *is* its bytes while a derived block
    *names* bytes as its own datum, and a reader has to be able to tell which of the two it is holding.
    """

    blob: str
    media_type: str
    size: int


class RowAuthorshipResult(TypedDict):
    """Who is claimed to have created the block, and whether the lookup behind that claim was complete.

    ``applicable`` is false for a provenance row: a ledger entry is itself the evidence, not a subject whose
    creation is recursively attributed, and an interface that showed "no claims" there would present a correct
    answer as an incomplete one.

    ``claims`` and ``provenance`` stay ``Any``: they belong to the authenticity domain, which #74 puts out of
    scope, and are pinned by the recorded wire shape until that slice types them.
    """

    applicable: bool
    complete: bool
    provenance: Any
    claims: list[Any]


class BrowseRowResult(TypedDict):
    """One block as a line in a list, as ``browse.row`` and ``browse.unreadable`` build it.

    The five required keys are the ones a row is useless without, and ``unreadable`` supplies exactly those: a
    block whose bytes are gone is still listed, because dropping it would make a redacted brain look like a
    smaller one.
    """

    block_id: str
    memory_type: str
    title: str
    detail: str
    resolvable: bool
    origin: NotRequired[str]
    media_type: NotRequired[str]
    size: NotRequired[int]
    subject: NotRequired[str]
    occurred_at: NotRequired[str]
    kind: NotRequired[str]
    tags: NotRequired[list[str]]
    blob: NotRequired[str]
    normalized_view: NotRequired[RowContentResult]
    content: NotRequired[RowContentResult]
    evidence_count: NotRequired[int]
    sources_count: NotRequired[int]
    steps_count: NotRequired[int]
    relations_count: NotRequired[int]


class ProjectedRowResult(BrowseRowResult):
    """A row with the evidence needed to judge its creator attached.

    Its own type rather than an optional key on the row, the way a compound match is its single-brain match plus
    ``brains``: ``browse.row`` never carries authorship and ``block_rows.project_rows`` always does, and one type
    with the key optional would describe neither.
    """

    authorship: RowAuthorshipResult


class BlocksResult(TypedDict):
    """One page of a module's blocks.

    ``installed`` is a key of its own rather than an empty ``rows`` list, because a module absent from a
    selectively pulled brain and a module with nothing matching are different facts, and a reader who cannot tell
    them apart goes looking for blocks that are not missing at all.

    ``matched`` counts the whole module when no filter ran and the whole scan when one did; ``block_count`` is
    always the module's own total, so the two together say whether a filter narrowed anything.
    """

    memory_type: str
    root: str | None
    block_count: int
    matched: int
    offset: int
    limit: int
    rows: list[ProjectedRowResult]
    truncated: bool
    filter: str | None
    installed: bool
    provenance: Any


@dataclass(frozen=True, slots=True)
class BrowseRowView:
    """Shared interpretation of one browse row for the CLI table and the TUI table.

    The two used to read the row by string independently and had already drifted in both directions: the TUI fell
    back through the content a derived block names for its type column and the CLI did not, while the CLI showed a
    detail and a size the TUI had no room for. Drift in that direction is silent -- both tables keep rendering --
    which is why the interpretation lives here rather than in whichever of them was edited last.
    """

    row: BrowseRowResult

    @property
    def title(self) -> str:
        """What the block is called. ``browse.identify`` already decided it; this is where the empty case ends."""
        return self.row.get("title") or UNNAMED

    @property
    def detail(self) -> str:
        """What the block says, as distinct from what it is called."""
        return self.row.get("detail", "")

    @property
    def resolvable(self) -> bool:
        """Whether the store could read it. A row that says no is still a row, and is styled as one."""
        return self.row.get("resolvable", True)

    @property
    def media_label(self) -> str:
        """What kind of thing this is, for the ``type`` column.

        Falls through to the content a derived block names, which is the TUI's rule and now both: a semantic block
        whose datum is a diagram has a media type the same way a canonical PDF does, and a blank column said
        otherwise. The CLI table gains that fallback here; see this slice's record for what a person sees change.
        """
        content: RowContentResult | dict[str, str] = self.row.get("content") or {}
        return str(self.row.get("media_type") or self.row.get("kind") or content.get("media_type") or "")

    @property
    def size(self) -> int | None:
        """How large the bytes are, when the row names any. ``None`` where the memory type has no size at all."""
        size = self.row.get("size")
        return size if isinstance(size, int) else None


__all__ = [
    "BlocksResult",
    "BrowseRowResult",
    "BrowseRowView",
    "ProjectedRowResult",
    "RowAuthorshipResult",
    "RowContentResult",
]
