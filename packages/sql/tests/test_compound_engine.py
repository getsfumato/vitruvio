"""SQL over several brains at once: one database, a ``brain`` column, and every root reported by brain."""

from __future__ import annotations

from typing import Any

import pytest
from boltzmann.blocks.memory_type import MemoryType
from boltzmann.blocks.semantic import SemanticBlock, SemanticKind
from boltzmann.module.composition import Composition
from boltzmann.module.module import Module
from boltzmann.store.memory import MemoryBlockStore

from vitruvio.kernel import UsageError
from vitruvio.sql import Similarity, SqlBrain, SqlEngine

# The `brain` fixture's type lives in conftest.py, which a test module cannot import by name.
Brain = Any


@pytest.fixture
def other(brain: Brain) -> dict[MemoryType, Module]:
    """A second brain: two facts of its own, plus one block it shares with the first -- content-addressed, so the
    same identity -- and no provenance module at all."""
    store = MemoryBlockStore()
    own = [
        SemanticBlock(kind=SemanticKind.FACT, label="Group axioms", statement="closure, identity", subject="Algebra"),
        SemanticBlock(kind=SemanticKind.FACT, label="Ring axioms", statement="two operations", subject="Algebra"),
    ]
    shared = brain.named["physics"]
    ids = [store.put_block(block) for block in [*own, shared]]
    return {MemoryType.SEMANTIC: Module(MemoryType.SEMANTIC, store, Composition(MemoryType.SEMANTIC, ids))}


def _engine(brain: Brain, other: dict[MemoryType, Module], **options: Any) -> SqlEngine:
    return SqlEngine(brains={"physics": SqlBrain(brain.modules), "algebra": SqlBrain(other)}, **options)


class TestUnion:
    def test_a_bare_table_is_every_brain_with_a_brain_column(self, brain: Brain, other: Any) -> None:
        with _engine(brain, other) as engine:
            outcome = engine.query("SELECT brain, count(*) FROM semantic GROUP BY brain")
        assert outcome.rows == [["algebra", 3], ["physics", 5]]
        assert outcome.brains == ["physics", "algebra"]

    def test_a_shared_block_is_a_row_per_brain_and_distinct_counts_it_once(self, brain: Brain, other: Any) -> None:
        with _engine(brain, other) as engine:
            outcome = engine.query("SELECT count(*), count(DISTINCT id) FROM semantic WHERE kind = 'fact'")
        assert outcome.rows == [[7, 6]]

    def test_a_qualified_table_reads_one_brain(self, brain: Brain, other: Any) -> None:
        with _engine(brain, other) as engine:
            outcome = engine.query("SELECT label FROM algebra.semantic WHERE subject = 'Algebra'")
        assert outcome.rows == [["Group axioms"], ["Ring axioms"]]
        assert outcome.tables == ["algebra.semantic"]

    def test_brains_can_be_joined_to_each_other(self, brain: Brain, other: Any) -> None:
        sql = "SELECT a.label FROM algebra.semantic a JOIN physics.semantic p ON p.id = a.id"
        with _engine(brain, other) as engine:
            assert engine.query(sql).rows == [["Physics fact 0"]]

    def test_blocks_spans_every_module_of_every_brain(self, brain: Brain, other: Any) -> None:
        with _engine(brain, other) as engine:
            rows = engine.query("SELECT brain, memory_type, count(*) FROM blocks GROUP BY ALL").rows
        assert ["algebra", "semantic", 3] in rows
        assert ["physics", "episodic", 3] in rows

    def test_an_unknown_brain_is_refused(self, brain: Brain, other: Any) -> None:
        with _engine(brain, other) as engine, pytest.raises(UsageError, match="no brain this compound consults"):
            engine.query("SELECT * FROM chemistry.semantic")

    def test_the_brains_are_part_of_the_signature(self, brain: Brain, other: Any) -> None:
        with _engine(brain, other) as engine:
            compound = engine.query("SELECT count(*) FROM semantic").signature
        with SqlEngine(brain.modules) as engine:
            single = engine.query("SELECT count(*) FROM semantic").signature
        assert compound != single


class TestHonesty:
    def test_roots_are_reported_per_brain(self, brain: Brain, other: Any) -> None:
        with _engine(brain, other) as engine:
            outcome = engine.query("SELECT count(*) FROM semantic")
        assert outcome.verified_against == {
            "algebra.semantic": str(other[MemoryType.SEMANTIC].root),
            "physics.provenance": str(brain.modules[MemoryType.PROVENANCE].root),
            "physics.semantic": str(brain.modules[MemoryType.SEMANTIC].root),
        }

    def test_each_brain_hides_by_its_own_ledger(self, brain: Brain, other: Any) -> None:
        """The old fact is superseded in physics; algebra has no provenance, so nothing there can be hidden."""
        with _engine(brain, other) as engine:
            outcome = engine.query("SELECT count(*) FROM semantic WHERE label = 'Old fact'")
        assert outcome.rows == [[0]]
        assert outcome.hidden == {"algebra.semantic": 0, "physics.semantic": 1}, "counted per brain, never summed"
        assert outcome.exact is False
        assert "algebra.provenance" in outcome.not_installed
        assert "of algebra" in outcome.approximate[0]["reason"]

    def test_a_qualified_read_depends_only_on_that_brain(self, brain: Brain, other: Any) -> None:
        with _engine(brain, other) as engine:
            outcome = engine.query("SELECT count(*) FROM physics.semantic")
        assert outcome.exact is True
        assert set(outcome.verified_against) == {"physics.provenance", "physics.semantic"}
        assert outcome.hidden == {"physics.semantic": 1}

    def test_a_missing_module_is_named_with_its_brain(self, brain: Brain, other: Any) -> None:
        with _engine(brain, other) as engine:
            outcome = engine.query("SELECT brain, count(*) FROM episodic GROUP BY brain")
        assert outcome.rows == [["physics", 3]]
        assert "algebra.episodic" in outcome.not_installed

    def test_verification_finds_a_block_in_whichever_brain_holds_it(self, brain: Brain, other: Any) -> None:
        with _engine(brain, other) as engine:
            outcome = engine.query("SELECT DISTINCT id FROM semantic", verify=True, include_superseded=True)
        assert outcome.verified_rows == outcome.row_count == 8


class TestSimilarity:
    class Scorer:
        def __init__(self, scores: dict[str, float], model: str) -> None:
            self.scores, self.model = scores, model

        def similarity(self, text: str) -> Similarity:
            return Similarity(scores=self.scores, models={"semantic": self.model})

    def test_each_brain_scores_with_its_own_model_and_the_outcome_names_both(self, brain: Brain, other: Any) -> None:
        group = next(str(identity) for identity in other[MemoryType.SEMANTIC].block_ids)
        engine = SqlEngine(
            brains={
                "physics": SqlBrain(brain.modules, scorer=self.Scorer({brain.id("concept"): 0.9}, "model/a")),
                "algebra": SqlBrain(other, scorer=self.Scorer({group: 0.8}, "model/b")),
            }
        )
        with engine:
            outcome = engine.query("SELECT brain, count(*) FROM semantic WHERE about(id, 'x', 0.5) GROUP BY brain")
        assert outcome.rows == [["algebra", 1], ["physics", 1]]
        assert outcome.approximate[-1]["models"] == {"algebra.semantic": "model/b", "physics.semantic": "model/a"}

    def test_a_qualified_query_is_scored_only_by_the_brain_it_reads(self, brain: Brain, other: Any) -> None:
        """The review's reproduction: a block both brains hold, low in the brain read and high in the other. Only the
        brain read may decide whether it is about the text."""
        shared = brain.id("physics")
        engine = SqlEngine(
            brains={
                "physics": SqlBrain(brain.modules, scorer=self.Scorer({shared: 0.1}, "model/a")),
                "algebra": SqlBrain(other, scorer=self.Scorer({shared: 0.9}, "model/b")),
            }
        )
        with engine:
            outcome = engine.query("SELECT count(*) FROM physics.semantic WHERE about(id, 'x', 0.5)")
        assert outcome.rows == [[0]]
        assert outcome.approximate[-1]["models"] == {"physics.semantic": "model/a"}

    def test_a_query_over_both_brains_takes_the_higher_score_and_names_both(self, brain: Brain, other: Any) -> None:
        shared = brain.id("physics")
        engine = SqlEngine(
            brains={
                "physics": SqlBrain(brain.modules, scorer=self.Scorer({shared: 0.1}, "model/a")),
                "algebra": SqlBrain(other, scorer=self.Scorer({shared: 0.9}, "model/b")),
            }
        )
        with engine:
            outcome = engine.query("SELECT brain FROM semantic WHERE about(id, 'x', 0.5)")
        assert outcome.rows == [["algebra"], ["physics"]]
        assert set(outcome.approximate[-1]["models"]) == {"algebra.semantic", "physics.semantic"}

    def test_a_brain_without_a_scorer_is_reported_unscored(self, brain: Brain, other: Any) -> None:
        engine = SqlEngine(
            brains={
                "physics": SqlBrain(brain.modules, scorer=self.Scorer({brain.id("concept"): 0.9}, "model/a")),
                "algebra": SqlBrain(other),
            }
        )
        with engine:
            outcome = engine.query("SELECT count(*) FROM semantic WHERE about(id, 'x', 0.5)")
        assert "algebra.semantic" in outcome.approximate[-1]["unscored"]


class TestConstruction:
    def test_one_brain_or_a_compound_and_not_both(self, brain: Brain) -> None:
        with pytest.raises(TypeError):
            SqlEngine(brain.modules, brains={"a": SqlBrain(brain.modules)})
        with pytest.raises(TypeError):
            SqlEngine()

    def test_an_empty_compound_is_refused(self) -> None:
        with pytest.raises(UsageError):
            SqlEngine(brains={})
