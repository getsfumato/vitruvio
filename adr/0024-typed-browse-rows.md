# ADR-0024: The browse row is typed, and its interpretation has one owner

## Context

ADR-0023 typed retrieval and gave a block's *name* one owner, `browse.identify`. It did not give the *row* one.
`browse.row` returned `dict[str, Any]`, `block_rows.project_rows` returned `tuple[list[dict[str, Any]], ... ]`, and
issue #74's third and fourth acceptance criteria — "the shared projection has one owner: browsing and retrieval do
not describe the same rows twice" and "the CLI and the TUI stop encoding nested-key knowledge independently" — were
met for the query path and not for the browse path. A match had a type; the row beside it did not.

The cost was already visible rather than hypothetical. `render/evidence.py` and `tui/app.py` each read the same row
by string — `title`, `resolvable`, `authorship`, `block_id`, `media_type`, `kind` — and the two had drifted in both
directions. The TUI's type column fell through to `content.media_type` and the CLI's did not; the CLI drew a
`detail` and a `size` the TUI had no room for. Drift in that direction is silent: both tables keep rendering, and
nothing says the same row is being answered two ways. That is exactly the failure ADR-0023 set out to end, left
standing in the half of the surface it did not reach.

## Decision

**The row is a `TypedDict`, under the payload regime.** `BrowseRowResult` declares the five keys every row carries
and fourteen that depend on the memory type. Those fourteen are `NotRequired`, for the reason `block_result` gives:
the row is projected from `Block.payload()`, so a field a memory type does not have is *absent* rather than `null` —
a `media_type` of `None` on a semantic block would suggest the field means something there.

**Authorship makes a second type, not an optional key.** `browse.row` never attaches authorship and
`block_rows.project_rows` always does, so `ProjectedRowResult` is `BrowseRowResult` plus `authorship` — the same
move ADR-0023 made for a compound match being its single-brain match plus `brains`. One type with the key optional
would describe neither producer.

**`cast` sits where mypy cannot follow the construction**, as ADR-0023 says. `browse.row` accretes its optional half
in a loop over field names held in variables, which no checker can relate to a `TypedDict`. That is one `cast` in
the function that builds the shape, rather than an untyped row reaching every reader. `block_rows` casts once more
at the authenticity boundary: `AuthorshipAudit` belongs to a domain #74 puts out of scope, so its shape is claimed
here and pinned by the recorded wire shape until that slice types it.

**`BrowseRowView` owns the interpretation, and the type column falls through.** The view carries `title`, `detail`,
`resolvable`, `size` and `media_label`, and both tables read it. Deciding one rule for `media_label` meant choosing
between the two the readers had drifted into, and the TUI's wins because it had a reason written down: a semantic
block whose datum is a 2 MB diagram has a media type the same way a canonical PDF does, and a blank column said
otherwise. So the CLI's type column gains that fall-through. A row's own `media_type` still wins where it has one — a
canonical block *is* its bytes, while a derived block only names some.

**`blocks` is declared `TYPED`.** `BlocksResult` is the page around the rows, and `installed` stays a key of its own
rather than being inferred from an empty list: a module absent from a selectively pulled brain and a module with
nothing matching are different facts, and a reader who cannot tell them apart goes looking for blocks that are not
missing at all.

**`UNNAMED` moves to `browse_result`.** The sentinel now sits with the type that declares the field it fills, so the
rule can import the shape it builds without a cycle. `browse` re-exports it; every existing reader is unaffected.

## Consequences

- Two of #74's acceptance criteria that PRs #78 to #80 left open are met, and the issue closes here rather than
  there.
- One thing a person sees changed, and it is the only one: the `type` column of `vitruvio inspect blocks` now shows
  the media type a derived block's content names where it was blank. Nothing pinned that column before, so a test
  does now — in both directions, including that a row's own media type still wins.
- The TUI's row state is typed end to end, which pulled a chain of `dict[str, Any]` parameters with it: `select`,
  `load_detail`, `_preview`, `_blob_preview`, `_content_preview`, `_keys` and `_reading`. That was the work, and it
  is the point: each was a place a key could be misspelt in silence.
- One test was asserting a state the runtime cannot produce. It set `origin` to `None` on a row to mean "no recorded
  file name", where `browse.row` omits the key entirely — the type said so, and the test now removes the key.
- Nothing moves on the wire. `ResultKind` is catalogue metadata and the row's contents are unchanged, so
  `tests/operation_shapes.json` is untouched.
- The remaining `dict[str, Any]` domains — authenticity, catalog, retention, sources, install — are unchanged and
  still pinned by the recording, and now have two worked examples of the idiom rather than one.

## What was rejected

**One row type with `authorship` optional.** It would have let a reader take a row from either producer without
knowing which, which is precisely the knowledge the type exists to carry: the CLI table subscripts `authorship`
because `project_rows` guarantees it, and that guarantee is what an optional key would throw away.

**Keeping the CLI's type column as it was and giving the view two rules.** A view with a per-caller rule is the
duplication moved rather than removed. One of the two behaviours had to win; the one with a stated reason did, and
the change is small, visible and now tested rather than silent.

**Typing `authorship` properly here.** It belongs to the authenticity slice, which #74 names as out of scope, and
reaching into it would have made this change span two domains to save one `cast` that is named and tested.
