# ADR-0011: Declarative Sources, Plugins, And Dedup Without A Cursor

**Decision status:** Accepted, implemented after M10.

## Context

Until now the only way into a brain was `vitruvio source register <file>`: acquisition was manual, one file at a
time. The material that motivated this does not arrive that way. A faculty platform publishes a new practical every
week (reachable through the user's own `aulasvirtuales` CLI), a browser extension drops arXiv PDFs into a folder, a
transcript exists per video. Each is a different acquisition mechanism and the next one will differ again.

`origin` already existed on every `RegistrationRecord` — written by the runtime since M1 and **read by nothing**.

## Decision

### 1. The configuration names a kind and cannot define one

An earlier draft of this let `vitruvio.toml` declare `list = "aulasvirtuales list --json"`. That is the obvious
design and it is wrong, because `vitruvio.toml` is committed: it would mean cloning a repository and running
`source pull` executes a stranger's command line. Mitigating that needs a trust-confirmation ceremony — a prompt, a
recorded consent, a fingerprint of the file — and every such ceremony is one users learn to click through.

So the threat is removed instead of managed: **there is nowhere in `SourceSpec` to put an argv.** A kind is a Python
class, resolved from one of two places:

- **A local script** — `$XDG_CONFIG_HOME/vitruvio/sources/*.py`, each defining a `BaseSource` subclass with a `KIND`.
- **An entry point** — the `vitruvio.sources` group, for something worth distributing.

Both are code *you installed on your own machine*, at the trust level of a shell profile. That is a different thing
from code that arrived with a `git clone`, and it is the whole distinction this decision buys. It is the same
structural move `config.py` already makes for secrets — "there is nowhere in this schema to put one" — and a test
asserts the absence of an argv-shaped field so nothing adds one by accident.

A plugin **overrides** a built-in kind of the same name, because the machine's owner is the one who put the file
there. Loading is lazy and per-command, so a broken plugin cannot break `vitruvio brain state`, and an import
failure names the file and the exception rather than emitting a traceback through `importlib` — the reader's next
action is to edit that file.

`options` is untyped, and that is not laziness. A discriminated union is right for a closed set and this set is open:
vitruvio cannot know a third-party plugin's fields. Validation happens in the subclass constructor, the only code
that does know. `path` stays first-class rather than living in `options`, because a relative path must resolve
against **the configuration file's directory**, and that is a kernel rule a plugin author must not have to remember.

A named `source pull` may supply repeatable `--option key=value` overrides. The runtime merges them over the
declaration into an ephemeral `SourceSpec`, constructs the kind from that copy, and never rewrites
`vitruvio.toml`. This lets one locally installed kind and one generic declaration serve several explicitly selected
brains without turning invocation state into project state. `--all` refuses overrides: applying one kind's fields to
every declared source would be ambiguous. A declaration that pins `brain` remains pinned; overrides do not weaken
the conflicting-brain refusal.

Because origin dedup runs before fetch, a kind must project every option that changes remote identity into
`Item.origin`. `aula://<course>/<resource>` is safe; `aula://<resource>` is not when two courses may reuse ids.

Some listings do not reveal the real filename or MIME type until a download redirects to the file. `Source.fetch`
therefore accepts either bare bytes or `FetchResult(data, media_type, title)`. The latter carries metadata learned
at acquisition time without making `list()` download content or making `--dry-run` impure. Configuration remains
authoritative: a declared `media_type` wins over both listing and fetched metadata.

### 2. `BaseSource` exists to supply five bounds, not to save typing

A source is the third kind of thing in `vitruvio.ingest`, and its rule is neither of the other two's. A pipeline must
be deterministic because its output is content-addressed evidence. A proposer may be a model because its output is a
proposal the gate judges. **A source is I/O against a world that changes**: it may fail, it may answer differently
tomorrow, and it may never be trusted with an unbounded operation.

Every bound below was chosen because its absence fails as a *hang with nothing on screen* — the most expensive
failure mode there is, because there is no message to search for:

- `stdin=DEVNULL`. A tool with an interactive selector otherwise waits forever. This is the same incident ADR-0007
  documents about ORAS and credential helpers, in a new place.
- A per-source timeout, defaulting to 300s rather than the credential helper's 5s. A source is allowed to do real
  work: `aulasvirtuales download-all --ocr` runs a vision model over a course. The bound exists to kill a hung
  fetch, not a slow one.
- `text=False`. Decoding corrupts every PDF that passes through.
- A `VITRUVIO_*`-stripped environment. A source shelling back into vitruvio while inheriting `VITRUVIO_BRAIN` writes
  into the brain that is pulling it, and those blocks are hard to tell from legitimate ones afterwards.
- Path containment: refuse a symlink, refuse anything outside the root, refuse a non-regular file, and check
  `stat().st_size` against `max_bytes` **before** the read. The non-regular-file check is not theoretical:
  `read_bytes()` on a FIFO blocks forever and a glob hands you one without comment.

`DirectorySource` ships alone. `http`, `playwright`, `arxiv` and `youtube` are each one class behind one extra once
the seam exists, and a bundle of half-tested kinds is a worse starting point than an honest single one plus
`source scaffold`. It is also the *second half* of most other sources: a tool that materialises files becomes a
plugin whose `list()` refreshes a directory and delegates.

### 3. Dedup is three layers and persists nothing

**No cursor.** A cursor would be the first thing under `.vitruvio/` that is neither derived nor rebuildable, and the
whole reason that directory can be deleted safely is that everything in it can be regenerated.

**Layer 1 — the origin index.** `IdentityKey.ORIGIN` makes "have I acquired this?" one hash-map probe on an index
vitruvio already builds for every module. It runs before the fetch, which is what makes a repeated pull cheap rather
than merely idempotent. The cost is `PROJECTION_ID` → `vitruvio-projection/2`: the identifier is in every index
header and inside every vector index's model tag, so every existing brain needs `index build` again and every
published brain a re-push. Paid now, with four toy repositories in the world, because it only gets more expensive.

An origin hit is compared against the declaration before it is trusted. Identity is
`(blob, media_type, size, normalized_view)`, so skipping on the origin alone would break the two corrections users
most legitimately make — fixing a `media_type`, or adding a `normalize_with` — by making them do nothing at all,
leaving the block that was meant to be fixed still wrong. Caveats for the reader: `fold()` case-folds keys, so two
origins differing only in case collide (worst case a spurious skip); an origin carrying a session token is unstable
and the *source* must canonicalise it; and a dropped block's registration record can survive, so a hit means
"already decided about this" — overridable with `--refetch`.

There is one conservative exception to the cheap origin hit. If neither the declaration nor the item listing knows
the type, an existing `application/octet-stream` block is fetched again: a newer source may now return a specific
type in `FetchResult`. After that correction, the specific held type makes origin dedup cheap again. A truly unknown
binary remains generic and is re-fetched; correctness and a usable viewer are preferable to treating ignorance as
stable metadata.

**Layer 2 — the redaction guard, which is a safety property and not an optimisation.** `Brain.register` calls
`store.put_bytes(data)` **before** its duplicate check, and `OciLayoutStore.has` returns `True` for a **tombstoned**
digest while `tombstone()` unlinks the file. So re-fetching redacted bytes writes the destroyed bytes back onto disk
and then quietly reports `duplicate=True`. A scheduled `source pull` is precisely the machine that would silently
undo `retain redact` — the command whose own docstring says it is for personal data, credentials and licensed
material. The guard therefore lives *after* the fetch, where `--refetch` cannot bypass it, and `--refetch` does not
override it. To be reported upstream as an SDK bug; until then, nothing here may assume `register` is safe on
these bytes.

**Layer 3 — content addressing.** Identical bytes, same block identity, `duplicate=True`. The backstop for any source
that cannot produce a stable origin: one wasted download, never a wrong result. `Item.digest` is optional and its
docstring says `None` is the *normal* case — Moodle's `contenthash` is SHA-1, an HTTP `ETag` is not a content hash,
and a transcript has no digest until it is fetched.

### 4. `pull --all`'s loop lives in the service

`dist push --all`'s equivalent sits in the CLI and is already the thinnest part of the service-layer boundary
(ADR-0003). Repeating that here would mean the future MCP server reimplements "which failures are fatal", and a
second implementation of that question is a second set of answers. Per-source failures accumulate; per-item failures
accumulate inside one source. A source's declared brain wins over `--brain` and a conflict is an **error**, because
registering one subject's material into another brain is the worst outcome available and content addressing has no
undo for it.

A source with no declared brain instead requires the ordinary explicit selection in a multi-brain project. That is
the intentional reusable case: `--brain simulacion source pull aula --option course_id=30030` and another invocation
may select another brain and course without mutating the declaration.

### 5. `ExitCode.SOURCE = 11`, and `UsageError`

Not `REGISTRY` (9), whose docstring scopes it to publishing; not `CONFIG` (3), which means the declaration itself is
wrong. What a caller does about the three differs — wait and retry, edit a file, fix a credential — and collapsing
them makes an agent guess. Mapped explicitly to HTTP 502 in `mapping.py`, or the `.get(…, 500)` fallback would report
an unreachable source as an internal error.

Building this surfaced an existing gap and it is fixed rather than propagated: a *semantic* usage error raised by our
own code (two contradicting flags, a name the project does not know) came out as a bare `VitruvioError`, and
`ExitCode.INTERNAL` is documented as "always a bug in vitruvio". Telling a user their typo is our bug costs them a
real investigation. `UsageError` carries `ExitCode.USAGE`.

`report_for`'s `retryable` short-circuit is deliberately **left alone**. It is read by nothing — not the envelope,
not `main()` — and weakening a documented invariant to set a field no consumer reads is a bad trade. It gets
revisited in the same commit as its first consumer.

## Consequences

- **Verified by running the CLI**, not by reasoning: a directory source registering two files, a second pull
  skipping both without reading them (proven by the origin layer, and the report says so), a third registering only
  the newly arrived file, a hand-written plugin scaffolded → edited → declared → pulled, `--all` continuing past a
  broken source and exiting 11, and — the one that matters — `retain redact` followed by `pull --refetch` leaving
  resolvability unchanged.
- **The redaction-guard test was confirmed to fail with the guard disabled**, not merely to pass with it. Without
  the guard the item comes back `duplicate` and the bytes are back on disk.
- **Everything a source registers lands in canonical memory and nowhere else.** Interpretation stays `task` /
  `ingest run`. A source cannot propose semantic blocks, which keeps the proposer boundary (ADR-0008) exactly where
  it was.
- **A `directory` source composes with `dist push` into a way to publish something nobody meant to.** Point one at
  the wrong folder and a private key becomes a canonical block in a public repository, which cannot be cleanly
  retracted. The containment rules are the mitigation and `source add` warns when a path leaves the project
  directory — but this is a real edge and it is named here rather than left in a docstring.
- **No scheduler.** `source pull` is a command; cron is the schedule. What made that safe to say is the redaction
  guard, because "run this on a timer" is only a reasonable suggestion if a timer cannot resurrect destroyed bytes.
