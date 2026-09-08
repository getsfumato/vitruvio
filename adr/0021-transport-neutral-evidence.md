# ADR-0021: Evidence arrives as bytes, and an origin is never invented

## Context

Four operations took a `Path`, opened it themselves, and — when no origin was given — recorded the absolute path
they had opened as the block's origin: `register`, `replace`, `put_content`, `ingest_run`. Two more named a path
on the way out: `content` returns raw bytes, and `export_content` created parent directories and replaced an
existing target *by default*.

The origin is the sharp end. It is provenance: permanent, part of what a consumer audits, and the key the pull
path looks up to answer "already have this?". A caller that is not a process on this machine has no meaningful
path here, and defaulting one on its behalf writes a fact about *this* filesystem into somebody else's brain.

The SDK never asked for any of it. `Brain.register` takes `bytes`, and so does everything under it. The `Path`
was a vitruvio invention, and the seam that does this correctly already existed one directory over: a declared
source is listed as an `Item` whose origin is *required* because it is the dedup key, fetched into a
`FetchResult` carrying bytes and a media type, and registered through `FetchOps._register_bytes`. `source pull`
has never seen a caller's path.

The asymmetry ran the other way too. `BaseSource.contain` has refused symlinks, escapes from the declared
directory, non-regular files and oversized files since ADR-0011. Manual registration refused a file that did not
exist, and nothing else — while `ingest run` did not even do that, so a typo surfaced as an `OSError` from inside
the write transaction. The path a *person* types is the one with a symlink in it.

## Decision

**The runtime takes `Evidence`, not a path.** `Evidence` lives in `vitruvio.ingest` beside `Item` and
`FetchResult`: `data`, `media_type`, `origin`, and the optional `license`, `retention_policy` and
`normalize_with`. `origin` is a required field. `Evidence.from_bytes` is the constructor for a caller that holds
bytes; `Evidence.from_path` is for one that holds a path, and it applies the containment policy before reading.
Turning a path into bytes is the adapter's job, and the CLI is where it happens.

**The four refusals have one owner.** They move out of `BaseSource.contain` into
`vitruvio.ingest.evidence.contain`, which `BaseSource` calls and re-raises from — a source names itself in the
message and reports at the source exit status, because "this declaration is unusable" and "you pointed at a FIFO"
are different things for a caller to know. The refusal for evidence is `EVIDENCE_REFUSED`, exit 2: nothing about
the brain forbids it, the caller named the wrong thing, and naming a different one works.

**A brain may declare a ceiling.** `[ingest] max_bytes`, named as `SourceSpec.max_bytes` is because it is the
same declaration about the same thing. Unset by default. Checked against `stat()` before a file is read, and
against `len()` in the operation, so evidence assembled in memory cannot exceed what evidence read from a file
may not.

**Output says who chose the destination.** `export_content` defaults to **refusing** an existing target and
accepts `within` to bound where a destination may land. Overwriting is right for exactly one caller — a person
who typed `--out` — and wrong for every caller that *derives* a destination, which is the shape issue #19 took.
The browser's export passes `within=Path.cwd()`, because the filename it derives comes from the origin recorded
in the block, and a pulled brain's origins are somebody else's text.

**`content` stays bytes, and gains a bounded sibling.** Base64 inside an envelope would be a different operation
with a different cost, as its docstring has always said. `content_range(digest, offset, length)` is that other
operation, declared for what it is: a window, plus the total size, for a caller that cannot be handed a file.

## Consequences

- `register`, `replace`, `put_content` and `ingest_run` stop being `Remote.LOCAL` in the operation catalogue.
  That is the observable result: four operations that could not be offered to a caller elsewhere now can be.
  `export_content`, `plan_migration`, `migrate` and `bench` remain local, and the catalogue test asserts it.
- Four operation signatures changed. The facade regenerates itself and the CLI is the only production caller,
  but every test that registered a file had to move to `Evidence.from_path`, which is most of the churn here.
- A missing file is now exit 2 rather than exit 1. It was reported through a bare `VitruvioError`, which the exit
  code table documents as "always a bug in vitruvio" — telling a user their typo is our bug.
- `Evidence.from_path` keeps `str(path)` as the derived origin rather than `path.as_uri()`, though the latter is
  what `DirectorySource` records. Changing what goes into provenance is not a refactor, and the two disagreeing
  is a separate decision with existing brains on the other side of it.
- The declared ceiling is enforced in two places on purpose. The adapter's check is what stops a large file being
  read at all; the runtime's is what makes the ceiling a property of the brain rather than of whoever called it.

## What was rejected

**Keeping the `Path` overloads alongside the new ones.** Cheaper for the tests and it leaves the origin default
in place, which is the defect. Two ways to register is also two answers to "what is the origin of this".

**Base64 in the ordinary `content` result.** It would make one operation serve both callers by making it wrong
for the one it already serves: a preview needs bytes, and a whole video does not belong in a JSON envelope.
