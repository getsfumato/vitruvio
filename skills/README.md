# Skills

The agent-facing contracts. The paper treats a **skill** as one of a brain's access contracts, alongside the CLI
itself — so the knowledge of *how to drive this* travels with the brain rather than living only in vitruvio's docs.

```console
vitruvio skills list
vitruvio skills install                       # into .claude/skills/
vitruvio skills install --into DIR --skill vitruvio-cli --force
```

| skill | covers |
|---|---|
| `vitruvio` | the entry point: what a brain is, the envelope, the exit codes, the five modules |
| `vitruvio-cli` | the command surface: every group, every command, the flag that decides the outcome |
| `vitruvio-query` | searching, and reading a bundle without over-claiming |
| `vitruvio-ingest` | the propose → validate → commit loop, and pulling from declared sources |
| `vitruvio-retention` | the five removal mechanisms, and which one you actually want |
| `vitruvio-dist` | publishing and installing |
| `vitruvio-reconcile` | joining a history somebody else advanced, and deciding what did not apply |

`vitruvio` carries four references: `json-envelope.md`, `exit-codes.md`, `evidence-bundle.md`, and
`cli-reference.md`.

## Layout

Each directory holds a `SKILL.md` opening with YAML frontmatter:

```yaml
---
name: vitruvio-cli
description: >-
  One or two sentences saying what this covers *and when to reach for it*. This is what
  decides whether the skill loads at all, so a vague one makes the skill dead weight.
allowed-tools: Bash(vitruvio:*), Read
---
```

A tool installing these can copy each directory verbatim — nothing is generated at install time. Two caveats it
should know:

- **The set is layered, not independent.** `vitruvio` is the entry point and the others are reached from it, so
  installing one alone works but loses the routing.
- **Cross-references are relative and point at the entry skill.** `vitruvio-cli` links to
  `../vitruvio/references/exit-codes.md` and `../vitruvio/references/cli-reference.md`. Install `vitruvio` alongside
  anything else, or those links dangle. `vitruvio skills install` with no `--skill` installs all six, which is why
  that is the default.

## One copy, two addresses

These files are **authored here**, and `apps/cli/src/vitruvio/cli/skills` is a symlink to this directory, so the
wheel and the sdist both carry the contents. Two copies under version control would drift, so there is one.

A symlink rather than hatchling's `force-include`, which cannot reach outside a project directory in an **sdist** —
`pip install vitruvio` would have failed for anyone whose installer preferred the sdist over the wheel.

Shipping them in the wheel is what ties a skill to the version of the CLI it documents: a skill installed from a
different release than the binary it drives is precisely the failure this arrangement prevents. Reading them from
here is what lets a tool install skills without pip.

`vitruvio skills install` leaves an existing skill directory alone unless `--force`, because a consumer may have
edited one and silently overwriting local edits is not something a copy command should do.

## Two of these are checked, not trusted

`vitruvio/references/cli-reference.md` is **generated** from the cyclopts command declarations by
`python -m vitruvio.cli.reference --write`, and CI fails when the committed copy is stale.

`vitruvio-cli/SKILL.md` writes command names in tables, where the repository's docs-promise check cannot see them —
so `apps/cli/tests/test_docs_promises.py` extracts every command it names and fails the build if one does not exist,
and fails if a command *group* goes undocumented. The counts in its opening line are pinned for the same reason:
adding a command forces the prose to be corrected rather than letting it quietly become wrong.

## What they all teach

1. Always `--json`, and branch on `ok` then `error.code`.
2. Read `warnings` **even when `ok` is true** — a degraded answer that looks clean is the failure the whole design is
   arranged to prevent.
3. The brain returns evidence; you write the prose. There is no `answer` field.
4. Never invent a `block_id`.
