---
name: vitruvio-onboarding
description: Guide a person through creating a first Boltzmann brain with vitruvio, standalone or inside a project, governed or ungoverned, and confirm it works. Use when the user is new to vitruvio, asks to set up, create, start or initialize a brain or project, asks whether they need a project, or asks what governed means before creating anything.
allowed-tools: Bash(vitruvio:*), Read
---

# A first brain

<!-- Mirrors docs/onboarding/*.mdx. Change the flow in both places. -->

Creating a brain is one command. Two things about that command cannot be changed afterwards — whether the brain
is governed, and, less strictly, whether it stands alone or belongs to a project — so this skill is mostly about
getting those two decisions made **by the user, on purpose**, before anything is written. Nothing here creates a
brain until both are decided.

Everything below assumes the habits of the `vitruvio` skill: pass `--json`, branch on `ok` then `error.code`, and
read `warnings` even when `ok` is true.

## 1. Look before asking

```bash
vitruvio --version
vitruvio project list --json
vitruvio config path --json
```

- If `vitruvio` is not installed, give the user the install line and stop. Do not run it for them: an installer is
  their decision.

  ```bash
  curl -fsSL https://vitruvio.sh/install.sh | sh
  vitruvio --version
  ```

- If `project list` names a project, or `config path` finds a `vitruvio.toml` above the working directory, the user
  is already inside a project. The path from here is *add a brain to it*, never *init a second project*. Show them
  what it holds with `vitruvio project show --json` before proposing anything.

## 2. Decision one: standalone, or in a project

Explain the difference in one paragraph, in the user's language. A brain is the unit of publication. A **project**
is what several brains share — one actor, one retention policy, one embedder, one registry account — in one
`vitruvio.toml`, with a repository per brain derived as `<namespace>/<project>-<brain>`.

Here a recommendation is welcome: one subject and one writer → standalone; several subjects that publish separately,
several people, or a brain that will live beside others → a project. A standalone brain can join a project later with
`project add NAME --path PATH --no-create`, so the cheaper mistake is standalone.

## 3. Decision two: governed, or ungoverned

Treat both `brain init` and the destination of `brain migrate` as an irreversible trust choice. Governance belongs
in genesis and cannot be added to that same brain later. If the user has not chosen, stop before either command and
ask, in the user's language, whether the new brain should be **governed** or **ungoverned**. Offer exactly those two
protocol choices, keeping the words `governed` and `ungoverned` visible. Do not rename them as personal, shared,
public, or similar categories; do not recommend or select a default; and do not create anything until the user
answers.

Explain the choices as:

- **ungoverned** — integrity, provenance and retention still apply, but the head has no trust root and remains
  unsigned; or
- **governed** — a trust root names the public keys whose signatures consumers may accept.

Never let the CLI defaults make this decision silently: `brain init` defaults to ungoverned, while migration
defaults to governed. For ungoverned, omit `--governed` on init and pass `--no-governed` explicitly on migrate.

For a governed brain, do not choose authorities on the user's behalf. Ask for all of these before creating it:

1. Each authority's canonical two-field Ed25519 public key (`ssh-ed25519 BASE64`), never a private key.
2. The canonical actor `subject` that key represents.
3. The scopes that key receives: `ingest`, `commit`, `drop:canonical`, `redact`, `govern`, or `propose`.
4. `govern_quorum`, and which authorized fingerprints currently loaded in `ssh-agent` will sign the genesis.
   `vitruvio auth keys --json` lists them; `ssh-add` is the user's step, not yours.

Explain the permission boundary while asking: scopes authorize a signature in the eyes of a verifier; they do not
grant filesystem access or stop somebody modifying a local copy. OS permissions control local writes, retention
policy controls allowed removal operations, registry credentials control publication, and the trust root controls
which resulting snapshots consumers call authorized.

Use `--governed --sign-with FINGERPRINT` only when the user explicitly wants every supplied key associated with the
configured actor and granted every scope. For distinct subjects or least-privilege scopes, author a reviewed
`TrustRoot` TOML/JSON document from the answers and pass `--trust-root`; do not take the all-scope shortcut merely
because it is shorter. A trust root must include at least one active `govern` holder, and its quorum must be
reachable. Expect the CLI to warn when the quorum equals the number of `govern` holders — losing one key would then
freeze governance permanently — and relay that warning rather than swallowing it.

## 4. Decision three: actor and policy

Every write is attributed, so `brain init` and `project init` need `--actor`: a lowercase address
(`ana@example.org`) or a lowercase namespaced name (`openai/codex`). Ask which one identifies the user; do not
invent one.

`brain init --policy` takes `conservative` (the default), `permissive` or `archival`. Unlike governance, leaving the
default is acceptable when the user has no opinion — say that the default was taken.

## 5. Create — four recipes

Always with `--json` and `--actor ACTOR`. Replace the placeholders with what the user answered.

Standalone, ungoverned:

```bash
vitruvio --json --actor ACTOR brain init PATH
```

Standalone, governed:

```bash
vitruvio --json --actor ACTOR brain init PATH --governed --sign-with FINGERPRINT --govern-quorum N
vitruvio --json --actor ACTOR brain init PATH --trust-root root.toml          # distinct subjects or scopes
```

In a project, ungoverned — `project init` only if step 1 found no project:

```bash
vitruvio --json --actor ACTOR project init NAME --namespace HOST/ACCOUNT     # --namespace only if they know where it publishes
vitruvio --json --project NAME project add BRAIN --description "..."
```

In a project, governed. `project add` has no governance flags and **always creates an ungoverned brain**, so the
genesis comes from `brain init` and `project add` only declares it:

```bash
vitruvio --json --actor ACTOR project init NAME
vitruvio --json --project NAME --actor ACTOR brain init ./brains/BRAIN --governed --sign-with FINGERPRINT
vitruvio --json --project NAME project add BRAIN --path ./brains/BRAIN --no-create
```

Run inside the project directory, `brain init` finds the project's `vitruvio.toml` and writes no second one; the
envelope's `data.config_file` is `null` in that case, which is correct.

## 6. Confirm, and report both results separately

```bash
vitruvio --json --brain PATH brain verify
vitruvio --json --brain PATH auth status
vitruvio --json --brain PATH brain state
vitruvio --json --project NAME project show          # in a project
```

`brain verify` is integrity; `auth status` is authenticity. Report each on its own line. On an ungoverned brain,
`auth status` reporting no trust root is the expected description, not a failure. Then run
`vitruvio --json --brain PATH inspect doctor` and relay anything it flags.

## 7. First evidence

```bash
vitruvio --json --brain PATH source register FILE
vitruvio --json --brain PATH index build
vitruvio --json --brain PATH search "TEXT"
```

Say what came back is an evidence bundle — block ids and provenance — and that the prose is the user's, or yours,
with citations only to block ids the brain returned.

## 8. Where to go next

- interpreting the evidence with a model → `vitruvio-ingest`
- publishing, or installing somebody else's brain → `vitruvio-dist`
- a published copy already exists, or a push exits 8 → `vitruvio-sync`
- one question across several brains of the project → `vitruvio-compound`
- a pre-0.9 brain to bring forward → `brain migrate`; its destination is a genesis, so step 3 applies, and it
  defaults to governed

## Never

- Run `brain init`, `project add` or `brain migrate` before both decisions are made and stated back to the user.
- Choose the governance, or rename the two choices into friendlier categories.
- Ask for, read, or pass a private key. Only fingerprints from `auth keys` and public keys ever appear.
- Run `brain use`; pass `--project` and `--brain` instead.
- Pass `--force` to anything during onboarding.
- Offer `project add` as the way to create a governed brain.
- Run `browse`: it is an interface for a person and refuses `--json`.
