# Architecture Decision Records

One file per decision, numbered in the order they were taken, never renumbered. A record states the context
that forced a choice, the choice, and what it costs — including the option that was rejected and why, because
that is the part a future reader cannot reconstruct.

A decision that turns out wrong gets a new record that supersedes the old one. The old file stays: the point
of the series is the reasoning, and deleting a mistake deletes the reasoning that led to the correction.

| ADR | Decision |
|---|---|
| [0001](0001-monorepo-layout-and-package-seams.md) | Monorepo layout and package seams |
| [0002](0002-configuration-and-brain-selection.md) | Configuration, brain selection, and where vitruvio writes |
| [0003](0003-the-service-layer.md) | The service layer, and why apps are thin |
| [0004](0004-output-contract-and-exit-codes.md) | The output contract and exit codes |
| [0005](0005-statistics-and-the-cost-model.md) | Statistics, the cost model, and recall as part of the objective |
| [0006](0006-the-vector-engine.md) | The vector engine, and what makes a dump publishable |
| [0007](0007-registry-credentials-and-endpoints.md) | Registry credentials, endpoints and the preflight |
| [0008](0008-ingest-and-the-proposer-boundary.md) | Normalization determinism, and the proposer boundary |
| [0009](0009-retention-and-the-five-mechanisms.md) | Retention, and why there are five mechanisms |
| [0010](0010-projects-and-derived-repositories.md) | Projects, and where a brain publishes |
| [0011](0011-declarative-sources-and-plugins.md) | Declarative sources, plugins, and dedup without a cursor |
| [0012](0012-the-terminal-interface.md) | The terminal interface, and one renderer for both of them |
| [0013](0013-decomposing-the-service-layer.md) | Decomposing the service layer, and what stayed on the facade |
| [0014](0014-reconciliation.md) | Reconciliation, and the four choices vitruvio had to make itself |
| [0015](0015-compound-retrieval.md) | Compound retrieval across a project's brains, and why it fuses by rank |
