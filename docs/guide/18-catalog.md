# Catalog

The catalog classifies canonical evidence without changing that evidence. Its declarations are ordinary semantic
blocks, so they are content-addressed, versioned, published and verified with the brain rather than living in a
sidecar database.

There are four declarations:

- a **scheme** names one classification axis; an exclusive scheme permits one direct class per source;
- a **class** is a label inside a scheme;
- a **hierarchy** makes one class broader than another;
- a **placement** assigns a canonical block to one or more classes.

Class references always take the unambiguous form `scheme/label`.

## Apply a manifest

A committed manifest is the reproducible path for a non-trivial catalog. For example:

```toml
schema = "vitruvio.catalog/v1"

[[schemes]]
name = "discipline"
exclusive = false

[[classes]]
scheme = "discipline"
label = "science"

[[classes]]
scheme = "discipline"
label = "physics"
broader = ["discipline/science"]

[[placements]]
source = "sha256:REPLACE_WITH_A_CANONICAL_BLOCK_ID"
classes = ["discipline/physics"]
```

Validate the entire document before writing, then apply it atomically:

```console
vitruvio catalog apply catalog.toml --dry-run
vitruvio catalog apply catalog.toml
vitruvio catalog
vitruvio catalog show
```

A validation failure writes nothing. Applying the same declarations again is idempotent. The shorter `catalog
scheme`, `catalog class` and `catalog place` commands create the same declaration blocks and are useful while
exploring.

The bare `vitruvio catalog` command renders a folder tree: schemes, nested classes, directly placed canonical
sources and an `unclassified` folder. Source leaves include the recorded creator and whether that identity is
cryptographically verified. Use `--json` to give an LLM the same hierarchy as structured data, including stable
block ids and effective memberships; `catalog show` remains the flat declaration-oriented view.

## Browse and query

`browse` resolves descendants, so browsing a broad class includes sources placed in narrower classes. Repeating a
class intersects the selected source sets:

```console
vitruvio catalog browse --class discipline/science
vitruvio catalog path --scheme discipline
vitruvio query search "ondas" --class discipline/physics
vitruvio query explain "ondas" --class discipline/physics
```

The planner treats classes as filters, not ranking hints. A result outside the selected classes is inadmissible even
when a lexical or vector index ranks it highly. `catalog path` is only a virtual navigation view over ordered
schemes; it creates no directories and changes no block.

Catalog metadata names canonical block ids rather than filenames. Register or inspect the source first, and never
copy a digest from another edition under the assumption that it means the same bytes.

For a person, `vitruvio browse` exposes this same hierarchy in the sidebar. Select a canonical source and press `c`
to add placements interactively. The UI never invents schemes or classes: declare those first with a manifest or the
`catalog scheme` / `catalog class` commands. Governed changes require an active authorized key with `commit` scope in
`ssh-agent` and the resulting snapshot is explicitly signed; ungoverned changes remain visibly unsigned.
