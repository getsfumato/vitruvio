"""The planner: the IR, the cost model, intent, fusion, and the properties that justify the design.

Three of these tests are load-bearing rather than incidental:

* **The crossover.** An exhaustive scan wins on a small module and loses at scale. If that does not happen, the cost
  model is decoration and a heuristic router would do.
* **Monotonicity.** Adding an index never worsens the chosen plan. This is the property that justifies exhaustive
  enumeration over a memo structure, and it would not hold under a heuristic pre-filter.
* **The validity rule.** No plan with a single scored generator survives when two are available. Checked structurally
  against the chosen plan, not just against the output, because the invariant is about how the answer was reached.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from boltzmann.query.request import Query, QueryFilters, QueryHints, RetrievalMode

from vitruvio.kernel import PlannerConfig
from vitruvio.planner import (
    Calibration,
    Candidate,
    CostBasedPlanner,
    IntentKind,
    Objective,
    Op,
    PlanBuilder,
    classify,
    damped_conjunction,
    damped_union,
    estimate,
    fuse,
    normalize,
    plan_recall,
    render,
)
from vitruvio.planner.planner import Capabilities
from vitruvio.stats import Freshness, ModuleStats, StatsVersion, TermStats, VectorStats

if TYPE_CHECKING:
    pass

STRUCTURAL = ["hash_map", "bitmap", "btree"]


def statistics(blocks: int, *, vector: bool = False) -> dict[str, ModuleStats]:
    """A semantic module of a given size, with a measured vector recall curve when asked for."""
    vectors = (
        {
            "text": VectorStats(
                vectors=blocks,
                blocks=blocks,
                dimensions=384,
                model_tag="fake/deterministic/384/cos@1",
                recall_curve=((32, 0.85), (64, 0.92), (128, 0.96)),
            )
        }
        if vector
        else {}
    )
    return {
        "semantic": ModuleStats(
            memory_type="semantic",
            version=StatsVersion(root="sha256:" + "ab" * 32, leaf_fingerprint="fp"),
            freshness=Freshness.fresh(),
            cardinality=blocks,
            resolvable_count=blocks,
            average_block_bytes=400.0,
            terms=TermStats(
                doc_count=blocks,
                vocabulary=blocks * 3,
                average_length=15.0,
                document_frequency={"t:fourier": max(1, blocks // 50)},
                postings=blocks * 10,
                tail_max_frequency=2,
            ),
            vectors=vectors,
        )
    }


def capabilities(*kinds: str) -> Capabilities:
    """Capabilities over a single semantic module."""
    return Capabilities(available={"semantic": list(kinds)}, usable={"semantic": frozenset(kinds)})


def chosen_plan(planner: CostBasedPlanner, query: Query, caps: Capabilities):
    """Plan without executing, and return the winner's explanation."""
    intent = planner._classify(query, {}, ("semantic",), caps)
    available = planner._generators(("semantic",), caps)
    scored = []
    for plan in planner._enumerate(query, ("semantic",), caps, intent):
        estimates = estimate(plan, planner.statistics, planner.calibration)
        recall = plan_recall(plan, estimates, intent.authority)
        valid, _ = Objective.admissible(plan, len(available), exact_intent=intent.is_exact)
        scored.append((plan, estimates, recall, valid))

    indexed_best = max(
        (
            recall
            for plan, _, recall, valid in scored
            if valid and any(plan[node].op is not Op.SEQ_SCAN for node in plan.generators())
        ),
        default=0.0,
    )
    floor = min(intent.recall_floor, indexed_best) if indexed_best else 0.0

    feasible = [
        (planner._objective.score(estimates.total_cost, recall), plan, estimates, recall)
        for plan, estimates, recall, valid in scored
        if valid and recall >= floor - 1e-9
    ]
    feasible.sort(key=lambda entry: (entry[0], entry[1].signature))
    return feasible[0]


def a_query(text: str = "periodica fourier", **hints: object) -> Query:
    """A query with no filters."""
    return Query(text=text, filters=QueryFilters(), hints=QueryHints(**hints))  # type: ignore[arg-type]


class TestIR:
    def test_identical_nodes_are_interned(self) -> None:
        """A mask consumed twice must be one node, or it is evaluated twice and costed twice."""
        builder = PlanBuilder()
        first = builder.add(Op.BITMAP_FILTER, scope="semantic", clauses=1)
        second = builder.add(Op.BITMAP_FILTER, scope="semantic", clauses=1)
        assert first == second

    def test_a_shared_node_has_several_dependents(self) -> None:
        """Which is the arena earning its place over a tree."""
        builder = PlanBuilder()
        mask = builder.add(Op.BITMAP_FILTER, scope="semantic", clauses=1)
        left = builder.add(Op.TERM_SCAN, scope="semantic", inputs=[mask], k=10)
        right = builder.add(Op.VECTOR_SEARCH, scope="semantic", inputs=[mask], k=10)
        plan = builder.finish(builder.add(Op.FUSE, inputs=[left, right]))
        assert set(plan.dependents(mask)) == {left, right}

    def test_the_signature_is_stable_across_builds(self) -> None:
        """Golden plan snapshots key on this, so it must not depend on anything but structure."""

        def build() -> str:
            builder = PlanBuilder()
            scan = builder.add(Op.TERM_SCAN, scope="semantic", k=10, terms=2)
            return builder.finish(builder.add(Op.BUNDLE, inputs=[scan])).signature

        assert build() == build()

    def test_the_signature_changes_with_a_parameter(self) -> None:
        builder = PlanBuilder()
        first = builder.finish(builder.add(Op.TERM_SCAN, scope="semantic", k=10))
        other = PlanBuilder()
        second = other.finish(other.add(Op.TERM_SCAN, scope="semantic", k=40))
        assert first.signature != second.signature

    def test_a_filter_is_not_a_generator(self) -> None:
        """Counting one would let a single-authority plan through the validity rule."""
        assert Op.BITMAP_FILTER.is_generator is False
        assert Op.RANGE_SCAN.is_generator is False
        assert Op.TERM_SCAN.is_generator is True

    def test_masks_and_rows_are_different_output_types(self) -> None:
        """The distinction is what makes pushdown expressible at all."""
        from vitruvio.planner.ir import Output

        assert Op.BITMAP_FILTER.output is Output.MASK
        assert Op.TERM_SCAN.output is Output.ROWS


class TestCostModel:
    def test_embedding_a_query_can_cost_more_than_reading_a_small_module(self) -> None:
        """The single number that argues for a cost model over a heuristic router."""
        builder = PlanBuilder()
        scan = builder.add(Op.SEQ_SCAN, scope="semantic", terms=3, selectivity=1.0)
        sequential = builder.finish(builder.add(Op.BUNDLE, inputs=[scan]))

        other = PlanBuilder()
        probe = other.add(Op.VECTOR_SEARCH, scope="semantic", vectors=50, dimensions=384, k=10, effort=64)
        vector = other.finish(other.add(Op.BUNDLE, inputs=[probe]))

        # 50 blocks, not 200: the crossover sits at roughly embed_text / get_block, which is about 100 blocks. The
        # point is that the crossover exists and the model finds it, not that it is in any particular place.
        stats = statistics(50, vector=True)
        assert estimate(sequential, stats).total_cost < estimate(vector, stats).total_cost

    def test_a_residual_costs_a_block_read_per_row(self) -> None:
        """The ratio against a bitmap word scan is why pushdown is costed rather than assumed."""
        builder = PlanBuilder()
        scan = builder.add(Op.TERM_SCAN, scope="semantic", k=100, rows=100, postings=100, terms=1)
        residual = builder.add(Op.RESIDUAL, scope="semantic", inputs=[scan], decode=True, selectivity=0.5)
        plan = builder.finish(builder.add(Op.BUNDLE, inputs=[residual]))

        estimates = estimate(plan, statistics(1000))
        calibration = Calibration()
        assert estimates.cost[residual] == pytest.approx(estimates.rows[scan] * calibration.get_block)

    def test_a_conjunction_is_damped_toward_over_estimating(self) -> None:
        """Under-estimating a candidate pool costs the answer; over-estimating costs latency."""
        independent = 0.1 * 0.1 * 0.1
        assert damped_conjunction([0.1, 0.1, 0.1]) > independent

    def test_a_union_is_damped_toward_under_estimating(self) -> None:
        """Generators overlap on a good query -- that is the point of fusing them."""
        assert damped_union([100, 100]) < 200

    def test_the_calibration_digest_changes_with_a_constant(self) -> None:
        """It keys the plan cache, so a re-measured constant must invalidate it."""
        assert Calibration().digest() != Calibration(get_block=90.0).digest()


class TestRecallObjective:
    def test_a_second_generator_strictly_improves_expected_recall(self) -> None:
        """This is how "no single index is authoritative" enters the objective rather than being bolted on."""
        authority = {"TermScan": 0.5, "VectorSearch": 0.75}

        builder = PlanBuilder()
        one = builder.add(Op.TERM_SCAN, scope="semantic", k=10, recall=0.95, rows=10, postings=10, terms=1)
        single = builder.finish(builder.add(Op.BUNDLE, inputs=[one]))

        other = PlanBuilder()
        lexical = other.add(Op.TERM_SCAN, scope="semantic", k=10, recall=0.95, rows=10, postings=10, terms=1)
        semantic = other.add(Op.VECTOR_SEARCH, scope="semantic", k=10, recall=0.92, vectors=10, dimensions=384)
        both = other.finish(other.add(Op.FUSE, inputs=[lexical, semantic]))

        stats = statistics(1000, vector=True)
        single_recall = plan_recall(single, estimate(single, stats), authority)
        both_recall = plan_recall(both, estimate(both, stats), authority)
        assert both_recall > single_recall

    def test_every_authority_prior_is_below_one_for_an_index(self) -> None:
        """The numeric form of the claim that no one index can be trusted alone."""
        from vitruvio.planner.intent import PROFILES

        for kind, (_, authority, _) in PROFILES.items():
            if kind in {IntentKind.EXACT, IntentKind.NAVIGATIONAL}:
                continue  # an exact lookup and an exhaustive scan genuinely do see everything
            for name, value in authority.items():
                if name == "SeqScan":
                    continue
                assert value < 1.0, f"{kind.value}/{name} claims total coverage"

    def test_the_validity_rule_rejects_a_single_generator_plan(self) -> None:
        builder = PlanBuilder()
        one = builder.add(Op.TERM_SCAN, scope="semantic", k=10)
        plan = builder.finish(builder.add(Op.BUNDLE, inputs=[one]))

        admissible, reason = Objective.admissible(plan, available_generators=2, exact_intent=False)
        assert admissible is False
        assert "authoritative" in (reason or "")

    def test_an_exact_lookup_is_exempt(self) -> None:
        """A digest has one answer; a second opinion has nothing to add."""
        builder = PlanBuilder()
        one = builder.add(Op.TERM_SCAN, scope="semantic", k=10)
        plan = builder.finish(builder.add(Op.BUNDLE, inputs=[one]))
        assert Objective.admissible(plan, 2, exact_intent=True)[0] is True

    def test_the_rule_does_not_fire_when_only_one_generator_exists(self) -> None:
        """Otherwise a brain with one index rejects every plan that uses it."""
        builder = PlanBuilder()
        one = builder.add(Op.TERM_SCAN, scope="semantic", k=10)
        plan = builder.finish(builder.add(Op.BUNDLE, inputs=[one]))
        assert Objective.admissible(plan, 1, exact_intent=False)[0] is True

    def test_the_objective_prices_a_miss_against_latency(self) -> None:
        objective = Objective(weight=1.0, miss_cost=250_000)
        assert objective.score(1_000, 1.0) < objective.score(1_000, 0.5)

    def test_lambda_zero_optimises_for_latency_alone(self) -> None:
        objective = Objective(weight=0.0, miss_cost=250_000)
        assert objective.score(1_000, 0.5) == objective.score(1_000, 1.0)


class TestCrossover:
    """The claim the whole cost model exists to make."""

    def test_an_exhaustive_scan_wins_on_a_small_module(self) -> None:
        planner = CostBasedPlanner(PlannerConfig(), statistics=statistics(20))
        _, plan, _, _ = chosen_plan(planner, a_query(), capabilities(*STRUCTURAL, "inverted"))
        assert {plan[node].op for node in plan.generators()} == {Op.SEQ_SCAN}

    def test_the_index_wins_at_scale(self) -> None:
        planner = CostBasedPlanner(PlannerConfig(), statistics=statistics(3000))
        _, plan, _, _ = chosen_plan(planner, a_query(), capabilities(*STRUCTURAL, "inverted"))
        assert Op.TERM_SCAN in {plan[node].op for node in plan.generators()}

    def test_the_same_code_makes_both_decisions(self) -> None:
        """Nothing is configured differently between the two: only the measured cardinality changes."""
        small = CostBasedPlanner(PlannerConfig(), statistics=statistics(20))
        large = CostBasedPlanner(PlannerConfig(), statistics=statistics(50_000))
        caps = capabilities(*STRUCTURAL, "inverted")

        _, small_plan, _, _ = chosen_plan(small, a_query(), caps)
        _, large_plan, large_estimates, _ = chosen_plan(large, a_query(), caps)
        assert small_plan.signature != large_plan.signature
        assert large_estimates.total_cost < estimate(small_plan, statistics(50_000)).total_cost


class TestMonotonicity:
    def test_adding_an_index_never_worsens_the_chosen_plan(self) -> None:
        """The property that justifies exhaustive enumeration over a memo, and would fail under a heuristic."""
        query = a_query()
        for blocks in (100, 1_000, 10_000):
            without = CostBasedPlanner(PlannerConfig(), statistics=statistics(blocks))
            with_vector = CostBasedPlanner(PlannerConfig(), statistics=statistics(blocks, vector=True))

            fewer = chosen_plan(without, query, capabilities(*STRUCTURAL, "inverted"))[0]
            more = chosen_plan(with_vector, query, capabilities(*STRUCTURAL, "inverted", "vector"))[0]
            assert more <= fewer + 1e-6, f"adding a vector index worsened J at {blocks} blocks"


class TestIntent:
    def test_a_digest_is_an_exact_lookup(self) -> None:
        assert classify("sha256:" + "ab" * 32).kind is IntentKind.EXACT

    def test_a_partial_digest_is_not(self) -> None:
        """Anchored, so a query that merely mentions a digest is still a text query."""
        assert classify("what cites sha256:" + "ab" * 32).kind is not IntentKind.EXACT

    def test_filters_with_no_text_are_navigational(self) -> None:
        """ "The episodes from May" is a filter evaluation, not a relevance ranking."""
        assert classify("", has_filters=True).kind is IntentKind.NAVIGATIONAL

    def test_unseen_terms_make_a_query_semantic_whatever_it_looks_like(self) -> None:
        """The best feature available, and free: it comes out of the document frequencies."""
        assert classify("blockchain", out_of_vocabulary=0.9).kind is IntentKind.SEMANTIC

    def test_a_sentence_is_semantic(self) -> None:
        assert classify("how do I decompose a periodic function").kind is IntentKind.SEMANTIC

    def test_a_short_known_keyword_list_is_lexical(self) -> None:
        assert classify("fourier series").kind is IntentKind.LEXICAL

    def test_expansion_depth_makes_a_query_associative(self) -> None:
        assert classify("fourier", expand_depth=2).kind is IntentKind.ASSOCIATIVE

    def test_a_mode_pins_the_kind_and_says_so(self) -> None:
        intent = classify("anything at all", mode=RetrievalMode.LEXICAL)
        assert intent.kind is IntentKind.LEXICAL
        assert any("mode=" in feature for feature in intent.features)

    def test_semantic_mode_still_admits_the_lexical_generator(self) -> None:
        """A hint restricts the space; it may not be used to violate the no-single-authority invariant."""
        from vitruvio.planner.intent import admissible_generators

        assert admissible_generators(RetrievalMode.SEMANTIC) is None

    def test_lexical_mode_excludes_the_vector_generator(self) -> None:
        """The one narrowing a hint may do: the caller asked to match words, not neighbours."""
        from vitruvio.planner.intent import admissible_generators

        permitted = admissible_generators(RetrievalMode.LEXICAL)
        assert permitted is not None
        assert "VectorSearch" not in permitted

    def test_classification_is_deterministic(self) -> None:
        first = classify("descomponer una funcion periodica", out_of_vocabulary=0.3)
        second = classify("descomponer una funcion periodica", out_of_vocabulary=0.3)
        assert first == second


class TestFusion:
    def test_absence_contributes_nothing(self) -> None:
        """The alternative rewards a document for a generator merely having had a list."""
        present = Candidate(block_id="sha256:aa")
        present.contribute("TermScan", 1, 0.9)
        absent = Candidate(block_id="sha256:bb")
        absent.contribute("VectorSearch", 1, 0.9)

        scored = {
            candidate.block_id: value
            for candidate, value in fuse({"a": present, "b": absent}, {"TermScan": 1.0, "VectorSearch": 0.5})
        }
        assert scored["sha256:aa"] > scored["sha256:bb"]

    def test_an_exact_hit_outranks_everything(self) -> None:
        """An identity match is not a relevance judgement, so it must not compete on rank."""
        exact = Candidate(block_id="sha256:aa", exact=True)
        exact.contribute("ExactLookup", 1, 1.0)
        similar = Candidate(block_id="sha256:bb")
        for rank in range(1, 4):
            similar.contribute("TermScan", rank, 0.99)

        ordered = fuse({"a": exact, "b": similar}, {"TermScan": 1.0, "ExactLookup": 1.0})
        assert ordered[0][0].block_id == "sha256:aa"

    def test_expansion_competes_rather_than_overriding(self) -> None:
        """Which is what keeps expand_depth from destroying precision."""
        direct = Candidate(block_id="sha256:aa")
        direct.contribute("TermScan", 1, 0.5, depth=0)
        expanded = Candidate(block_id="sha256:bb")
        # Depth belongs to the contribution, not to the constructor: a candidate's depth is how it was reached.
        expanded.contribute("GraphExpand", 1, 0.9, depth=2)

        ordered = fuse({"a": direct, "b": expanded}, {"TermScan": 0.5, "GraphExpand": 0.6})
        assert ordered[0][0].block_id == "sha256:aa"

    def test_ordering_is_deterministic_on_a_tie(self) -> None:
        """Golden snapshots depend on this, and the last tie-break matches the SDK's own."""
        left = Candidate(block_id="sha256:bb")
        left.contribute("TermScan", 1, 0.5)
        right = Candidate(block_id="sha256:aa")
        right.contribute("TermScan", 1, 0.5)

        ordered = fuse({"b": left, "a": right}, {"TermScan": 1.0})
        assert [candidate.block_id for candidate, _ in ordered] == ["sha256:aa", "sha256:bb"]

    def test_normalisation_makes_the_best_match_one(self) -> None:
        candidates = {}
        for position, identity in enumerate(("sha256:aa", "sha256:bb"), start=1):
            candidate = Candidate(block_id=identity)
            candidate.contribute("TermScan", position, 1.0 / position)
            candidates[identity] = candidate

        normalized = normalize(fuse(candidates, {"TermScan": 1.0}))
        assert normalized[0][1] == pytest.approx(1.0)


class TestScoreRendering:
    def test_a_score_is_a_string_with_fixed_precision(self) -> None:
        assert render(0.5) == "0.50"
        assert render(1.0) == "1.00"

    def test_negative_zero_never_appears(self) -> None:
        """A valid float and an absurd score."""
        assert render(-0.0) == "0.00"
        assert not render(-1e-12).startswith("-")

    def test_rendering_is_monotone(self) -> None:
        assert render(0.6) >= render(0.4)

    def test_the_same_value_by_two_paths_renders_identically(self) -> None:
        """Decimal(repr(x)) rather than Decimal(x): the shortest repr, not the binary tail."""
        assert render(0.1 + 0.2) == render(0.3)

    def test_rounding_is_half_even(self) -> None:
        """Half-up accumulates a systematic upward bias across a bundle."""
        assert render(0.125) == "0.12"
        assert render(0.135) == "0.14"

    def test_every_rendering_matches_the_protocol_format(self) -> None:
        import re

        for value in (0.0, 0.004, 0.5, 0.999, 1.0, 2.0):
            assert re.fullmatch(r"[01]\.\d{2}", render(value)), value
