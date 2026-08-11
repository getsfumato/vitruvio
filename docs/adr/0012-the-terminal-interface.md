# ADR-0012: The Terminal Interface, And One Renderer For Both Of Them

**Decision status:** Accepted, implemented after M11.

## Context

Everything the CLI printed for a person was assembled with `f"{name:<12}"` and rules drawn out of hyphens. That
worked, and it stopped working quietly: `root` was eighteen characters wide in `brain info` and twelve in
`inspect roots`, so a reader comparing the two was comparing two layouts rather than two brains. There were
nineteen hand-aligned label-and-value blocks with three different label widths, and no answer anywhere to "what
colour is canonical", because nothing was coloured.

Separately, and more importantly: **there was no way to read a brain.** Forty commands could answer questions
about one — its roots, its statistics, whether it verified — and none of them could show what was in it. A
canonical module of registered PDFs was a column of digests. `search` was the only way to see any content at all,
which meant the only way to look at your own evidence was to think of a query that would rank it highly. For a
brain whose whole purpose is preserving material you will come back to, that is the wrong shape.

The material that motivated it is the [facultad](0010-projects-and-derived-repositories.md) case: six brains, one
per subject, holding lecture PDFs, photographs of blackboards, transcripts. The question a person actually has is
"what is in analisis-ii", and it had no command.

## Decision

### 1. Human output goes through Rich, and only human output

One theme, one console factory, and four shapes — a table, a label-and-value block, a styled verdict, a digest —
declared once in `vitruvio.cli.render.theme` and used by every command. Colour is meaning and never decoration:
a memory type always gets the same colour, a digest is always dim, a verdict is green or red and nothing else is.
Rich's automatic highlighting is **off**, because in a tool whose output is digests, media types and paths it
colours most of every line for no reason, and colour that carries no meaning is what makes the colour that does
carry meaning invisible.

The seam is `Console.emit(command, data, view=...)`. A command hands over a renderable; `emit` decides whether
anything is drawn. In `--json` mode nothing here is reached, the Rich console is never even constructed, and the
envelope is byte-identical to what it was before there was a renderer. That property is tested rather than
asserted: a renderable printed into a JSON stream would look fine to a person reading a terminal and would be
fatal to the agent parsing it.

`lines=` survives for output that genuinely is lines — a generated completion script, `config get`'s bare value,
the planner's `explain` tree, and the cascade plan `drop` prints to *stderr* before it asks for confirmation.
Wrapping those in a renderable would be ceremony, and in `config get`'s case it would break the shell variable
somebody is assigning it to.

### 2. `vitruvio browse` is a second interface over the service layer, not a second implementation

Textual, and the same shape a note-taking application uses: modules, then their blocks, then the selected block.
It holds **no protocol logic**. Every pane calls `BrainService`, exactly as a command body does, and renders what
comes back with the renderers the CLI uses — which is why the theme is pushed onto Textual's own Rich console at
mount rather than duplicated as Textual CSS.

That constraint forced three methods into the service instead of into the interface, which is the outcome
[ADR-0003](0003-the-service-layer.md) wanted: `blocks()` pages a module as rows, `content()` returns the bytes a
block names, and `related()` reads the provenance records naming a block. The future MCP server gets all three.
`inspect blocks`, `inspect content` and `inspect links` are those same three reads with an envelope, which is what
an agent drives — `browse` refuses `--json` and refuses a stdout that is not a terminal, because a TUI has no
output mode and drawing control codes into a file is not a fallback.

### 3. Reading a module is not querying it, and the interface says so twice

`blocks()` returns a module in its own order with no score, and its filter is a substring over rows already read.
It names no index and cannot rank. Retrieval is `search`, where a cost model chooses the plan
([ADR-0005](0005-statistics-and-the-cost-model.md)).

Collapsing the two would have been easy and is the mistake this rejects. A filter box that quietly performed
retrieval would be a second, much worse retrieval path living inside the interface people spend the most time in
— no cost model, no verification, no explanation — and every result it produced would look exactly like a result
from the one with those things. So they are two different affordances in two different places: the filter box
narrows the rows, and `s` opens a search screen that says on it that a score is agreement between retrieval
strategies rather than a probability.

The cost of that separation is a user who types in the filter box expecting semantics and gets nothing. The
placeholder text says "this is not a query", which is the smallest honest version of the fix.

### 4. A preview routes on the block's media type, and draws with half-blocks

The block says what its bytes are; that claim is part of its identity and part of what was verified. Sniffing the
bytes instead would mean a preview that contradicts the evidence, which is the one thing a viewer of a verifiable
store must not do — so a mislabelled blob previews as what it claims to be, and looks wrong, which is the correct
outcome.

Images and PDF pages are drawn with the upper-half-block glyph: two pixels per character cell, foreground and
background. It needs no terminal capability negotiation and it works inside a Textual widget. **Sixel and the
iTerm2 and Kitty image protocols were rejected**: each looks better in the one terminal that implements it and
prints garbage in the rest, and a viewer that shows garbage half the time is not a viewer. Video, audio and
anything else a terminal genuinely cannot show report their metadata and offer to hand the bytes to the desktop.

Pillow and pypdfium2 stay behind `vitruvio[vision]`, where they already were. Without them a preview names the
extra that would draw it. Silently falling back to "no preview available" would leave a reader thinking the brain
held nothing.

### 5. A preview is a thumbnail, and the way out is the desktop's own handler

Two pixels per character cell means a page in a 60-column pane is 60x80 pixels for the whole page. That is a
resolution ceiling and not a filter to tune: rendering at scale 2 and downsampling with Lanczos, asking pdfium for
the target size directly, and averaging down from scale 4 measure 2.38, 2.39 and 2.38 on the same page. The
preview shows where the diagram is; it cannot show the caption.

The first user question this produced was whether the brain had *stored* the PDF at that resolution. It has not,
and it structurally cannot: a canonical block is named by the hash of its bytes, so recompressing them would
change the block's identity and break the module root. The rasterization happens at draw time and is thrown away.
That the question came up at all is a sign the interface should say what it is doing, which is why the guide states
the ceiling and the export path in the same paragraph.

So the interface hands the bytes out, and it hands them to `open` / `xdg-open` / `os.startfile` -- **not**
`webbrowser`, which is where this started. `webbrowser.open("file://...")` on macOS opens the file in Chrome: for
a PDF that is not what anyone meant, for a `.mp4` or an `.xlsx` the browser is the wrong application, and for a
type it cannot handle it offers to download a file the user already has. The platform openers consult the handler
the *user* configured, which is the only correct answer to "open this".

Three consequences of writing bytes out to be opened. The temporary file is named after the origin the
registration recorded, because a viewer whose title bar reads `content.pdf` has discarded the only context there
was. Each open gets its own temporary directory, because two blocks legitimately share an origin filename -- two
editions of the same paper is the ordinary case -- and one shared directory would replace what the first viewer
still had open. And a machine with no opener says so and reports where the bytes are: over SSH with no display
that path is the whole answer rather than a degraded one, and `webbrowser` failed there silently.

`vitruvio inspect content DIGEST --open` is the same path outside the interface, so the answer does not require
the TUI.

### 6. Textual is a hard dependency; the import is not

`vitruvio browse` is not a side feature, and an interface that is only present when somebody guessed the right
extra is an interface nobody finds. Textual is pure Python and small — the opposite of torch, which is why *that*
is an extra.

What stays true is the import discipline [ADR-0001](0001-monorepo-layout-and-package-seams.md) exists for: nothing
under `vitruvio.cli.tui` is imported until the `browse` body runs, so `vitruvio config show` still starts in tens
of milliseconds. The stylesheet is inline in the app class rather than a `.tcss` file, because hatchling drops
non-Python files from a package directory unless they are named as artifacts, and a stylesheet that shipped
missing would be a completely unstyled interface.

### 7. The interface is about a brain it can change, not a brain it was given

Added in M4. `p` opens a picker — projects on the left, that project's brains on the right — and choosing one
retargets the whole interface in place.

The interface used to be handed one resolved service and could never be pointed anywhere else, so "read the other
subject" meant quitting and re-running `vitruvio browse` with different flags. That is untenable once somebody
keeps a project per subject or per client, which is what [ADR-0010](0010-projects-and-derived-repositories.md)
recommends: reading across two of them is the normal case rather than an exotic one.

Two rules keep it from becoming a second selection mechanism. The panes are **one decision in the CLI's order** — a
brain name only means something inside a project, so moving the project cursor refills the brains — and the choice
is resolved by `vitruvio.kernel.resolve`, so a picked row opens exactly the brain `--project x --brain y` would.
The interface must not be a place where a brain can be selected by rules the CLI does not share.

It is also why `browse` is the one command that treats an unselected brain as a question: it opens the picker
instead of failing. A list is a better answer than five flag names to somebody who is trying to *look* at
something. Every other command still refuses, because a non-interactive caller cannot answer a question.

## Consequences

**Two things had to be added after the first person used it, and both were the same mistake.** The cursor started
in the module sidebar, whose entries are all leaves, so the arrow keys appeared to do nothing and no key led to the
next pane -- the sidebar was a room with no door. And the header showed the brain's path but not which precedence
layer chose it, so a bare `vitruvio browse` opened *something* and the interface could not say what.
Both were the interface knowing something and not showing it: the fix is that the cursor starts in the blocks,
`left`/`m` and `right`/`enter` cross between panes, moving the sidebar cursor opens that module, and `i` reports
the brain, the layer that selected it, the snapshot and the actor. A worker that raises is a related failure of the
same kind -- Textual re-raises `WorkerFailed` and the application ends -- which one canonical block whose bytes
were not the image its media type claimed was enough to trigger.

**Every read in the interface runs on a worker thread.** Resolving a page of blocks reads and hashes each one, and
a canonical module of scanned PDFs makes that take a visible moment; on the event loop it read as a hang. An
exclusive worker group also means the fourth arrow-key press cancels the three previews nobody is waiting for.

**A page is bounded at two hundred rows.** A page is *read*, not indexed, so an unbounded module would be an
unbounded wait. `truncated` says what was not returned, in the envelope and on screen.

**`blocks()` and `related()` treat an uninstalled module as empty rather than as an error.** A selectively pulled
brain is missing modules on purpose, and the tree lists all five with a dash against the absent ones. A block that
cannot be read — tombstoned under an erasure policy, or never installed — still appears, marked, for the reason
[ADR-0009](0009-retention-and-the-five-mechanisms.md) gives: lawful erasure has to stay distinguishable from a
corrupt store, and a viewer that dropped those rows would make a redacted brain look like a smaller one.

**Human rendering now wraps to the terminal's width.** Piping human output to a file wraps it at eighty columns,
where hand-built lines did not. That is accepted rather than worked around: `--json` is the interface for anything
that is not a person, and the alternative — a renderer that guesses when it is being piped — is how output ends up
with two behaviours nobody can predict.

**Nothing is summarised.** The interface draws blocks, bytes, records and proofs. There is no pane that says what
a brain is "about", because that would be the CLI having quietly become the model — the same line
[ADR-0004](0004-output-contract-and-exit-codes.md) draws about the absence of an `answer` field.
