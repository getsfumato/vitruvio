# ADR-0014: Reconciliation, And The Four Choices Vitruvio Had To Make Itself

**Decision status:** Accepted.

## Context

pyboltzmann 0.6 added reconciliation: `fetch`, which brings a remote history without moving the local pointer, and
joining two histories under `merge`, `rebase` or `squash`. Before it, a push whose history had diverged could only
be refused, and vitruvio's advice at that refusal — in `dist push`'s docstring, in the `vitruvio-dist` skill, and
in guide chapter 10 — was to `pull`, re-commit, and push again.

That advice was wrong in a way worth recording, because it is the reason this work happened. A pull *adopts* the
published composition. Every block committed here since the last pull stops being a member of any module: it no
longer verifies into a root, no longer appears in a search, and a pack no longer carries it. The documentation said
so plainly, two paragraphs from the advice, and added that **no command restores it**. So the only exit vitruvio
offered from a divergence was to discard one side's work, and it was suggested in the imperative.

A second thing surfaced while wiring the new exceptions. `DivergenceError` was not in the mapping table, so it
matched its parent `DistributionError` and a diverged push reported `REGISTRY_FAILED`, exit 9, `retryable=True`.
Exit 8 — documented in three places as "the histories diverged" — was never emitted by anything. An agent
following the retryability flag retried a refusal whose answer is identical every time.

The SDK settles the hard semantics: the three strategies are one computation recorded three ways, a conflict is a
validation failure rather than a differencing failure, and admitting a block whose evidence is absent is refused.
None of that was ours to decide. What follows is the four things that were.

## Decision

### 1. `reconcile` is its own command group; `fetch` belongs to `dist`

`dist` is transport — bytes to and from a registry. Reconciliation is history: which versions this brain descends
from, and whose name stays on the work. `fetch` is transport, so it sits with `push` and `pull`; the ten
reconciliation commands are their own group. The split git makes between `fetch` and `merge`, for the same reason.

The group is registered after `dist` because that is the order somebody meets it: you publish, the push is refused
because somebody else published first, and reconciling is what you do about it.

The three strategies are **three commands** rather than one `--strategy` flag. A choice that decides attribution
should be in what somebody typed, not in a flag's default they did not notice.

### 2. The strategy is declared per brain, and vitruvio supplies no default

`ReconcileRequest.strategy` is required in the SDK, and the docstring says why: once snapshots are signed, the
difference between the three is attribution, and a default would be the SDK choosing whose name comes off the work.
That constraint had to survive being wrapped, or wrapping it would have quietly undone it.

A key in `vitruvio.toml` does not undo it — `reconcile = "merge"` under a brain is a person stating the thing once
rather than a tool assuming it. What *would* have undone it is giving that key a default value, so it has none:

```toml
[brains.algebra]
reconcile = "merge"        # merge | rebase | squash
```

**Absent means a fetch reconciles nothing.** It brings the history, reports the plan, and says how to choose. The
alternative considered was defaulting to `merge` on the grounds that it is the most conservative — it is the only
strategy under which the other side's snapshots and signatures survive — and rejected: "conservative" is a claim
about attribution, which is exactly the claim that is not ours.

The kernel declares its own `ReconcileStrategy` enum rather than importing the SDK's, because the kernel staying
importable without the SDK is the seam that package exists to hold. `runtime/coerce.py` is the one place the two
meet, so if they drift they drift there. A test asserts they have not.

### 3. A fetch commits only a clean plan, and never opens a reconciliation

`ReconcilePlan.is_clean` — every incoming block applied, and nothing this brain holds leaving — is the whole
condition. Clean, and a declared strategy: it commits. Otherwise it reports.

The subtle half is what "otherwise" does **not** do. It does not open the reconciliation. An open one sets the
`reconcile` pointer, after which the SDK refuses every ordinary write on the brain — one guard on the single write
path, so commit, ingest, register and drop all stop. Opening one as a side effect of a command somebody ran to look
at a remote would leave brains that refuse writes for reasons that are off-screen, and the person who ran it has no
particular reason to know the two are connected.

Nothing is bought by opening it early, either. The SDK persists decisions and recomputes the plan, precisely so a
judgment is never acted on after it may have stopped holding. So the resolver recomputes it when somebody is ready
to answer, and the fetch stays a fetch.

The `--no-reconcile` flag exists for a script driving the steps itself.

### 4. Exit 12 is new, and does not reuse 10

`REVIEW` (10) is documented as "the protocol asking for a human", which describes a halted reconciliation as
accurately as it describes a cascade over the review threshold. It was still the wrong code to reuse, because the
two lead to different commands: a 10 means someone must approve a removal, a 12 means verdicts are waiting and
`reconcile status` lists them. A caller that could not tell them apart would reach for the wrong one half the time.
The enum is append-only, so adding is legal and reassigning would not have been.

One code covers both directions a halt arrives from — a reconciliation that just stopped, and any ordinary write
refused because one is open — because the answer to both is the same command.

The SDK's message for the second case ends in `reconcile_abort()`, a Python method on a class the user never sees.
The mapping table rewrites the hint rather than passing it through; a person handed that vocabulary has been handed
the wrong tool's manual.

## Consequences

**`brain history` had to become a graph.** A snapshot names `parents`, plural. The renderer was reading `parent`,
singular, which 0.6 removed — and `Mapping.get` returns `None` rather than raising, so the parent line silently
stopped printing and every snapshot looked parentless. `--graph` now marks the first-parent chain, which is not
merely the first entry: it is the history a reconciliation was performed onto, what every rule meaning "the parent"
refers to, and the chain an audit follows.

**The interactive resolver resolves; it does not originate.** It records decisions, accepts removals and
concludes, and it reports "nothing in progress" when nothing is. Originating a reconciliation needs the other
history's digest, and nothing persists which history was last fetched — so that is typed once, into
`reconcile merge|rebase|squash`, which is also where the strategy is chosen. Splitting it that way keeps the
screen to the part that is genuinely a loop, and keeps the one irreversible-feeling choice in something somebody
typed.

It is a Textual app under `cli/tui/`, with the lazy import and the TTY and `--json` refusals `browse` already
established (ADR-0012), and `q` prints what state it is leaving behind — walking away from an open reconciliation
is how a brain ends up refusing writes for an invisible reason.

Decisions the protocol forbids are **absent** from the footer rather than refused on keypress. `admit` on a rejected
block is the case, and the reason is on screen: nothing downstream would catch the broken invariant, so there is no
later safety net to fall back on.

**The eight reconciliation operations stay behind `reconcile_ops` rather than being forwarded method by method.**
Adding them to `BrainService` took it to 85 public methods against a `PLR0904` limit of 80 — which is exactly what
ADR-0013 installed that ratchet to catch, in its own words, "a domain's worth of logic landing back on it". Raising
the threshold would have spent the mechanism to avoid the refactor it exists to force. It costs little here:
plan, start, decide, conclude is a loop, and no caller wants one of those operations alone.

**`fetch` reached the facade, `reconcile` did not.** The asymmetry is deliberate and follows the group split — the
facade keeps transport beside `push` and `pull`, and the domain lives in its ops class.
