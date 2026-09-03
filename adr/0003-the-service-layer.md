# ADR-0003: The Service Layer, And Why Apps Are Thin

**Decision status:** Accepted, implemented in M1.

## Context

Vitruvio will expose the same protocol through three interfaces: a CLI now, an MCP server and an HTTP API
later. Each of them has to answer the same questions -- which brain, what does this operation do, what does its
result look like, and what does a failure mean.

There is also a constraint discovered by reading the SDK rather than by reasoning about it: `Brain.__init__`
calls `self.rebuild_indices()` (`brain.py:225`). Every construction of a brain rebuilds every registered index.
Register a vector index and `vitruvio brain state` -- a command whose whole job is to read a pointer file --
constructs an embedder, imports sentence-transformers, and imports torch.

## Decision

**One service layer, `vitruvio.runtime`, with one method per protocol operation, each returning a plain
dictionary.** The CLI renders those dictionaries; the MCP server and the API will serialize the same ones.

Four modules carry it (see ADR-0013: `service` was later decomposed into one module per domain, with
`BrainService` kept as the facade — the decision below is unchanged, only its internal shape):

- `service.BrainService` -- the operations. Nothing here decides how a result is displayed, and nothing writes
  prose.
- `assembly` -- `Capability.INSPECT | RETRIEVE | WRITE`. `INSPECT` registers **no** index, which is the answer
  to the constraint above. Every operation declares its capability, because "does this need an index" is a fact
  about the operation.
- `wire` -- the only place SDK models become JSON. Its job is the part `model_dump` cannot do: surfacing
  *computed* properties (`Snapshot.digest`, `EvidenceBundle.all_verified`, `CascadePlan.size`).
- `mapping` -- one table from an exception to a code, an exit status, an HTTP status, and **whether retrying
  could ever help**. That last column is the one a caller acts on: a registry timeout is retryable, a retention
  policy refusing a canonical drop is not, and an agent that retries the second is an agent in a loop.

**An app may import `vitruvio.runtime` and `vitruvio.kernel`, and may never import `boltzmann`.** Enforced by
import-linter. If an app needs an SDK type, the service layer is missing a method -- and adding it there makes
the capability available to all three interfaces instead of one.

## Consequences

- `apps/mcp` should be a FastMCP app, a loop over the operation catalogue, and a lifespan that opens a service.
  If it needs more, that is information: the service layer is incomplete.
- The CLI's `--json` and a future MCP tool result cannot drift, because they serialize the identical dictionary.
- The capability gate is asserted on `sys.modules` in the tests rather than by timing, so it cannot pass by
  being fast on a good day.
- `wire` has to be maintained as the SDK's models grow. That is the cost of the guarantee, and it is cheap:
  every function in it is four lines and deliberately dumb.
- One real bug was caught by running the CLI rather than by reading it: `Snapshot.block_count` is a computed
  property, so `model_dump` omitted it and `brain history` crashed. Surfacing computed properties is exactly
  `wire`'s stated job, which is why the fix belonged there rather than in the renderer.
