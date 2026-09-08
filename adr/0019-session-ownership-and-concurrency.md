# ADR-0019: One session is one coherence scope, and it admits one writer

## Context

ADR-0013 made `BrainSession` the single cache every operation reads through, and issue #34 gave it the one rule it
needed to be correct: a write executes inside `BrainSession.write`, which compares the durable head pointer before
and after and clears the cache when it moved. That rule answers *when the session forgets*. It says nothing about
*who is still reading*, and the difference stops being academic the moment one session outlives one request.

One already does. `vitruvio browse` builds a single `BrainService` and drives it from Textual worker threads whose
`exclusive=True` groups serialize only within a group: classifying a source is a write in group `classification`,
the block rows and the detail pane are reads in groups `rows` and `detail`, and nothing keeps them apart. ADR-0003
plans the same shape for a protocol adapter — "a lifespan that opens a service" — which is a session shared by
every concurrent request the process accepts.

Three things were wrong under that shape, none of them reachable from a single-threaded CLI command and none
covered by a test:

- `brain()` was a check-then-set over a plain dict. Two threads both called `open_brain`, which rebuilds every
  registered index and can take seconds, and the loser's `Brain` was returned to its caller without ever being
  the session's cached instance — a live brain the session could not reach, which is the one thing ADR-0013's
  rule exists to prevent.
- `invalidate()` clears the session's references and revokes nothing. A thread already holding a brain kept
  answering from a composition that had been replaced.
- `write()`'s before/after comparison assumed it was the only writer. Two threads sharing the WRITE brain each
  measured a pointer the other had already moved, and the distribution flows hold that brain across a registry
  round trip that takes as long as the network does.

## Decision

**One session is one coherence scope.** A CLI command owns one for its own duration. The browser owns one for the
run. A protocol adapter opens one per brain per request scope, or shares one and accepts the refusals below.
Adapters do not add cache rules of their own; the three mechanisms here are the whole contract.

**The cache is guarded, briefly.** `brain()` reads the cache without a lock and takes one only to open, so a
cached capability stays free. `invalidate()` takes the same lock, which is what makes "opened, then cached"
indivisible.

**Invalidation bumps a generation, and a read can be told.** `BrainSession.pinned(capability)` is the read
counterpart of `write()`: it captures the generation, yields the brain, and raises `StaleBrainError` if the
composition was replaced while the body ran. A reference already handed out cannot be revoked — but its answer
can be refused, which is the honest version of the same guarantee. The error is retryable and reported as 409.
`BrainService.pinned` exposes it to an interface that answers one question with several calls; the browser's
preview and four tabs are the case that exists today, and they now read again rather than paint two compositions.

**One writer per session, refused rather than queued.** `write()` records who is inside and raises
`SessionBusyError` for anybody else. Reentrancy is tracked rather than delegated to an `RLock`, because the unit
that may nest is the **task** and not the thread: `migrate` and the reconciliation flows nest a write on one
thread, but `push_async` and `pull_async` hold the write open across the registry round trip, and two coroutines
awaiting on one event loop share a thread identity — so a thread-reentrant lock reads the second as a nested call
and lets it in. The owner is the running `asyncio` task when there is one and the thread otherwise, and it is
released in a `finally`, so a cancelled push leaves the session writable. Refusal is also what makes the
before/after comparison mean anything again.

Both errors share a new `ExitCode.BUSY` (13): the only status in the table that means "nothing is wrong, ask
again".

## Consequences

- A new failure mode exists that no caller had to handle before. It is marked `retryable`, and the CLI cannot
  reach it: one command, one session, one thread. The browser reaches it and answers by reading again — twice at
  most, because the retry can race the next write too and an exception out of a thread worker ends the
  application. An async adapter serving two requests on one loop is the other caller that reaches it, which is
  why the writer is identified by task.
- Queueing was rejected. It is the obvious alternative and it is wrong here: the writes being separated are
  registry pushes and pulls, and a caller parked behind one has no way to learn it is waiting. A refusal it can
  retry is information; a silent wait is a hang with a good reputation.
- Holding the lock for the duration of `write()` was rejected for the same reason — it would serialize a read
  behind a minutes-long push. The lock covers opening and clearing only.
- `pinned()` is opt-in rather than wrapped around every read. Making all forty-seven read sites able to raise
  `BRAIN_STALE` would trade a millisecond-wide staleness window for a new error on every operation, and the reads
  that actually span something slow are few and named: the two distribution reads, and the browser's detail pane.
- This is a coherence contract within one process. Two processes on one layout are still ordered by the store,
  not by this lock, and nothing here changes that.
