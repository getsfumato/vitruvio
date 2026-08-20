"""Running a plan, and the fixed tail that the optimiser cannot touch.

The tail is where the protocol's invariants are enforced, so none of it is negotiable:

* **``Accessibility`` before ``TopK``.** The other order spends the limit on blocks that are then hidden.
* **``Verify`` drops rather than flags.** Membership is checked *provably* -- an inclusion proof against the module's
  root, not just "the store gave me bytes" -- and a failure removes the row. ``Match.verified`` is therefore always
  true, which is what ``require_verified()`` presumes. A dropped row is recorded as a degradation, because silence
  there would hide corruption.
* **A reserve past the limit.** So a drop can be backfilled instead of shortening the bundle.
* **An unresolvable block is still a member.** It is emitted with empty content and ``resolvable=False`` -- a
  redacted block is a verifiable member whose bytes were destroyed under policy, and a caller must be able to tell
  that from corruption. But it is only admitted when it was reached by *identity* or by *association*: arriving from a
  content generator can only mean a stale index, and returning it would be reporting a match on content that no
  longer exists.

``truncated`` diverges from the SDK's scan on purpose. The scan sets it by comparing a complete match set against the
limit; an index plan never has the complete set, only a pool. So it is true whenever any generator did not exhaust its
domain, which is the only defensible reading of "there may be more".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from boltzmann.blocks.memory_type import MemoryType
from boltzmann.identity.digest import BlockId
from boltzmann.indices.base import IndexKind
from boltzmann.module.module import Module
from boltzmann.query.evidence import EvidenceBundle, Match, SourceRef
from boltzmann.query.request import Query

from vitruvio.planner import fusion
from vitruvio.planner.explain import Degradation
from vitruvio.planner.intent import Intent
from vitruvio.planner.ir import Metrics, Op, Plan
from vitruvio.planner.planner import Capabilities, CostBasedPlanner


@dataclass
class Executor:
    """
    Runs one plan against one set of modules.

    Attributes:
        planner (CostBasedPlanner): For the cached provenance view and the configuration.
        modules (dict[MemoryType, Module]): The installed modules.
        query (Query): What was asked.
        intent (Intent): The classification, which carries the fusion weights.
        capabilities (Capabilities): What may be consulted.
        analyze (bool): Whether to record per-node actuals.
    """

    planner: CostBasedPlanner
    modules: dict[MemoryType, Module]
    query: Query
    intent: Intent
    capabilities: Capabilities
    analyze: bool = False
    degradations: list[Degradation] = field(default_factory=list)

    def run(self, plan: Plan) -> tuple[EvidenceBundle, Metrics, float, list[Degradation]]:
        """
        Execute a plan.

        Args:
            plan (Plan): What to run.

        Returns:
            tuple[EvidenceBundle, Metrics, float, list[Degradation]]: The bundle, per-node actuals, the microseconds
            spent building the provenance view, and anything that had to be worked around.
        """
        metrics = Metrics.over(plan)
        ledger, prelude = self.planner.ledger_for(self.modules)

        candidates: dict[str, fusion.Candidate] = {}
        exhausted = True
        searched: list[MemoryType] = []
        masks: dict[MemoryType, set[str] | None] = {}

        for node_id, node in enumerate(plan.nodes):
            if node.op is Op.EMPTY:
                continue
            if node.scope is None or (not node.op.is_generator and node.op is not Op.EXACT_LOOKUP):
                continue

            memory_type = MemoryType(node.scope)
            module = self.modules.get(memory_type)
            if module is None:
                continue
            if memory_type not in searched:
                searched.append(memory_type)

            started = time.perf_counter()
            hits, node_exhausted = self._generate(node, module, plan, candidates)
            if node.op is Op.GRAPH_EXPAND:
                # GraphExpand is federated: its operator scope says where the plan placed the expansion, not which
                # module owns every reached block. Apply each reached block's owning-module mask, or a filter from the
                # seed scope can incorrectly discard a valid hit from another module.
                hits = self._filter_federated_hits(hits, masks)
            else:
                if memory_type not in masks:
                    held = self._mask(module)
                    masks[memory_type] = None if held is None else set(held)
                allowed = masks[memory_type]
                if allowed is not None:
                    hits = [hit for hit in hits if hit[0] in allowed]
            spent = (time.perf_counter() - started) * 1e6

            fusion.accumulate(
                candidates,
                node.op.value,
                hits,
                depth=1 if node.op is Op.GRAPH_EXPAND else 0,
                exact=node.op is Op.EXACT_LOOKUP,
            )
            exhausted = exhausted and node_exhausted
            if self.analyze:
                metrics.record(node_id, len(hits), spent)

        bundle = self._finalize(plan, candidates, ledger, searched, exhausted=exhausted, metrics=metrics)
        return bundle, metrics, prelude, self.degradations

    # --- Generators -----------------------------------------------------------

    def _generate(
        self,
        node: Any,
        module: Module,
        plan: Plan,
        candidates: dict[str, fusion.Candidate],
    ) -> tuple[list[tuple[str, float]], bool]:
        """
        Run one generator and return its ranked hits, plus whether it enumerated its whole domain.

        Args:
            node (Node): Which operator.
            module (Module): The module it runs over.
            plan (Plan): For reading a mask node's parameters.
            candidates (dict[str, fusion.Candidate]): Accumulated so far, which a graph expansion seeds from.

        Returns:
            tuple[list[tuple[str, float]], bool]: Hits, and whether the domain was exhausted.
        """
        parameters = node.parameters
        limit = int(parameters.get("k", self.query.hints.limit))

        if node.op is Op.EXACT_LOOKUP:
            return self._exact(module), True

        if node.op is Op.SEQ_SCAN:
            return self._sequential(module), True

        if node.op is Op.TERM_SCAN:
            return self._lexical(module, limit)

        if node.op in {Op.VECTOR_SEARCH, Op.BRUTE_VECTOR}:
            return self._semantic(module, node, limit)

        if node.op is Op.GRAPH_EXPAND:
            return self._associative(module, node, candidates)

        return [], True

    def _index(self, module: Module, kind: IndexKind) -> Any | None:
        """
        One of vitruvio's own indices, or ``None``.

        The SDK types ``module.indices`` as the ``Index`` Protocol, which declares only ``build`` and ``search``.
        Everything richer -- a facet filter, a term lookup, an ordinal translation -- is vitruvio's. Narrowing here
        means a conforming index from somewhere else is skipped rather than crashed on.
        """
        from vitruvio.indices import VitruvioIndex

        index = module.indices.get(kind.value)
        return index if isinstance(index, VitruvioIndex) else None

    def _mask(self, module: Module) -> tuple[str, ...] | None:
        """
        The pre-filter, as block identities.

        Identities rather than ordinals: ordinals are each index's internal numbering, and translating them across an
        index boundary couples two layouts together.

        ``None`` means "could not be evaluated, post-filter instead"; an empty tuple means "nothing matches". Those
        are different answers, and conflating them silently excludes everything.
        """
        from vitruvio.indices import (
            BitmapIndex,
            BTreeIndex,
            Combine,
            Facet,
            FacetClause,
            FacetQuery,
            OrderedKey,
            RangeQuery,
        )

        selected: set[str] | None = None
        scope = module.memory_type.value
        index = self._index(module, IndexKind.BITMAP) if self.capabilities.has(scope, IndexKind.BITMAP) else None

        clauses: list[FacetClause] = []
        if self.query.filters.subject:
            clauses.append(FacetClause(Facet.SUBJECT, (self.query.filters.subject,)))
        if self.query.filters.tags:
            clauses.append(FacetClause(Facet.TAG, tuple(self.query.filters.tags), combine=Combine.ALL))
        if clauses and isinstance(index, BitmapIndex):
            matching = index.matching(FacetQuery(clauses=tuple(clauses)))
            if matching is not None:
                selected = set(matching)

        filters = self.query.filters
        ordered = self._index(module, IndexKind.BTREE) if self.capabilities.has(scope, IndexKind.BTREE) else None
        if (filters.since or filters.until) and isinstance(ordered, BTreeIndex):
            ranged = {
                str(identity)
                for identity, _score in ordered.search(
                    RangeQuery(key=OrderedKey.OCCURRED_AT, low=filters.since, high=filters.until),
                    limit=0,
                )
            }
            selected = ranged if selected is None else selected & ranged
        return None if selected is None else tuple(sorted(selected))

    def _filter_federated_hits(
        self,
        hits: list[tuple[str, float]],
        masks: dict[MemoryType, set[str] | None],
    ) -> list[tuple[str, float]]:
        """Apply masks to graph hits in the module that actually owns each identity.

        A graph expansion can cross module boundaries. An identity is admitted when at least one installed owner
        admits it; residual payload checks remain the final correctness boundary for unavailable or stale masks.
        """
        admitted: list[tuple[str, float]] = []
        for identity, score in hits:
            owners = [
                (memory_type, module)
                for memory_type, module in self.modules.items()
                if BlockId.parse(identity) in module.composition.block_ids
            ]
            for memory_type, module in owners:
                if memory_type not in masks:
                    held = self._mask(module)
                    masks[memory_type] = None if held is None else set(held)
                allowed = masks[memory_type]
                if allowed is None or identity in allowed:
                    admitted.append((identity, score))
                    break
        return admitted

    def _exact(self, module: Module) -> list[tuple[str, float]]:
        """Resolve identity-shaped queries: a digest, or a label the hash map holds."""
        from vitruvio.indices import HashMapIndex, IdentityKey, IdQuery

        index = self._index(module, IndexKind.HASH_MAP)
        text = self.query.text.strip()
        if not isinstance(index, HashMapIndex) or not text:
            return []

        if text.startswith("sha256:"):
            query = IdQuery(identities=(text,))
        else:
            # A deliberate superset of the SDK's scan, which returns nothing for non-digest text in exact mode. A
            # label is close enough to an identity that resolving one is what a caller means.
            query = IdQuery(keys=((IdentityKey.LABEL, text), (IdentityKey.ALIAS, text)))
        return [(hit.block_id, hit.score) for hit in index.lookup(query).hits]

    def _sequential(self, module: Module) -> list[tuple[str, float]]:
        """
        The exhaustive fallback: the SDK's own scoring over every block.

        Reusing ``searchable_text`` and ``content_terms`` rather than reimplementing them is what makes the
        differential test against the scan meaningful -- a divergence here would look like a scoring bug.
        """
        from boltzmann.query.scan import content_terms, searchable_text

        terms = content_terms(self.query.text)
        if not terms:
            return [(str(identity), 1.0) for identity in module.block_ids]

        hits: list[tuple[str, float]] = []
        for identity in module.block_ids:
            if not module.store.is_resolvable(identity):
                continue
            try:
                block = module.get(identity)
            except Exception:
                continue
            haystack = " ".join(searchable_text(block)).casefold()
            present = sum(1 for term in terms if term in haystack)
            if present:
                hits.append((str(identity), present / len(terms)))
        hits.sort(key=lambda pair: (-pair[1], pair[0]))
        return hits

    def _lexical(self, module: Module, limit: int) -> tuple[list[tuple[str, float]], bool]:
        """BM25 over the inverted index, with the bitmap mask applied before scoring."""
        from vitruvio.indices import InvertedIndex, TermQuery, query_groups, query_terms

        index = self._index(module, IndexKind.INVERTED)
        if not isinstance(index, InvertedIndex) or not self.query.text.strip():
            return [], True

        mask = self._mask(module)
        # Translated at this index's own edge, from identities. `None` (no filter) and an empty mask (a filter that
        # admits nothing) stay distinguishable all the way through.
        allow = index.ordinals_for(mask) if mask is not None else None

        results = index.lookup(
            TermQuery(
                terms=query_terms(self.query.text),
                groups=query_groups(self.query.text),
                allow=allow,
            ),
            limit=limit,
        )
        return [(hit.block_id, hit.score) for hit in results.hits], results.exhausted

    def _semantic(self, module: Module, node: Any, limit: int) -> tuple[list[tuple[str, float]], bool]:
        """
        The vector probe.

        Degrades rather than failing when the embedder cannot run: the index is fine, we simply cannot make a probe
        vector, so this generator drops out *for this query* and the degradation is reported.
        """
        index = self._index(module, IndexKind.VECTOR)
        if index is None or not self.query.text.strip():
            return [], True

        from vitruvio.indices import VectorQuery

        try:
            results = index.search(
                VectorQuery(
                    text=self.query.text,
                    exact=node.op is Op.BRUTE_VECTOR,
                    effort=int(node.parameters.get("effort", 64)),
                ),
                limit=limit,
            )
        except Exception as error:
            self.degradations.append(
                Degradation(
                    kind="embedder_unavailable",
                    detail=f"{module.memory_type.value}: {error}",
                )
            )
            return [], True
        return [(str(identity), score) for identity, score in results], len(results) < limit

    def _associative(
        self, module: Module, node: Any, candidates: dict[str, fusion.Candidate]
    ) -> tuple[list[tuple[str, float]], bool]:
        """
        Expand from the **fused** hits rather than from one index's.

        Which means expansion follows consensus rather than whichever index happened to be consulted first -- the
        invariant expressed structurally rather than as a preference.

        Returns:
            tuple[list[tuple[str, float]], bool]: Hits, and whether the expansion enumerated its domain. The second
            value used to be hardcoded ``True`` at the call site, which made ``truncated`` unable to report the one
            thing it exists for whenever a graph plan dropped what it had reached.
        """
        from vitruvio.indices import FederatedGraphView, GraphIndex, TraversalQuery

        # Federated across every module that has a graph, because `derived_from` and `supersedes` live only in
        # provenance -- so "what was this derived from" is unanswerable from the derived module's own index.
        graphs = {
            memory_type.value: index
            for memory_type, held in self.modules.items()
            if self.capabilities.has(memory_type.value, IndexKind.GRAPH)
            and isinstance(index := self._index(held, IndexKind.GRAPH), GraphIndex)
        }
        if not graphs or not candidates:
            return [], True

        # Seeded from the *fused* hits rather than from one index's, so expansion follows consensus rather than whichever
        # generator happened to run first. That is the no-single-authority invariant expressed structurally.
        seeds = tuple(candidate.block_id for candidate in candidates.values() if candidate.depth == 0)
        if not seeds:
            return [], True

        limit = int(node.parameters.get("k", 20))
        max_nodes = limit * 4
        reached = FederatedGraphView(graphs).expand(
            TraversalQuery(
                seeds=seeds,
                depth=int(node.parameters.get("depth", 1)),
                decay=0.5,
                max_nodes=max_nodes,
            )
        )
        # Only installed blocks: a caller cannot resolve an identity that is not here, and the graph index reports the
        # external ones separately for the callers that want them.
        installed = {str(identity) for held in self.modules.values() for identity in held.composition.block_ids}
        resolvable = [(identity, score) for identity, score, _ in reached if identity in installed]

        # Two independent ways this pool is not the domain: the traversal stops at `max_nodes`, and what survives is
        # then cut to `k`. Excluding an *external* target is neither -- it can never be returned to a caller, so
        # counting it would leave `truncated` permanently true for any brain that cites something it does not hold.
        #
        # The ceiling test adds the seeds back because `max_nodes` bounds the visited set, which `reached` has already
        # had the seeds removed from. Without that, a traversal stopped by the ceiling could still report itself
        # exhausted -- and of the two ways to be wrong here, claiming completeness is the one that misleads.
        exhausted = len(resolvable) <= limit and len(reached) + len(seeds) < max_nodes
        return resolvable[:limit], exhausted

    # --- The fixed tail -------------------------------------------------------

    def _finalize(
        self,
        plan: Plan,
        candidates: dict[str, fusion.Candidate],
        ledger: Any,
        searched: list[MemoryType],
        *,
        exhausted: bool,
        metrics: Metrics,
    ) -> EvidenceBundle:
        """Fuse, hide, cut, resolve, verify, bundle."""
        limit = max(1, self.query.hints.limit)
        scored = fusion.fuse(candidates, self.intent.weights, k=self.planner.config.rrf_k)

        if not self.query.filters.include_superseded:
            scored = [
                (candidate, value)
                for candidate, value in scored
                if ledger.is_accessible(BlockId.parse(candidate.block_id))
            ]

        # Residual predicates run before the reserve/limit. Otherwise enough high-ranked non-matches can consume the
        # reserve and hide a valid candidate that appears later in an exhaustive generator.
        if self._has_block_filters():
            scored = [
                (candidate, value) for candidate, value in scored if self._candidate_matches_filters(candidate.block_id)
            ]

        # A reserve past the limit, so a verification drop is backfilled rather than shortening the bundle.
        reserve = limit * 2
        pool, dropped_at_limit = scored[:reserve], len(scored) > reserve
        normalized = fusion.normalize(pool)

        matches: list[Match] = []
        for candidate, value in normalized:
            if len(matches) >= limit:
                dropped_at_limit = True
                break
            match = self._verify(candidate, value, ledger)
            if match is not None:
                matches.append(match)

        return EvidenceBundle(
            matches=matches,
            verified_against={
                memory_type: self.modules[memory_type].root for memory_type in searched if memory_type in self.modules
            },
            # More conservative than the SDK's scan, and stated as such: an approximate pool may always be hiding
            # something, so the flag means "there may be more" rather than "the complete set exceeded the limit".
            truncated=dropped_at_limit or not exhausted,
        )

    # one return per reason a candidate is dropped; collapsing them would report the wrong reason.
    def _verify(self, candidate: fusion.Candidate, score: float, ledger: Any) -> Match | None:  # noqa: PLR0911
        """
        Resolve one candidate and prove its membership, or drop it.

        Three checks, not one: the store verifies the bytes hash to the identity, ``composition`` gives membership,
        and an inclusion proof against the module root makes that membership *provable*. Anything that fails is
        dropped and recorded -- never returned with ``verified=False``, because a caller that trusted such a row would
        be trusting exactly what the protocol says it must not.
        """
        identity = BlockId.parse(candidate.block_id)
        for memory_type, module in self.modules.items():
            if identity not in module.composition.block_ids:
                continue

            try:
                proof = module.inclusion_proof(identity)
                if not proof.verify(module.root):
                    self.degradations.append(
                        Degradation(
                            kind="verification_failed",
                            detail=f"{candidate.block_id} does not prove into {memory_type.value}",
                        )
                    )
                    return None
            except Exception as error:
                self.degradations.append(
                    Degradation(kind="verification_failed", detail=f"{candidate.block_id}: {error}")
                )
                return None

            resolvable = module.store.is_resolvable(identity)
            if not resolvable and not self._admissible_unresolvable(candidate):
                # Reached by a content generator, so a match on content that no longer exists: a stale index, not a
                # legitimate result.
                self.degradations.append(
                    Degradation(
                        kind="index_stale",
                        detail=f"{candidate.block_id} matched on content but its bytes are gone",
                    )
                )
                return None

            content: dict[str, Any] = {}
            sources: list[SourceRef] = []
            filters = self.query.filters
            if not resolvable and any((filters.subject, filters.tags, filters.since, filters.until, filters.evidence)):
                return None
            if resolvable:
                try:
                    block = module.get(identity)
                except Exception as error:
                    self.degradations.append(
                        Degradation(kind="verification_failed", detail=f"{candidate.block_id}: {error}")
                    )
                    return None
                content = block.payload()
                if not self._matches_filters(content):
                    return None
                sources = [
                    SourceRef(block_id=BlockId.parse(cited), locator=ledger.locators.get(identity))
                    for cited in (content.get("evidence") or [])
                    if isinstance(cited, str)
                ]

            return Match(
                block_id=identity,
                memory_type=memory_type,
                content=content,
                score=fusion.render(score),
                sources=sources,
                verified=True,
                resolvable=resolvable,
                superseded_by=ledger.superseded_by.get(identity),
            )
        return None

    def _matches_filters(self, content: dict[str, Any]) -> bool:
        """Apply every block-level predicate after resolution, including residual fallbacks.

        Index masks are an optimization. This check is the correctness boundary: a stale or unavailable mask may cost
        more work, but it cannot let a block outside the requested subject, tags, time window or evidence set through.
        """
        filters = self.query.filters
        if filters.subject and content.get("subject") != filters.subject:
            return False
        if filters.tags and not set(filters.tags).issubset(set(content.get("tags") or [])):
            return False
        occurred_at = content.get("occurred_at")
        if filters.since and (not occurred_at or str(occurred_at) < filters.since):
            return False
        if filters.until and (not occurred_at or str(occurred_at) > filters.until):
            return False
        if filters.evidence:
            held = {str(item) for item in content.get("evidence") or []}
            if not {str(item) for item in filters.evidence}.issubset(held):
                return False
        return True

    def _candidate_matches_filters(self, block_id: str) -> bool:
        """Resolve one candidate just far enough to apply residual predicates before ``TopK``."""
        identity = BlockId.parse(block_id)
        for module in self.modules.values():
            if identity not in module.composition.block_ids or not module.store.is_resolvable(identity):
                continue
            try:
                return self._matches_filters(module.get(identity).payload())
            except Exception:
                return False
        return False

    def _has_block_filters(self) -> bool:
        """Whether candidate payloads need predicate checks before the result limit."""
        filters = self.query.filters
        return bool(filters.subject or filters.tags or filters.since or filters.until or filters.evidence)

    @staticmethod
    def _admissible_unresolvable(candidate: fusion.Candidate) -> bool:
        """
        Whether an unresolvable block may still be returned.

        Yes when it was reached by identity or by association -- "the redacted source you cited is still a member" is
        an answer worth giving. No when a content generator produced it, because content that cannot be read cannot
        have matched.
        """
        return any(origin in {Op.EXACT_LOOKUP.value, Op.GRAPH_EXPAND.value} for origin in candidate.origins)
