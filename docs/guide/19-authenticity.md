# Authenticity

Integrity and authenticity answer different questions. `brain verify` proves that the blocks match their hashes and
module roots. `auth status` also asks whether valid, authorized SSH signatures support the claimed head. A brain may
therefore be intact and still be unsigned, unpinned or signed by a key outside its trust root.

Vitruvio never reads a private key. Signing uses an Ed25519 key already offered by `ssh-agent`:

```console
ssh-add ~/.ssh/id_ed25519
vitruvio auth keys
vitruvio auth status
vitruvio auth trust-root
```

The fingerprint printed by `auth keys` is the identifier accepted by `--sign-with` and `auth sign`.

## Start governed

Governance must be in the genesis; it cannot be asserted retroactively over an existing history. The concise path
synthesizes revision 1 from one or more agent keys, gives them every signing scope and associates them with the
configured actor:

```console
vitruvio --actor maintainer@example.org brain init ./brain \
  --governed --sign-with SHA256:REPLACE_WITH_FINGERPRINT
```

Repeat `--sign-with` and set `--govern-quorum 2` when two independent keys must approve a trust-root change. For
separate scopes or subjects, author a TOML or JSON `TrustRoot` document and pass `--trust-root root.toml`. The root
travels in the snapshot; the consumer pins its digest, not a mutable list maintained elsewhere.

Signing is always explicit:

```console
vitruvio auth sign SHA256:REPLACE_WITH_FINGERPRINT
vitruvio auth status
vitruvio auth attribution
```

`auth trust-root` is the human-readable governance inventory: the root digest and revision, consumer pin, quorum,
each public-key fingerprint, its canonical subject, scopes and whether it is active at the inspected snapshot. Pass
`--snapshot` to audit an earlier version. It prints an explicit ungoverned result when no root exists; an unsigned
ungoverned brain is not thereby corrupt.

`auth attribution` compares actors in the head's provenance with the `subject` entries vouched for by accepted
keys. It does not infer that a machine key belongs to a person.

For a chronological audit, `vitruvio history` (also available as `vitruvio brain history`) lists every reachable
snapshot, including histories joined by reconciliation, with its actors, integrity result and authenticity state.
`--graph` preserves the DAG view while adding actor and authorization columns. A block's row in `vitruvio browse`
goes one step deeper: its authorship tab connects that block's creation provenance to the historical snapshot and
signature subjects that can—or cannot—verify the asserted actor.

## Pinning is a consumer decision

On first trusted contact, review the trust-root digest out of band and pin it:

```console
vitruvio auth pin --source out_of_band
vitruvio auth status
```

An explicit digest can be supplied with `--trust-root`. A pin is local consumer state: publishing a brain cannot
force its readers to trust a replacement root.

`[authenticity]` controls evaluation:

```toml
[authenticity]
unsigned = "refuse"
required_signatures = 2
allow_propose_head = false
```

The default warns on unsigned brains so legacy data remains readable. It does not silently call it authentic.

## Installation is gated before adoption

`dist pull` retrieves the remote history into the content-addressed store, evaluates its detached records and only
then decides whether to move the local head. An unsigned head follows the configured `unsigned` policy: `warn` and
`permit` may install it as explicitly unauthenticated, while `refuse` rejects it. Once a signature is present, a
head evaluated as `unauthorized` is refused before adoption. That makes `required_signatures`, trust-root authority
and signing scope hard installation gates rather than warning-only diagnostics. The successful pull result includes
the evaluated `authenticity` state; it never folds that state into integrity verification.

## Rotation and revocation

For a distributed rotation, create the exact document once, send that immutable plan to the quorum, collect detached
records, and commit the same bytes:

```console
vitruvio auth plan-rotate next-root.toml --output rotation.json
vitruvio auth countersign rotation.json SHA256:KEY_A --output key-a.json
vitruvio auth countersign rotation.json SHA256:KEY_B --output key-b.json
vitruvio auth rotate --plan rotation.json --record key-a.json --record key-b.json
```

A local quorum can use `auth rotate --trust-root next-root.toml --sign-with ...`. `auth revoke` distinguishes
prospective retirement (`--retired-from`) from compromise at an already published snapshot
(`--compromised-from`); they are intentionally not interchangeable.
