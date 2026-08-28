# 17. Compound: one question, several brains

```bash
vitruvio compound search "sesgo de seleccion" --brains metrica-a --brains metrica-b
vitruvio compound search "sesgo de seleccion" --brains metrica-a,metrica-b --fuse
vitruvio compound search "sesgo de seleccion" --all
vitruvio compound explain "sesgo de seleccion" --all
```

A project is several brains under one configuration -- a subject per brain, a client per brain, a metric per brain
([chapter 13](13-projects.md)) -- and every command so far addresses one of them. A **compound** asks two or more the
same question in one invocation, and composes what they return.

## Brains of one project, by name

`--brains` takes the names `project show` lists. It does not take a path, and a name from another project is a name
this project does not declare:

```
error: this project has no brain called './brains/metrica-a'
hint: a compound takes brain names from this project only; known: metrica-a, metrica-b, metrica-c
```

That is the whole scoping rule. A compound composes *this* project's brains, and the vocabulary it accepts is the
project's. `--brains` is the flag rather than `--brain` because the singular is the global option that selects one
brain for every other command, and a compound is about the project rather than about any one brain in it -- a
global `--brain` left in the environment is ignored here.

`--all` composes every declared brain whose layout exists on this machine, and lists the others under `skipped`
rather than failing on them: a project where one subject has not been pulled yet is an ordinary project. Two brains
at least, either way -- for one, `search` is the command.

## Every brain answers on its own

Nothing is shared between members at query time. Each one plans with its own planner over its own statistics
([chapter 6](06-the-planner.md)), consults its own indices, and verifies against its own roots. What a compound adds
happens *after* every brain has answered, and it is a rule rather than a decision -- which is why `compound explain`
returns one explanation per brain and no compound plan.

That independence is also the cost. Opening a brain for retrieval rebuilds its indices, so a compound of three
brains pays three rebuilds: about what three separate searches would.

## Grouped by default

```
4 matches across 2 brains

metrica-a
2 matches
verified against  canonical sha256:151df9b233  semantic sha256:7e77a9fd08

 #   score   memory     block               identity
 1    1.00   semantic   sha256:4ec8c0ba55   Sesgo de seleccion
 2    0.98   semantic   sha256:dc7f65edbf   Muestreo

metrica-b
...
```

Each brain's ranking comes back intact, one after the other, in the order you named them. Nothing is merged, so a
block two brains both hold appears once per brain.

This is the default because of something [chapter 5](05-searching.md) says about a single bundle: a score is
agreement between retrieval strategies, rescaled so the best match reads `1.00`. Two `1.00` from two brains
therefore say nothing about each other, and sorting a concatenation by score would rank a weak best-in-brain above
a strong second-in-brain. Grouped output claims nothing the brains did not.

## `--fuse` merges by rank, never by score

```
 #   score   brains                   memory     block               identity
 1    1.00   metrica-a#1  metrica-b#1   semantic   sha256:4ec8c0ba55   Sesgo de seleccion
 2    0.61   metrica-a#2                semantic   sha256:dc7f65edbf   Muestreo
```

Reciprocal-rank fusion across brains -- the same rule the planner uses across generators inside one brain
([ADR-0005](../adr/0005-statistics-and-the-cost-model.md)). Each brain that returned a block contributes
`1 / (60 + rank)`, a brain that did not contributes nothing, and the top is rescaled to `1.00`. Ranks are
comparable across brains where scores are not: rank 1 means "the best this brain had" in every brain.

**A block two brains hold is one block.** A block's identity is the hash of its content -- no actor, no time, no
task in it -- so the same paragraph ingested into two brains is the same block, and under `--fuse` it accumulates
from both and rises. `brains` on the match says so: `metrica-a#1  metrica-b#1` is two subjects agreeing, and that
agreement is what a compound exists to surface.

The fused score is still a string, still agreement rather than probability, and now agreement between brains as
well as between strategies. Do not present it as confidence.

## Reading the result

Three fields beyond what a single bundle carries:

- **`members[]`** -- per brain: `count`, `truncated`, `all_verified`, `verified_against`, `plan`. Roots stay here
  and are never merged: two brains holding semantic memory have two semantic roots, and a citation names the one
  it verified against. A `truncated` member means there may be more in *that* brain.
- **`matches[].brains[]`** -- which brain or brains returned the block, at what rank in each, with the score that
  brain gave it -- and that brain's own `resolvable`, `superseded_by` and `sources`. One entry when grouped; one per
  contributing brain when fused. A block id fixes the *content*; it does not fix a brain's *installation* of it: a
  block redacted in one brain and readable in another, or superseded in one and current in another, is one block
  with two states, and this is where both are kept.
- Under `--fuse` the match-level fields follow a stated policy: `resolvable` is true if **any** brain can resolve the
  bytes (they can be quoted from that brain), `superseded_by` names a successor if **any** brain names one (cite it
  as current only after checking), and `sources` is the union. Reversing the order of the brains changes only the
  order of those lists.
- **`skipped[]`** -- declared brains that were not consulted, and why.

Cite the block **and** the brain. Everything chapter 5 says about `truncated`, `superseded_by` and `resolvable`
holds per match.

## When a brain returns nothing

`compound explain` shows why each brain planned as it did, side by side. A brain with no indices built scans; a brain
whose statistics are stale plans pessimistically; a brain with nothing on the subject returns an empty bundle, which
is an answer. `warnings` in the envelope carries each brain's degradations prefixed with its name.

## Next

The [`vitruvio-compound` skill](11-skills.md) walks an agent through choosing the project and the brains with the
user before running any of this.
