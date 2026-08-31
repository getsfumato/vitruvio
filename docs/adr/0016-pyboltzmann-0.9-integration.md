# ADR-0016: pyboltzmann 0.9 integration

**Status:** accepted

## Context

pyboltzmann 0.9 makes three protocol concepts first-class that Vitruvio did not expose: catalog declarations over
canonical evidence, detached SSH authenticity with governed trust-root revisions, and canonical actor identifiers
plus `assisted_by` provenance. Treating these as CLI-only features would split protocol behaviour across interfaces;
silently rewriting old actors or adding governance to an existing genesis would change hashed history while claiming
continuity that does not exist.

## Decision

Catalog, authenticity and migration are deep runtime modules declared in the operation catalogue and forwarded by
the generated `BrainService` facade. The CLI only parses files and renders their wire results.

Catalog declarations stay in semantic memory through the SDK. Vitruvio adds an atomic `vitruvio.catalog/v1`
manifest and class-aware retrieval, including the planner's final correctness filter. There is no Vitruvio metadata
database.

Authenticity uses `ssh-agent` exclusively. Private key paths and key bytes have no runtime or configuration field.
Integrity and authenticity are reported independently. Trust-root pinning remains consumer-local, while rotation and
revocation commit governed protocol revisions.

Actors use the protocol's two canonical forms — lowercase address or namespaced name — for all new writes.
`assisted_by` is a repeatable structured list and records the assisting actor, kind, display name and model in
provenance v2. Legacy actor strings remain loadable so an old brain can be inspected and migrated; they fail only
when asked to author a new write.

Migration creates a new brain from current accessible state. It never mutates in place, never claims to preserve
snapshot or provenance identities, and fails atomically unless the operator explicitly accepts a partial result.
Governance belongs in the new genesis, which gives old brains a legitimate upgrade path without retroactive signing.

Installing an ancestor snapshot is an explicit rollback under 0.9 and is exposed only through
`dist pull --allow-rollback`.

## Consequences

- Catalog metadata travels, verifies and reconciles like other knowledge.
- A signature proves authorship only under an accepted trust root and consumer policy; intact unsigned data remains
  distinguishable from corrupt data.
- Existing configurations with informal actor names can still read but must adopt canonical identifiers before the
  next write.
- Migration can preserve content-derived knowledge ids, but new provenance and snapshot ids are expected and
  documented.
- The temporary migration command can be deprecated after supported legacy populations have moved; the report schema
  remains the audit boundary.
