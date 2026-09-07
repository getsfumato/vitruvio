# ADR-0018: The declared actor and per-brain collaborators are authoritative

## Context

ADR-0002 made every write name an actor and resolved it by precedence: flag, then environment, then the file.
Collaborators followed the same shape, each layer replacing the whole list, and the list was project-wide. Both
decisions treated `vitruvio.toml` as a convenience default that any invocation could override.

Provenance is what the override touched. A `--actor` in one shell re-attributed every write of a session to somebody
the reviewed file never named; a `--assisted-by` put a party into provenance v2 that appeared in no committed
declaration; and a project whose brains are worked by different agents could only declare one list for all of them,
so a collaborator recorded into the wrong brain was one flag away. The first user-visible symptom was a brain whose
blocks all named an assistant nobody had agreed to, indistinguishable in the ledger from one where they had.

## Decision

**The declared actor is authoritative.** Once `[actor] id` is set, every write into the project is attributed to it.
`--actor` and `VITRUVIO_ACTOR_ID` naming the same id are harmless repetitions and leave the file as the origin; naming a
different id is refused with `ACTOR_OVERRIDE_REFUSED` (exit 3). The commands that create the declaration -- `brain
init`, `project init`, `brain migrate` -- resolve with `declaring=True` and may name any actor. With nothing declared,
flags and environment supply the actor as before, which is how the declaration first gets written.

**Collaborators are declared per brain and selected, never replaced.** `[[brains.<name>.assisted_by]]` for a named
brain and `[[brain.assisted_by]]` for the single one are the universe of parties a write into that brain may record;
a brain that declares nobody inherits the project's `[[assisted_by]]`. With no flag every declared party is recorded.
`--assisted-by` and `VITRUVIO_ASSISTED_BY` select among them, keeping the declared `kind`, `name` and `model`; a
party the brain has not declared is refused with `COLLABORATOR_NOT_DECLARED` (exit 3); `--empty-assisted-by` records
no assistance for one invocation. When nothing is declared anywhere, or the invocation is declaring, flags name
parties outright: `brain init --assisted-by` writes `brain.assisted_by`, `project add --assisted-by` writes
`brains.<name>.assisted_by`.

`ResolvedConfig` carries `collaborators_origin` beside `actor_origin`; `config show` prints the declarations and
`inspect doctor` reports `config.collaborators` for the selected brain, so a refusal is never the first time somebody
learns what the file says.

## Consequences

- Automation that relied on `--actor` overriding a declared actor breaks with an error that names the declaration
  to change. That is the intended cost: attribution moves from shell history to a reviewed file.
- The shipped skills stop telling an agent to pass `--assisted-by` freely. Onboarding asks which email is the actor
  and which agents will assist each brain, and writes both; an undeclared agent stops and asks rather than writing.
- ADR-0002's "flag beats file" precedence for the actor and the project-wide collaborator list are superseded by
  this record. Its refusal of an unattributed write stands.

## Amendment: a brain may declare its own actor

The actor was first kept project-wide because a project is what several brains share. The exception that turned
up immediately is the brain a *different person* keeps inside a shared project. `[brains.<name>.actor]` (and
`[brain.actor]` for a single-brain file) now declares that person; it must carry an `id`, it replaces the project's
actor for writes into that brain, and it is authoritative in exactly the same way -- a differing `--actor` or
`VITRUVIO_ACTOR_ID` is refused, and the refusal names the table to change. `project add --actor` writes it when the
id differs from the project's. The project's actor stays the default for every brain that declares none.
