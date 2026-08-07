# CLI reference

Generated from the command declarations by `python -m vitruvio.cli.reference`. Do not edit by hand: a reference that
disagrees with the parser is worse than none, because it costs a turn to discover.

Every command accepts the global options `--brain`, `--config`, `--actor`, `--actor-kind`, `--json`, `--quiet`,
`--no-color` and `--verbose`. Pass `--json` whenever something other than a person is reading.

## `vitruvio brain`

Select a brain and inspect its state.

### `vitruvio brain use` `path`

Record a brain as the interactive default.

### `vitruvio brain list`

List the brains this machine knows about, most recently used first.

### `vitruvio brain init` `[path]` `--policy` `--force`

Create a brain, and a vitruvio.toml beside it.

### `vitruvio brain state`

Print what is installed, at which version, and where it came from.

### `vitruvio brain verify`

Recompute every module's Merkle root from its blocks and compare.

### `vitruvio brain history` `--limit`

List the retained snapshots, most recent first.

### `vitruvio brain info`

Print the per-module anatomy: roots, block counts, and which indices are registered.

## `vitruvio source`

Register canonical evidence.

### `vitruvio source register` `path` `--media-type` `--origin` `--license-id` `--retention-policy` `--normalize-with`

Register a source as canonical evidence.

### `vitruvio source replace` `path` `--supersedes` *(required)* `--media-type` `--origin` `--license-id` `--normalize-with`

Register a newer edition and record that it supersedes an older block.

### `vitruvio source put` `path` `--media-type`

Store bytes addressably without registering a canonical block.

## `vitruvio task`

Define processing tasks, validate a model's candidates, and commit them.

### `vitruvio task define` `source` `--allowed` `--require` `--instructions` `--task-id` `--replacing`

Define what a model is being asked to do with one canonical block.

### `vitruvio task rederive` `replacing` `--source` `--allowed`

Define a task that re-derives one existing block from its evidence.

### `vitruvio task schema` `--task` *(required)*

Print the JSON Schema a proposal for this task must satisfy.

### `vitruvio task validate` `candidates` `--task` *(required)*

Run the validation gate over a candidate set, committing nothing.

### `vitruvio task commit` `candidates` `--task` *(required)*

Validate and commit a candidate set.

## `vitruvio ingest`

Run a source through registration, proposal, validation and commit.

### `vitruvio ingest run` `path` `--media-type` `--proposer` `--allowed` `--normalize-with` `--subject` `--origin` `--dry-run`

Register a source, propose knowledge from it, validate and commit.

### `vitruvio ingest pipelines`

List the normalization pipelines this build can run.

## `vitruvio index`

Build and inspect the derived indices.

### `vitruvio index list`

List every registered index: kind, module, how much it holds, and where it lives.

### `vitruvio index build` `--memory-type` `--force`

Build or refresh the indices.

### `vitruvio index stats` `--memory-type`

Print the statistics the query planner costs against.

### `vitruvio index verify`

Check every index against the composition it claims to describe.

### `vitruvio index gc` `--apply`

Delete index files that no longer belong to a declared index.

## `vitruvio query`

Retrieve evidence from the brain.

### `vitruvio query search` `[text]` `--memory-type` `--subject` `--since` `--until` `--tag` `--evidence` `--include-superseded` `--mode` `--limit` `--expand-depth` `--content`

Search the brain and print the Evidence Bundle.

### `vitruvio query resolve` `block-id`

Read one block by identity.

### `vitruvio query prove` `block-id` `--memory-type` *(required)*

Produce a verified Merkle inclusion proof for one block.

### `vitruvio query explain` `[text]` `--memory-type` `--subject` `--since` `--until` `--tag` `--include-superseded` `--mode` `--limit` `--expand-depth` `--analyze`

Show how a query would be answered, and what the alternatives cost.

## `vitruvio retain`

Drop, supersede, demote, prune and redact -- the five removal mechanisms.

### `vitruvio retain plan-drop` `blocks...` `--memory-type` *(required)* `--rederive-against`

Show what dropping these blocks would take with it.

### `vitruvio retain drop` `blocks...` `--memory-type` *(required)* `--reason` `--rederive-against` `--yes`

Exclude blocks from a module, cascading through provenance.

### `vitruvio retain drop-producer` `producer` `--kind` `--producer-version` `--memory-type` `--reason` `--yes`

Drop everything one producer derived.

### `vitruvio retain supersede` `block` `--supersedes` *(required)* `--memory-type` *(required)* `--reason`

Record that one block takes precedence over another.

### `vitruvio retain demote` `block` `--memory-type` *(required)* `--reason`

Lower a block's retrieval priority without removing it.

### `vitruvio retain prune` `--apply`

Reclaim blobs unreachable from every retained root.

### `vitruvio retain redact` `block` `--memory-type` *(required)* `--reason` *(required)* `--yes`

Destroy a block's bytes while a retained root still names it.

### `vitruvio retain policy`

Show the retention policy in force, and what it permits.

## `vitruvio dist`

Publish a brain to a registry, and install one from a registry.

### `vitruvio dist pack` `--tag` `--module`

Build the OCI artifact locally, without pushing.

### `vitruvio dist push` `[reference]` `--tag` `--module` `--force` `--anonymous` `--insecure` `--local`

Publish the brain to a registry.

### `vitruvio dist plan-pull` `[reference]` `--tag` `--module` `--anonymous` `--insecure` `--local`

Report what a pull would transfer, before transferring it.

### `vitruvio dist pull` `[reference]` `--tag` `--module` `--anonymous` `--insecure` `--local`

Install a published brain.

### `vitruvio dist tags` `[reference]` `--anonymous` `--insecure` `--local`

List the tags a repository holds.

## `vitruvio registry`

Manage registry credentials, and check a registry before publishing.

### `vitruvio registry login` `host` `--username` `--token-stdin` `--from-docker`

Store credentials for a registry host.

### `vitruvio registry logout` `host`

Remove a host's stored credentials.

### `vitruvio registry whoami` `[host]`

Report which credentials would be used, and where they came from.

### `vitruvio registry list`

List the hosts vitruvio holds credentials for.

### `vitruvio registry check` `[reference]` `--username` `--token-stdin` `--anonymous` `--insecure`

Push a probe artifact shaped exactly like a brain, and report what the registry accepted.

## `vitruvio inspect`

Read the brain's structure: roots, modules, blocks, resolvability.

### `vitruvio inspect resolvability`

Report which blocks are readable, which are tombstoned, and which are simply absent.

### `vitruvio inspect roots`

Print every installed module's Merkle root, and the snapshot digest that pins the set.

### `vitruvio inspect module` `memory-type` `--limit`

Print one module's shape and a sample of its block identities.

### `vitruvio inspect block` `block-id`

Read one block by identity.

### `vitruvio inspect prove` `block-id` `--memory-type` *(required)*

Produce a Merkle inclusion proof for one block, already checked against the module's root.

### `vitruvio inspect doctor`

Check the environment: what is installed, what is configured, and what would fail.

## `vitruvio config`

Inspect and edit the project configuration.

### `vitruvio config show` `--effective`

Print the configuration, and where it came from.

### `vitruvio config path`

Print the path of the configuration file that would be used.

### `vitruvio config get` `key`

Print one value, addressed by dotted key.

### `vitruvio config set` `key` `value` `--file`

Set one value, addressed by dotted key.

### `vitruvio config validate`

Check that the configuration parses and satisfies the schema.

## `vitruvio skills`

Install the agent-facing skills into a repository.

### `vitruvio skills list`

List the skills this build ships, and what each one covers.

### `vitruvio skills install` `--into` `--skill` `--force`

Copy the skills into a repository so an agent can read them.

## `vitruvio completion`

Print a shell completion script.

## `vitruvio search`

Search the brain and print the Evidence Bundle.
