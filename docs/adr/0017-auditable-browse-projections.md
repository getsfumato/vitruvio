# ADR-0017: Bounded browse projections and the history audit envelope

## Context

Showing a block creator joins three independent structures: creation provenance names the actor, the snapshot that
introduced that provenance names a trust root, and detached SSH records determine whether an accepted key vouches
for the actor subject. The first implementation placed the join in `BrowsingOps`, then made catalog navigation call
that public operation. A page was bounded, but an empty or small catalog directory still read the entire canonical
module; each creator claim also verified every module of its historical brain. Typing a filter made that cost visible.

History had a second ambiguity. Existing callers consumed `history.snapshots` as the SDK's retained sequence. A
reconciliation makes other commits reachable without putting them in that sequence, while the human audit must show
them. Replacing the meaning and order of `snapshots` made the CLI complete at the cost of a silent envelope break.

## Decision

`Capability.BROWSE` is the read-only capability for composition-order listing. A shared internal
`block_rows.project_rows` projection accepts an opened brain, a memory type and an already-selected identity list.
`BrowsingOps` owns pagination and filtering; `CatalogOps` owns class traversal; neither operations object constructs
the other. Provenance lookup, origin recovery and creator attribution happen inside the projection and are therefore
bounded by the selected identities. Empty selections return before provenance is opened.

Signature authentication and Merkle integrity stay separate. Browse attribution authenticates introducing snapshots
and caches the result, but does not construct and verify a historical brain. `LifecycleOps.history` is the explicit
whole-history audit and is the only consumer that asks the authorship audit to rehash each historical snapshot.
Unreadable current or parent provenance compositions are evidence gaps: they produce `complete: false` and no
invented introductions.

The history envelope now carries both views:

- `snapshots` remains the retained SDK sequence, in SDK order, for compatibility;
- `commits` is every retained or reachable audit row, including unreadable reachable documents, with HEAD first;
- `ancestry` and `reachable` retain their graph meanings.

Human `history` and `history --graph` render `commits`. Machine callers that used `snapshots` keep their prior
semantics and can opt into the complete audit explicitly.

## Consequences

A filtered browse still scans the module because exact match count is part of that operation; it now authenticates
without whole-brain rehashing and the TUI debounces intermediate prefixes. A full catalog tree still needs every
canonical leaf, while browsing one class reads only its effective source set.

The row projection is intentionally not a second service surface. Keeping selection outside it prevents pagination,
catalog semantics and query ranking from leaking into one broad helper. The cost is that callers remain responsible
for choosing identities and that the JSON envelope exposes two history collections whose distinction must stay
documented and tested.
