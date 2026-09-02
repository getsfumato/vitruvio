# 15. Browsing

```bash
vitruvio browse                              # the interface
vitruvio browse --memory-type semantic       # open on a module

vitruvio inspect blocks canonical            # the same reading, as text
vitruvio inspect blocks semantic --contains fourier
vitruvio inspect content <DIGEST>            # what the bytes are
vitruvio inspect content <DIGEST> --out apunte.pdf
vitruvio inspect links <BLOCK_ID>            # where it came from
```

Everything up to here has been about *asking* a brain something. This chapter is about reading one — opening it
and looking at what is in there, which is a different question and has a different answer.

## Reading is not querying

`search` ranks. The planner picks indices, fuses their results and returns an Evidence Bundle with a score on
every match. That is what you want when you know what you are looking for.

`inspect blocks` lists a module in **its own order**, one line per block, and consults no index. There is no
score column because nothing was ranked. `--contains` filters the rows that were read — a substring over the
title, the detail, the subject, the tags and the identity — and it is bounded by the same `--limit` an unfiltered
page is.

The distinction is worth keeping straight, because a filter that looked like retrieval would be a second and much
worse retrieval path sitting next to the one with a cost model behind it. In the interface it is two different
things in two different places: the filter box narrows what is on screen, and `s` opens the query workspace.

A canonical block carries no name — its identity must not depend on what anyone called the file — so a canonical
row is titled by the **origin its registration recorded**, read back out of provenance. That is why a canonical
module reads as the files that went into it, and why a brain pulled without its provenance layer shows media
types instead.

## The interface

```
┌────────────┬─────────────────────────┬──────────────────────────┐
│ memory     │ filter …                │ preview payload links    │
│  canonical│ block title creator auth│ authorship          proof│
│ catalog    │ sha…  apunte… alex…  ✓  │ apunte.pdf               │
│  discipline│                         │                           │
│ …          │ sha256… pizarr… png     │ [the page, drawn]        │
└────────────┴─────────────────────────┴──────────────────────────┘
```

Modules on the left — **every** module, including the ones this brain does not have, because a module absent from
a selectively pulled brain is a fact about this brain rather than something to hide. What is in the selected
module or catalog folder in the middle. Every row names the actor asserted by the block's creation provenance and
whether that identity is verified by an accepted historical signature. The selected block is on the right, in five
tabs:

| tab | what it holds |
|---|---|
| preview | the bytes the block names, drawn if a terminal can draw them |
| payload | the block's document, as JSON. What it *is*, exactly |
| links | the provenance records naming it: registration, derivation, supersession, removal |
| authorship | who created it, assistance, the introducing snapshot, signatures, trust root and consumer pin |
| proof | its Merkle inclusion proof, already checked against the module root |

`verified` is deliberately narrower than “the bytes are valid”: it means an accepted signature on the snapshot that
introduced the creation provenance vouches for the same actor subject. `asserted` means provenance names an actor but
that cryptographic link is absent or not accepted. The proof tab remains the independent integrity check.

"The bytes the block names" is not only canonical: a derived block may carry its own datum out of line — a
semantic block whose `content` names a rendered diagram, an episodic one naming a recording. Its preview shows
both halves, text first, because the statement is what the block claims *about* the bytes and is the only part a
query can reach; the bytes are drawn beneath it. `o` and `e` act on those bytes exactly as they do on a canonical
block's, and content this brain does not hold — a selective install — is reported under the text rather than
replacing it.

**Which brain am I looking at?** Several layers select one and only `--brain` is visible in the command you typed,
so the header names the brain and the layer that chose it (`facultad/analisis-numerico by flag`, `demo/brain by
state`), and `i` prints the whole path, the snapshot, which modules are installed and who writes are attributed
to. The project is part of the short form because two projects each holding a `metrica-a` is the ordinary case,
and the path alone does not say which one you got. `vitruvio brain state` answers the same question outside the
interface.

**Reading another project's brain.** `p` opens a picker: projects on the left, that project's brains on the right.
Choosing one retargets the whole interface in place — no quitting, no second `vitruvio browse` with different
flags. The two panes are one decision, in the same order the CLI resolves them: a brain name only means something
inside a project, so moving the project cursor refills the brains beside it.

```
┌────────────────────────┬────────────────────────────────────────┐
│    project     brains  │    brain             state  description│
│ *  eticompass  2       │ *  metrica-a                …          │
│    facultad    3       │    metrica-b                …          │
└────────────────────────┴────────────────────────────────────────┘
```

`*` is the brain you have open, in both columns — half of what you open this screen to ask is *where am I*, and the
cursor cannot answer that, because it moves as soon as you start looking around. The list is every project
`--project` accepts, so `vitruvio project register` is what puts one in it. `escape` keeps the brain you had.

This is also what `vitruvio browse` does when **no** brain was selected at all: it asks, instead of printing five
flag names at somebody who is trying to look at something. It is the one command that does — every other one
still refuses, because a non-interactive caller cannot answer a question.

**Moving around.** The cursor starts in the blocks, because that is what you came to read: up and down walk the
evidence. `left` (or `m`) goes to the sidebar, landing on the module you are already in; `right` or `enter` comes
back into the blocks. In the sidebar, moving the cursor **opens** that module — there is no second keystroke to
confirm. `tab` cycles the panes.

The catalog appears below the memory modules as folders: scheme, class hierarchy and canonical source. A broad class
includes sources placed in descendants, while `unclassified` makes evidence outside every placement visible. On a
canonical row, `c` opens an interactive class selector. Existing append-only placements are locked; exclusive schemes
permit only one direct placement. The interface dry-runs every change first. For a governed brain it then requires an
active `commit`-scoped key from `ssh-agent`, asks which eligible key to use and signs the new snapshot. If signing
fails after the catalog commit, the alert prints the exact `vitruvio auth sign ... --snapshot ...` recovery command.

Other keys: `/` filter, `s` search, `p` project and brain, `c` classify a canonical source, `t` swap between original bytes and their normalized
text view, `]` and `[` turn PDF pages, `o` open in whatever the desktop uses, `e` export into the working
directory, `y` copy the block id, `n` and `b` page through a large module, `r` re-read, `?` every binding.

`browse` needs a terminal and refuses `--json`. It has no output mode — the three `inspect` commands above are
the same three reads with an envelope, which is what an agent should drive.

## Seeing how a query ran

`s` opens a query workspace inside `browse`. The query and optional RFC3339 time window sit at the top;
results stay on the left; the chosen physical plan and its visual evidence stay on the right. A graph expansion
depth is explicit because following one edge and following three are materially different queries. A blank depth
means one hop, zero disables expansion, and the planner caps the request at the project's `graph_expand_max`.

The workspace asks for at most 25 matches and does not page them. They are the first ranked matches, not proof that
the brain holds no others; the status says `more may exist` when the Evidence Bundle reports truncation.

The **plan** tab names every operator and, per module, the indices the planner actually consulted. “Available” is
not the same as “selected”: an installed vector index may lose to an exhaustive scan on a small module, and the UI
says so instead of drawing a vector view that did not participate.

The other tabs are conditional views over the same execution:

| tab | what it draws |
|---|---|
| graph | up to 40 real typed edges per consulted graph scope touching the returned neighborhood |
| vectors | the query and up to 20 returned block vectors per scope, projected to 2D with PCA |
| B-tree | up to 25 ordered values around the actual `bisect` window used by `RangeScan` |

The vector coordinates show relative geometry, not match scores. Vitruvio's B-tree role is implemented by sorted
parallel arrays because whole-module rebuilds make pointer pages unnecessary; the tab names that engine and draws
its ordered spine rather than inventing stored tree nodes. Hash lookup is still named in the plan when selected,
but has no dedicated diagram: a dictionary probe has no useful internal geometry to visualize. Every diagram is a
bounded inspection view, so a dense neighborhood or wide range can extend beyond what fits in the panel.

Choosing a result returns to the reading view and reveals that exact block. `escape` returns without choosing.

## What a preview can and cannot show

Previews route on the media type the **block** carries, never on sniffed bytes. The block says what it is, that
claim is part of its identity, and a viewer of a verifiable store must not contradict it.

| bytes | shown as |
|---|---|
| `text/*`, JSON, YAML, TOML | themselves; Markdown rendered, code highlighted |
| `image/*` | half-block graphics, two pixels per character cell |
| `application/pdf` | the page, rasterized, one at a time |
| video, audio, anything else | its metadata, and `o` hands it to the desktop |

With `--out` the same command writes the bytes somewhere permanent instead.

Images and PDF pages need `vitruvio[vision]` — Pillow and pypdfium2. Without them the preview says which extra
would draw it rather than pretending there is nothing there.

**A preview is a thumbnail, and that is a ceiling rather than a defect.** Each character cell carries two pixels,
so a page in a 60-column pane is 60x80 pixels for the whole page — enough to see where the diagram is, not enough
to read the caption. The stored bytes are untouched: a canonical block is *named by their hash*, so nothing is
recompressed or downscaled at rest, and `inspect content --out` gives you back a file identical to the one you
registered. What is bounded is the drawing.

To actually read it, in order of usefulness:

- **`t`** — the normalized text view, if the block has one. Real text, reflowable and searchable. A PDF only has
  one when it was registered with `--normalize-with pdf-text`, or through `ingest run`; a plain
  `source register file.pdf` stores the bytes and extracts nothing.
- **`o`**, or `inspect content DIGEST --open` — hands the bytes to whatever this desktop opens PDFs with, at full
  resolution. Not a web browser: `open`, `xdg-open` or `startfile`, so a video goes to a player and a spreadsheet
  to a spreadsheet program. The temporary file is named after the origin, so the viewer's title bar says
  something.
- **More cells** — a smaller terminal font is linearly more resolution.

Sixel and the terminal-specific image protocols are deliberately not used. Each looks better in the one terminal
that implements it and prints garbage in the rest, and a viewer that shows garbage half the time is not a viewer.

## Content is not evidence

`inspect content` takes a **content address** — a block's `blob`, or its `normalized_view.blob` — and not a block
identity. `inspect blocks` prints both, and the difference is the protocol's: the block is the knowledge-level
statement that certain bytes were incorporated, and other blocks cite *it*. The bytes are what that statement is
about.

So exporting content copies it out; it changes nothing and cites nothing. And a digest the store cannot produce
is an error rather than empty bytes, because empty bytes are indistinguishable from an empty file.

## What a row tells you when it cannot be read

A block can be a verifiable member of a version and still not be readable: tombstoned by a redaction under an
erasure policy, or never installed by a selective pull. Both still appear, marked, in the list and in the
interface.

That is not politeness about edge cases. A viewer that dropped those rows would make a redacted brain look like a
smaller one, and the protocol is explicit that lawful erasure must stay distinguishable from a corrupt store.
`inspect resolvability` counts them per module.

The reasoning behind the interface, including what was rejected, is
[ADR-0012](../adr/0012-the-terminal-interface.md).
