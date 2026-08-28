---
name: vitruvio-compound
description: Compose several brains of one vitruvio project into one query and read the cross-brain evidence honestly. Use when the user wants knowledge from more than one brain at once, asks to compose, combine or cross brains, wants to know what two subjects say about the same thing, or mentions vitruvio compound.
allowed-tools: Bash(vitruvio:*), Read
---

# Composing brains

A project holds several brains -- a subject per brain, a client per brain, a metric per brain -- and every ordinary
command addresses one of them. A **compound** asks two or more of them the *same* question in one invocation:

```bash
vitruvio --json --project ethicompass compound search "sesgo de seleccion" --brains metrica-a --brains metrica-b
vitruvio --json --project ethicompass compound search "sesgo de seleccion" --all --fuse
vitruvio --json --project ethicompass compound explain "sesgo de seleccion" --all
```

This skill is two things: what composition does, so you can say what a result means; and a **guided flow**, because
the command is deliberately non-interactive and the choosing is your job, done by asking the user.

## How composition works

- **Brains of one project, by name.** `--brains` takes the names `project show` lists, never a path. A path is refused
  with exit 2, and so is a name from another project. That is the scoping rule, not a limitation to work around:
  the vocabulary a compound accepts is the project's.
- **Every brain answers on its own.** Each member plans with its own planner over its own statistics, consults its
  own indices, verifies against its own roots. Nothing is shared between them at query time. A compound of three
  brains opens three brains, and opening a brain at retrieval rebuilds its indices -- expect it to cost about as
  much as three searches.
- **Grouped by default.** Each brain's ranking comes back intact, one brain after the other, in the order you named
  them. Nothing is merged: a block two brains both hold appears once per brain. This is the default because each
  brain's scores are normalised so *its* best match reads `1.00`, and two `1.00` from two brains say nothing about
  each other. Grouped output claims nothing the brains did not.
- **`--fuse` merges by rank, never by score.** Reciprocal-rank fusion across brains -- the same rule the planner
  already uses across retrieval strategies inside one brain: each brain that returned a block contributes
  `1 / (60 + rank)`, absence contributes nothing, and the top match is rescaled to `1.00`. A block is the hash of
  its content, so the same paragraph ingested into two brains is **one block**, and under `--fuse` it accumulates
  from both and rises. That is the cross-brain signal a compound exists to surface: what two subjects agree on.
- **`--all`** composes every brain the project declares whose layout exists on this machine, and reports the
  others under `skipped`. At least two must result, or the command refuses.

Every filter `query search` takes -- `--memory-type`, `--subject`, `--tag`, `--since`, `--until`, `--mode`,
`--limit`, `--expand-depth` -- applies in every brain. `--limit` is **per brain**.

## The guided flow

Do these in order. Each step is gated on the user's answer to the previous one; never guess a project, never
guess brains, never run `brain use` to make a command pass.

1. **Which project.** Run `vitruvio project list --json` and show the user `data.projects[].name` with each one's
   `brains`. Ask which project. If exactly one is registered, say so and continue with it. If none is, tell them to
   run `vitruvio project register` inside the project's directory (or `project init` for a new one) and stop. Do not
   offer a project whose `present` is `false`: its `vitruvio.toml` has moved.

2. **Which brains.** Run `vitruvio --json --project <PROJECT> project show --json` and show `data.brains[]` --
   `name`, `description`, and `exists`. Ask which brains to compose: at least two, or all of them. A brain whose
   `exists` is `false` has no layout on this machine and cannot be consulted; say so rather than offering it. Only
   this project's brains are on the table.

3. **Which question, and how ranked.** If the user has not stated the query, ask for it. Ask whether they want the
   result grouped (each brain's own ranking, the default) or fused (one ranking across brains; what both brains
   hold rises). One sentence each is enough.

4. **Run it.**

   ```bash
   vitruvio --json --project <PROJECT> compound search "<TEXT>" --brains <A> --brains <B> [--fuse] [filters]
   vitruvio --json --project <PROJECT> compound search "<TEXT>" --all [--fuse]
   ```

5. **When a brain returned nothing**, or the user asks why the plans differ, run
   `vitruvio --json --project <PROJECT> compound explain "<TEXT>" --brains <A> --brains <B>`. It returns one
   explanation per brain, side by side; there is no compound plan, because composition is a rule applied after every
   brain has answered, not a decision.

## Reading the result without over-claiming

Branch on `data.fused`, then read three things:

- **`data.members[]`** -- one entry per brain: `count`, `truncated`, `all_verified`, `verified_against` and `plan`.
  `verified_against` is **per brain** and is never merged: two brains holding semantic memory have two semantic
  roots, and a citation names the one it verified against. A `truncated` member means there may be more in *that*
  brain -- check it before saying a brain holds nothing on the topic.
- **`data.matches[].brains[]`** -- which brain or brains returned the block, at what rank in each, with the score
  that brain gave it. Grouped: always one entry. Fused: one entry per brain that returned it, and two entries is the
  cross-brain agreement worth pointing out.
- **`data.skipped[]`** -- declared brains that were not consulted, and why. A compound that silently dropped a brain
  would misstate what the project says; it does not, and neither should you.

**Cite the block and the brain.** `block_id` names the content; the brain names whose composition it verified in.
The fused `score` is agreement between brains and retrieval strategies -- a string, not a probability, and not to
be reformatted. The brain returns evidence and you write the prose; there is no field that does it for you.

## When it refuses

| exit | what happened | what to do |
|---|---|---|
| 2, `known: ...` in the hint | a name the project does not declare, a path, only one brain, or `--brains` together with `--all` | re-ask with the names listed in the hint; do not guess |
| 3 | no project could be found from this directory | pass `--project <NAME>`, or `project register` first |
| 4 | a named brain has no layout on this machine | `dist pull` it, or leave it out |

`vitruvio-query` covers reading a single bundle; everything it says about `score`, `truncated`, `superseded_by` and
`resolvable` holds for every match here.
