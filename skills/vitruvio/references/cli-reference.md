# CLI reference

Generated from the command declarations by `python -m vitruvio.cli.reference`. Do not edit by hand: a reference that
disagrees with the parser is worse than none, because it costs a turn to discover.

Every command accepts the global options `--brain`, `--config`, `--actor`, `--actor-kind`, `--assisted-by`, `--json`,
`--quiet`, `--no-color` and `--verbose`. Pass `--json` whenever something other than a person is reading.

## `vitruvio project`

Manage a project and the brains it holds.

### `vitruvio project init` `name` `--description` `--namespace`

Create a project in the working directory.

### `vitruvio project register` `[name]` `--path`

Make a project addressable by `--project <name>` from any directory.

### `vitruvio project list`

List every project this machine can address by name, and the brains each one holds.

### `vitruvio project forget` `name`

Drop a project from this machine's registry.

### `vitruvio project show`

List the project's brains, where each lives, and where each publishes.

### `vitruvio project add` `name` `--path` `--description` `--reference` `--no-create` `--no-publish`

Add a brain to the project, creating its layout.

### `vitruvio project remove` `name`

Unregister a brain from the project.

## `vitruvio brain`

Select a brain and inspect its state.

### `vitruvio brain use` `brain`

Record a brain as this project's default, for a shell.

### `vitruvio brain list`

List this project's brains, then the ones this machine remembers.

### `vitruvio brain init` `[path]` `--policy` `--force` `--governed` `--trust-root` `--sign-with` `--govern-quorum`

Create a brain, and a vitruvio.toml beside it.

### `vitruvio brain migrate` `--to` *(required)* `--governed` `--trust-root` `--sign-with` `--govern-quorum` `--allow-partial` `--dry-run` `--report` `--force-report`

Recreate a legacy brain's current accessible state under the current protocol.

### `vitruvio brain state`

Print what is installed, at which version, and where it came from.

### `vitruvio brain verify`

Recompute every module's Merkle root from its blocks and compare.

### `vitruvio brain history` `--limit` `--graph`

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

### `vitruvio source pull` `[name]` `--all-sources` `--dry-run` `--limit` `--refetch` `--option`

Acquire from a declared source and register what is new as canonical evidence.

### `vitruvio source status`

What sources the selected brain declares, and whether each one can be used.

### `vitruvio source kinds`

Every source kind this installation can construct, and where each came from.

### `vitruvio source scaffold` `kind` `--force`

Write a starter plugin for a source kind vitruvio does not ship.

### `vitruvio source add` `name` `--kind` *(required)* `--path` `--media-type` `--normalize-with` `--license-id` `--option`

Declare a source in vitruvio.toml.

### `vitruvio source remove` `name`

Undeclare a source. Nothing it ever registered is touched.

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

### `vitruvio query search` `[text]` `--memory-type` `--subject` `--since` `--until` `--tag` `--classes` `--evidence` `--include-superseded` `--mode` `--limit` `--expand-depth` `--content`

Search the brain and print the Evidence Bundle.

### `vitruvio query resolve` `block-id`

Read one block by identity.

### `vitruvio query prove` `block-id` `--memory-type` *(required)*

Produce a verified Merkle inclusion proof for one block.

### `vitruvio query explain` `[text]` `--memory-type` `--subject` `--since` `--until` `--tag` `--classes` `--include-superseded` `--mode` `--limit` `--expand-depth` `--analyze`

Show how a query would be answered, and what the alternatives cost.

## `vitruvio compound`

Ask several brains of one project the same question.

### `vitruvio compound search` `[text]` `--brains` `--all` `--fuse` `--memory-type` `--subject` `--since` `--until` `--tag` `--evidence` `--include-superseded` `--mode` `--limit` `--expand-depth` `--content`

Search several brains of this project at once and print the composed evidence.

### `vitruvio compound explain` `[text]` `--brains` `--all` `--memory-type` `--subject` `--since` `--until` `--tag` `--include-superseded` `--mode` `--limit` `--expand-depth` `--analyze`

Show how each brain of a compound would answer the query, side by side.

## `vitruvio browse` `--memory-type`

Read and query the brain in a terminal workspace that also shows the executed plan and selected indices.

## `vitruvio catalog`

Classify canonical evidence with portable schemes and classes.

### `vitruvio catalog show`

List declared schemes, classes, hierarchy and effective sources.

### `vitruvio catalog apply` `path` `--dry-run`

Validate and atomically apply a TOML or JSON ``vitruvio.catalog/v1`` manifest.

### `vitruvio catalog scheme` `name` `--exclusive` `--dry-run`

Declare a classification scheme.

### `vitruvio catalog class` `scheme` `label` `--broader` `--dry-run`

Declare a class, optionally below existing ``scheme/label`` classes.

### `vitruvio catalog place` `source` `classes` `--dry-run`

Place one canonical block in one or more ``scheme/label`` classes.

### `vitruvio catalog browse` `classes`

Browse the intersection of one or more classes, descendants included.

### `vitruvio catalog path` `schemes` `[path]`

List a virtual path using the requested scheme order.

## `vitruvio auth`

Authenticate and govern a brain with detached SSH signatures.

### `vitruvio auth keys`

List public Ed25519 keys available through the current SSH agent.

### `vitruvio auth status` `--snapshot` `--offered`

Verify integrity and report the independent authenticity state.

### `vitruvio auth sign` `key` `--snapshot` `--scope`

Explicitly sign a snapshot through ssh-agent; no private key enters Vitruvio.

### `vitruvio auth pin` `--trust-root` `--source`

Pin the current trust root (TOFU) or an out-of-band digest.

### `vitruvio auth attribution`

Show which declared actors are vouched by valid signature subjects.

### `vitruvio auth plan-rotate` `trust-root` `--output` *(required)* `--force`

Build once the exact revision document a distributed quorum will countersign.

### `vitruvio auth countersign` `plan` `key` `--output` *(required)* `--force`

Countersign the exact revision document in a rotation plan.

### `vitruvio auth rotate` `--trust-root` `--plan` `--sign-with` `--record`

Commit a local trust-root change or a planned distributed rotation.

### `vitruvio auth revoke` `key` `--sign-with` `--record` `--retired-from` `--compromised-from`

Retire a key or withdraw its signatures from a compromised snapshot onward.

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

### `vitruvio dist push` `[reference]` `--tag` `--module` `--force` `--anonymous` `--insecure` `--local` `--all`

Publish the brain to a registry.

### `vitruvio dist fetch` `[reference]` `--tag` `--module` `--reconcile` `--reason` `--anonymous` `--insecure` `--local`

Bring another history here without adopting it, and reconcile it when that decides nothing for you.

### `vitruvio dist plan-pull` `[reference]` `--tag` `--module` `--ignore-vector-indices` `--anonymous` `--insecure` `--local`

Report what a pull would transfer, before transferring it.

### `vitruvio dist pull` `[reference]` `--tag` `--module` `--ignore-vector-indices` `--allow-rollback` `--anonymous` `--insecure` `--local`

Install a published brain.

### `vitruvio dist tags` `[reference]` `--anonymous` `--insecure` `--local`

List the tags a repository holds.

## `vitruvio reconcile`

Join another history into this one: merge, rebase or squash, and decide what did not apply.

### `vitruvio reconcile plan` `theirs` `--ancestor`

Report what joining another history would produce, and what each strategy would cost.

### `vitruvio reconcile merge` `theirs` `--reason` *(required)* `--ancestor`

Join both histories, naming both as parents.

### `vitruvio reconcile rebase` `theirs` `--reason` *(required)* `--ancestor`

Replay their history onto this one, minting new snapshot identities.

### `vitruvio reconcile squash` `theirs` `--reason` *(required)* `--ancestor`

Collapse their snapshots into one.

### `vitruvio reconcile status`

Report the reconciliation being resolved, if there is one.

### `vitruvio reconcile resolve` `[block]` `--admit` `--reject` `--prefer`

Decide what did not apply — one block at a time, or all of them in an interactive workspace.

### `vitruvio reconcile accept-removals`

State that the work this reconciliation removes may go.

### `vitruvio reconcile continue`

Conclude the reconciliation now that its questions are answered.

### `vitruvio reconcile abort`

Abandon the reconciliation being resolved.

### `vitruvio reconcile tree` `[theirs]` `--ancestor`

Show where two histories parted, and what each has added since.

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

Read the brain's structure: roots, modules, blocks, content, resolvability.

### `vitruvio inspect resolvability`

Report which blocks are readable, which are tombstoned, and which are simply absent.

### `vitruvio inspect roots`

Print every installed module's Merkle root, and the snapshot digest that pins the set.

### `vitruvio inspect module` `memory-type` `--limit`

Print one module's shape and a sample of its block identities.

### `vitruvio inspect blocks` `memory-type` `--limit` `--offset` `--contains`

List what a module holds, one line per block, in the module's own order.

### `vitruvio inspect content` `digest` `--out` `--open` `--media-type` `--page` `--width`

Show, open or export the bytes a block names.

### `vitruvio inspect links` `block-id` `--limit`

Print the provenance records that name a block: where it came from, and what has been done to it.

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

### `vitruvio config embedder`

Inspect and test the configured embedding providers.

#### `vitruvio config embedder list`

List the embedding providers this build knows, and whether each can run.

#### `vitruvio config embedder test` `--which` `--text`

Embed one phrase and report what came back.

## `vitruvio skills`

Install the agent-facing skills into a repository.

### `vitruvio skills list`

List the skills this build ships, and what each one covers.

### `vitruvio skills install` `--into` `--skill` `--force`

Copy the skills into a repository so an agent can read them.

## `vitruvio completion` `shell`

Print a completion script for bash, zsh or fish.

## `vitruvio bench`

Measure recall and latency against a corpus with known answers.

### `vitruvio bench corpus` `path` `--tier` `--seed` `--queries`

Write a generated corpus to disk and keep it.

## `vitruvio update` `--check` `--yes` `--version`

Check for a newer vitruvio, and install it.

## `vitruvio search` `[text]` `--memory-type` `--subject` `--since` `--until` `--tag` `--classes` `--evidence` `--include-superseded` `--mode` `--limit` `--expand-depth` `--content`

Search the brain and print the Evidence Bundle.
