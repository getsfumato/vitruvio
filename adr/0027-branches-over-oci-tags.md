# ADR-0027: Branches are the protocol's, and vitruvio only decides how they meet a project

**Decision status:** Accepted.

## Context

A team publishing one brain to one tag ran into the divergence check every day. It works as intended: a push that is
not a fast-forward exits 8, and the answer is `dist fetch` and a reconciliation. But on a shared `latest` every push
after the first is one of those, so the tag serializes the whole team, and reconciling faster does not change that.

The obvious answer is the one git has: a branch per line of work, each published to its own tag, joined when someone
decides to. The question was where branches live.

Building them in vitruvio alone was viable, and the first plan did it. Branch state would sit in a vitruvio-owned file
beside the brain, and one small adapter would write the SDK's head pointer. That plan had three costs that do not go
away:

- **Another client's prune could reclaim a branch head.** The SDK prunes from `retained`, which is truncated to
  `retained_roots`. Only vitruvio would know a stale branch's head mattered, and a prune by any other runtime would
  reclaim it while vitruvio still named it.
- **A branch would be a branch to vitruvio only.** Another runtime listing the repository would see an unfamiliar tag
  and nothing to say it was a branch.
- **The adapter would write a pointer vitruvio does not own,** pinned to one SDK version.

## Decision

**Branches are part of the Boltzmann protocol (paper Section 7.5) and live in pyboltzmann ≥ 0.10.0. vitruvio adds the
CLI, the defaults, and the mapping onto a project.**

From the protocol and the SDK:

- **Refs.** Named pointers are kept in a `refs` pointer beside `head`, not inside it. `BrainState` forbids extra
  fields, so an older SDK could not read a head pointer that carried a branch.
- **Retention.** Every branch head is a retained root, kept in `retained` outside the bound. A client that knows
  nothing of refs therefore still keeps branch heads when it prunes.
- **Naming.** `main` is the default branch. Every other branch publishes to `br.<name>` with `/` replaced by `.`. The
  prefix is fixed by the protocol, so every client reads the same branches. The branch is not written into the
  manifest, so digests do not change.
- **Operations.** `checkout` writes the ref before moving the head. `join` fast-forwards or delegates to
  reconciliation, with no default strategy.
- **Pull** into a branch's tag installs into that branch.
- **Push** re-reads its tag after writing and reports a lost publish (`LostPublishError`).

From vitruvio:

- **`main` publishes to `[registry].tag`, not to the SDK's `latest`.** A project that configured `stable` keeps
  publishing there. The current branch and its tag are read from the `refs` pointer without opening the brain
  (`vitruvio.runtime.branches`), because every distribution command needs the default tag before it decides to open
  anything.
- **Moving the head opens the install view.** `switch`, `create`, `delete` and a fast-forward `merge` use the view
  `pull` uses, which registers no indices. The SDK refreshes indices after a checkout, and on the full WRITE view that
  would re-embed the whole composition for a pointer move. The vector sidecar is bound to a Merkle root, so a query
  after a switch never reads the other branch's vectors.
- **A merge that reconciles behaves like `reconcile`.** It uses the brain's declared strategy or refuses. A halt is
  exit 12 with the questions in the envelope, and `reconcile resolve`/`continue` finish it. Only this path opens the
  full WRITE view, as `reconcile` does.
- **`branch switch` tracks.** A branch that exists only on the registry is pulled from its tag, so `InstallOps` carries
  projections, authenticity and the vector layer as it does for any pull.
- **Error codes.** `LostPublishError` is `PUSH_RACED` with exit 8, sharing the code and remedy with `DIVERGED` (ADR-0004
  allows several codes per exit when the remedy is the same). The branch errors are `BRANCH_NOT_FOUND` (4) and
  `BRANCH_EXISTS`, `BRANCH_NAME_INVALID`, `BRANCH_UNMERGED` and `BRANCH_REFUSED` (2).

## Consequences

- A brain that never creates a branch writes no `refs` pointer and behaves as before. The one change is that `dist
  pull` with a reconciliation open now exits 12, as a commit already did.
- `dist tags` reports each tag as `default`, `branch` or `release`.
- **The race is detected, not prevented.** OCI has no conditional write on a tag. Only the push that lost observes the
  race, and only a client that re-reads. Branches make it rare rather than impossible.
- **Deleting a branch from the registry is not offered.** OCI deletes by manifest digest, and a branch's manifest may
  be the one `latest` names.
- Importing `asyncio` at module scope in `boltzmann.brain`, which arrived with the post-publish check in 0.10.0, costs
  every CLI start ~17ms and fails `test_import_cost`. It is fixed upstream in the SDK's next patch release, which this
  pin then moves to.
