# 14. Sources: declaring where material comes from

Chapter 3 registered evidence a file at a time. That is right for a paper you just read and wrong for material that
keeps arriving: a course platform that publishes a new practical every week, a browser extension dropping arXiv PDFs
into a folder, a transcript per video.

A **source** is a declaration of where material comes from. `vitruvio source pull` acquires from it and registers what
is new into **canonical** memory. Interpretation is unchanged and still separate — `vitruvio task` and
`vitruvio ingest run` are where a model gets to say what the evidence *means*.

```console
vitruvio source add papers --kind directory --path ~/Downloads/arxiv --option glob='*.pdf'
vitruvio source pull papers --dry-run     # what it would take, fetching nothing
vitruvio source pull papers               # registers
vitruvio source pull papers               # skipped by origin; nothing is fetched
```

```toml
[sources.papers]
kind = "directory"
path = "~/Downloads/arxiv"
normalize_with = "pdf-text"

[sources.papers.options]
glob = "*.pdf"

[sources.algebra-aula]
kind = "aulasvirtuales"        # a plugin you wrote
brain = "algebra"
options = { materia = "77" }
```

## Defaults in the declaration, overrides on one pull

`options` is the open, kind-specific part of a source. The declaration provides defaults, and a named pull may
override them for that invocation without rewriting `vitruvio.toml`:

```console
vitruvio --project facultad --brain simulacion source pull aula \
  --option course_id=30030 --option 'sections=Teoría|Bibliografía' --dry-run
vitruvio --project facultad --brain fisica source pull aula \
  --option course_id=30110 --option 'sections=Teoría|Bibliografía' --dry-run
```

The kind receives the merged values through `self.options`; command-line values win. Its constructor validates the
result exactly as it validates declared options. Values are parsed as booleans, integers or strings by the same rules
as `source add`.

This is the reusable-source shape: declare `aula` without `brain`, then select the destination brain explicitly on
every pull. A source that declares `brain = "simulacion"` remains pinned to it, and a conflicting `--brain` is still
refused. `--option` cannot be combined with `--all`, because one override set has no unambiguous meaning across
different kinds.

Overrides are acquisition parameters, not hidden state. A plugin must include every value that changes a remote
item's identity in that item's stable `origin` — for example both `course_id` and `resource_id`. Otherwise two
courses that reuse an id could cause a false origin skip.

## The config names a kind and cannot define one

There is nowhere in `vitruvio.toml` to put a command line. That is deliberate, and it is the reason this feature has
no trust-confirmation ceremony: `vitruvio.toml` is committed and shared, so a field holding an argv would mean that
cloning a repository and running `source pull` executes a stranger's command.

A kind vitruvio does not ship is a **Python class you install**, either as a file under
`$XDG_CONFIG_HOME/vitruvio/sources/` or as a `vitruvio.sources` entry point. Importing a module you wrote from your
own configuration directory is code execution at the same trust level as your shell profile. Executing an argv that
arrived with a `git clone` is not, and no prompt makes it so.

```console
vitruvio source kinds        # directory (built-in), plus anything you installed
```

## Writing a source

```console
vitruvio source scaffold aulasvirtuales     # writes ~/.config/vitruvio/sources/aulasvirtuales.py
$EDITOR ~/.config/vitruvio/sources/aulasvirtuales.py
vitruvio source add algebra-aula --kind aulasvirtuales --brain-name algebra --option materia=77
vitruvio source pull algebra-aula
```

Two methods: `list()` returns what is on offer without fetching any of it, and `fetch(item)` returns one item's
bytes. They are separate so that a duplicate can be skipped *before* it is downloaded. When a remote listing omits
the real filename or MIME type, return `FetchResult(data, media_type=..., title=...)` instead of bare bytes. The
metadata discovered from the downloaded file is then recorded on the canonical block; a declared `media_type` still
wins.

`self.options` contains the effective merge for this pull, not necessarily only the committed defaults. Reject
unknown keys in the constructor so a typo on either `source add` or `source pull` fails before fetching anything.

Use what `BaseSource` gives you rather than the bare equivalents. Each carries a bound the naked call does not, and
every one of them fails as a hang with nothing on screen:

| use | instead of | because |
|---|---|---|
| `self.run([...])` | `subprocess.run` | closed stdin, a real timeout, bytes not text, `VITRUVIO_*` stripped, stderr in the error |
| `self.get(url)` | `httpx.get` | a timeout, a status check, `max_bytes` |
| `self.contain(path)` | `Path.read_bytes` | refuses a symlink, anything outside the root, a FIFO, an oversized file |

The `VITRUVIO_*` stripping is not paranoia: a source that shells back into vitruvio while inheriting `VITRUVIO_BRAIN`
writes into the brain that is pulling it. And `contain` refusing a FIFO is the one that has teeth — `read_bytes()` on
a FIFO blocks forever, and a glob will hand you one without comment.

### `origin` is the dedup key

Make it stable across runs, and strip anything incidental before returning it — a session token, a rotating query
parameter. Only the source knows which parts of its own addresses are meaningful. vitruvio case-folds it, so two
origins differing only in case collide; the worst case is a spurious skip.

## What stops the same thing being registered twice

Three layers, in the order they run:

1. **The origin index.** Before anything is fetched, one hash-map lookup answers "have I acquired this?". This is the
   cheap layer: pulling a folder of a hundred unchanged files does a hundred lookups and no reads.
2. **The redaction guard.** After the fetch, before the register. See below.
3. **Content addressing.** Identical bytes compute the same block identity, so `register` reports a duplicate. The
   backstop for a source that cannot produce a stable origin: one wasted download, never a wrong result.

Nothing is persisted for any of this. The origin index is derived state under `.vitruvio/`, regenerated by
`vitruvio index build` like every other index — which is why there is no cursor file.

Changing a source's `media_type` or `normalize_with` **re-registers** rather than being skipped. Both are part of a
canonical block's identity, so a silent skip would make the correction do nothing and leave the wrong block in place.
Likewise, an item listed without a type does not trust an existing `application/octet-stream` registration: it is
fetched once so a `FetchResult` can replace that generic type. Once the origin holds a specific MIME type, later
pulls skip it before downloading again.

### A pull can never restore redacted bytes

`vitruvio retain redact` destroys bytes under policy — for personal data, credentials, licensed material. A source
that still lists the same item would re-fetch exactly those bytes, and a scheduled pull would undo every redaction on
a schedule.

So a digest that was tombstoned is refused, out loud, and `--refetch` does not override it:

```console
vitruvio retain redact <BLOCK> --memory-type canonical --reason "personal data" --yes
vitruvio source pull papers --refetch
# skip  paper.pdf   sha256:a0f459... was redacted; re-registering would restore the bytes a
#                   retention policy destroyed. Undo the redaction deliberately if that is what you want
```

Undoing a redaction is a deliberate act: register the file by hand with `vitruvio source register`.

## Several brains

A source declares which brain it feeds, so one command updates a whole project:

```console
vitruvio source pull --all
# ok    algebra-aula       4 registered
# warning: fisica-aula: source 'fisica-aula': aulasvirtuales exited 2: no session, run `login` first
# ok    papers             12 registered, 3 skipped
```

`--all` keeps going past a failure and exits 11 if any source failed. Being told which one of six went wrong is
better than stopping at the first and leaving four that would have worked unpulled and unmentioned.

The source's declared brain **wins**, and a conflicting `--brain` is an error rather than an override. Registering
one subject's material into another brain is the worst outcome available here, and content addressing has no undo
for it.

## Where a pull can go wrong

```console
vitruvio source status
# ok  papers        directory        ~/Downloads/arxiv
# --  algebra-aula  aulasvirtuales   -> algebra   aulasvirtuales is not on PATH
```

`status` reports an unusable source as a row rather than failing, because one broken declaration must not hide the
five that are fine.

One risk worth stating plainly: a `directory` source composes with `vitruvio dist push` into a way to publish
something you did not mean to. Point one at the wrong folder and a private key becomes a canonical block,
content-addressed and Merkle-committed in a public repository — which cannot be cleanly retracted. `source add`
warns when a path sits outside the project directory. Read that warning.

## Exit codes

| code | meaning |
|---|---|
| 0 | pulled, or everything was already held |
| 2 | the invocation was wrong: an undeclared source, `--all` with a name |
| 3 | the declaration is wrong: no `path` on a directory source, a kind that is not installed |
| 11 | a source was unreachable or refused: a tool missing, a timeout, a directory gone |
