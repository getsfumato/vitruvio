# ADR-0010: Projects, And Where A Brain Publishes

**Decision status:** Accepted.

## Context

A brain is the unit of *publication*, not the unit of work. A degree has a subject per brain, an agency a client per
brain, a team a service per brain — and the reason to keep them apart rather than in one brain is that each is then
publishable, installable and droppable on its own. Somebody who wants one subject should not have to pull five.

Until now `vitruvio.toml` addressed exactly one brain (`[brain].path`) and one repository
(`[registry].reference`). Six subjects meant six configuration files and six directories, which loses the thing they
have in common: one actor, one retention policy, one embedder, one registry account. Six copies of those drift, and
six brains that drift retrieve differently for reasons nobody can see.

## Decision

### A project is a set of named brains sharing one configuration

```toml
[project]
name = "facultad"

[registry]
namespace = "docker.io/you"

[brains.algebra]
path = "./brains/algebra"

[brains.analisis-ii]
path = "./brains/analisis-ii"
description = "apuntes"
```

`[brain].path` still works and is what a project of one should keep using. The two forms coexist rather than one
superseding the other, because a single-brain project is the common case and making it declare a name would be
ceremony for nothing.

### A brain is selected by name, and the name wins

`--brain algebra` and `--brain ./brains/algebra` both work, and the **name is tried first**. Within a project the
names are the vocabulary, and a stray directory in the working tree that happens to share a name must not shadow the
member — silently operating on the wrong brain is the failure worth spending a lookup to avoid. `$VITRUVIO_BRAIN`
takes a name too, which is how a container or a CI job picks a subject without editing a file.

A project holding exactly one brain needs no flag at all. With two or more it is a real question, and the error asks
it by listing them — it does **not** fall back to a saved pointer, which is
[ADR-0002](0002-configuration-and-brain-selection.md)'s amendment and the reason a name is trustworthy.

### The project is selected by name too, from any directory

Added in M4. `[projects]` in the state file maps a project's name to its `vitruvio.toml`, written by `project init`
and managed by `project register` / `list` / `forget`, so `--project facultad --brain analisis-numerico` states an
invocation's whole context from anywhere.

That pair is what makes concurrency free. A project per client and a subject per brain is the shape this ADR
already assumed; what it did not follow through on is that people then work in **several at once** — three agents,
three projects, three brains — and every layer below the flags is either directory-dependent or machine-global.
Two commands that name their project and brain share no mutable state and cannot influence one another.

The registry holds names and nothing else: every entry is a path to a committed file. A project is still configured
in exactly one place, and losing the registry costs the shorthand rather than any knowledge.

### The repository is derived, not written per brain

`<namespace>/<project>-<brain>`, so `facultad` + `algebra` publishes to `docker.io/you/facultad-algebra`. A brain may
override with its own `reference`, and usually does not.

The project prefix is not decoration. Without it, two projects that each hold a brain called `notes` publish to one
repository and overwrite each other — and the second one finds out when a pull returns the wrong subject.

**The namespace itself falls back to whichever registry account is logged in.** That is the point of
`registry login --from-docker`: log in once, and adding a subject to a project is adding a directory rather than
editing a registry reference.

Note the asymmetry with credentials, which is deliberate. `credential_for` refuses to read Docker's config unless
asked, because reading a *token* silently would make "which account am I publishing as" depend on a file vitruvio
does not own. `account_for` reads it by default, because reading a *username* only ever proposes a destination —
which is then printed by `project show` before anything is pushed, and overridden by one line of configuration.

### Names are validated as repository components, at load time

`analisis-ii`, not `Análisis II`. Enforced when the configuration is read rather than discovered at push time,
because the alternative is a registry rejecting the name after the artifact has already been packed — and the error
a registry returns for a malformed name says nothing about which of the two names was wrong.

### `dist push --all` publishes the project, and skips rather than fails

An **empty** brain is skipped, not attempted. A project where one subject has not been started yet is the ordinary
state, and letting it come back as a failed push would make `--all` exit non-zero on a perfectly healthy project
until every last brain had something in it.

A brain that fails for a real reason does not stop the others. Publishing five of six and being told which one did
not go is better than publishing two and stopping, because the four that would have worked are still not published
and nobody knows that either. The command exits non-zero if anything genuinely failed.

## Consequences

- Verified end to end against **real Docker Hub**: a three-subject `facultad` project, `--brain algebra` writing to
  the brain it names, `dist push --all` publishing each to its own derived repository, and a fresh install pulling
  one back, verifying it against its Merkle roots and searching it.
- `project remove` never deletes a layout, and prints where it still is. "Remove it from this project" and "destroy
  it" are different requests, and a brain may be the only copy of what it holds.
- `brain list` shows the project's brains and the machine's remembered ones as **two lists**. Merging them would make
  a name that works here look the same as a path that worked somewhere else last week.
- `resolve(require_brain=False)` exists for the `project` commands. A project that holds no brains yet is the state
  `project show` most needs to be able to report, and requiring a brain to describe a project that has none would
  make the command useless exactly when it is most wanted.
