# 13. Projects: one project, many brains

A brain is the unit of *publication*. If somebody might want one subject without the other four, those are five
brains — each publishable, installable and droppable on its own.

What a project adds is the part they share: one actor, one retention policy, one embedder, one registry account.

```console
vitruvio project init facultad --actor you@example.com
vitruvio project add algebra --description "apuntes de algebra"
vitruvio project add analisis-ii
vitruvio project add fisica-i
vitruvio project show
```

```toml
[project]
name = "facultad"

[actor]
id = "you@example.com"

[brains.algebra]
path = "./brains/algebra"
description = "apuntes de algebra"

[brains.analisis-ii]
path = "./brains/analisis-ii"
```

## Selecting a brain by name

```console
vitruvio --brain algebra ingest run apuntes.md
vitruvio --brain algebra search "que es una base"
```

The name is tried before the path, so a stray `./algebra` directory cannot shadow the project member. Working on the
wrong brain in silence is the failure that ordering exists to prevent.

`$VITRUVIO_BRAIN=algebra` works the same way, which is how a container or a CI job picks a subject without editing a
file. A project holding exactly one brain needs no flag at all.

## Publishing: log in once

```console
vitruvio registry login docker.io --from-docker
vitruvio project show
```

```
project     facultad
publishes   docker.io/you  (from your registry login)

  algebra            docker.io/you/facultad-algebra
  analisis-ii        docker.io/you/facultad-analisis-ii
  fisica-i           docker.io/you/facultad-fisica-i
```

Each brain derives `<namespace>/<project>-<brain>`. Nobody writes a registry reference per subject, and adding a
subject is adding a directory.

The namespace comes from `[registry].namespace` when set, and otherwise from whichever account you are logged in as.
That fallback is the whole point of `--from-docker`.

The **project prefix is load-bearing**: without it, two projects that each hold a brain called `notes` would publish
to one repository and overwrite each other — and the second one would find out when a pull returned the wrong
subject.

## Publishing the whole project

```console
vitruvio dist push --all --tag v1
```

```
ok    algebra            docker.io/you/facultad-algebra:v1
ok    analisis-ii        docker.io/you/facultad-analisis-ii:v1
skip  fisica-i           nothing committed yet
```

An empty brain is **skipped, not failed**: a subject you have not started yet is the ordinary state of a project. A
brain that fails for a real reason does not stop the others, and the command exits non-zero at the end.

Run `vitruvio registry check <repository>` once before the first push to a new host. Docker Hub is verified to accept
the protocol's manifest shape; the next registry is the one that refuses.

## Overriding one brain

```toml
[brains.tesis]
path = "./brains/tesis"
reference = "ghcr.io/you/tesis"     # this one goes somewhere else entirely
```

## Names are repository components

`analisis-ii`, not `Análisis II`. Lowercase letters, digits and single separators. Checked when the file is read
rather than when the artifact is pushed — a registry rejecting the name after a pack gives an error that says nothing
about which of the two names was wrong.

## Removing

```console
vitruvio project remove analisis-ii
```

Unregisters it and prints where the layout still is. It never deletes: "remove it from this project" and "destroy it"
are different requests, and a brain may be the only copy of what it holds.
