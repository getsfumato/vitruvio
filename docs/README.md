# Documentation

- [Guide](guide/README.md) — how to use vitruvio, chapter by chapter, in order.
- [Decisions](adr/README.md) — why it is like this. One ADR per decision, numbered, never renumbered.
- [Architecture](architecture.md) — the workspace, the layering, and the two contracts that carry weight.
- [Contributing](contributing.md) — the dev loop, and the gate every change passes.

For how an *agent* drives vitruvio, see [`skills/`](../skills/README.md) — those are installable contracts rather
than prose, and they ship inside the wheel.

Two things here are checked rather than trusted, and both exist because the documentation was once wrong in a way
nobody noticed: `skills/vitruvio/references/cli-reference.md` is generated from the command declarations, and no
document in this repository may name a command that does not exist. CI fails on either.
