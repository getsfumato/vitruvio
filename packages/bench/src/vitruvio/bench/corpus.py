"""Synthetic brains with known ground truth.

Two reasons this exists rather than a fixture directory.

**Scale.** Below a few hundred blocks an exhaustive scan legitimately *wins* every plan comparison -- reading every
block costs less than one query embedding -- so a small corpus would pass every planner test without exercising a
single index path. The crossover from exhaustive to indexed is the central claim of the cost model, and it can only be
observed on a corpus large enough for it to happen.

**Ground truth.** The corpus is generated *from* its relevance judgements rather than judged afterwards, so recall is
measurable rather than estimated. Each query has a known answer set, and a plan either finds it or does not.

Everything is deterministic: the generator takes a seed and no wall-clock time, so two runs on two machines produce
byte-identical brains and a benchmark comparison means something.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from boltzmann.blocks.memory_type import MemoryType
from boltzmann.blocks.provenance import Actor, ActorKind
from boltzmann.ingest.proposer import Candidate, CandidateSet
from boltzmann.ingest.register import RegistrationRequest
from boltzmann.retention.policy import PERMISSIVE_POLICY

if TYPE_CHECKING:
    from pathlib import Path

    from boltzmann.brain import Brain

SUBJECTS = ("senales", "control", "algebra", "termodinamica", "optica", "redes")
"""Subjects, so a facet filter has something with real selectivity to cut on."""

# Per subject: the distinctive terms its blocks use. A query built from one subject's terms should find that
# subject's blocks and no others, which is what makes the ground truth checkable.
VOCABULARY: dict[str, tuple[str, ...]] = {
    "senales": ("fourier", "periodica", "armonico", "espectro", "convolucion", "muestreo"),
    "control": ("laplace", "realimentacion", "estabilidad", "polos", "transitorio", "ganancia"),
    "algebra": ("matriz", "autovalor", "determinante", "ortogonal", "subespacio", "rango"),
    "termodinamica": ("entropia", "adiabatico", "isotermico", "entalpia", "reversible", "ciclo"),
    "optica": ("difraccion", "interferencia", "polarizacion", "refraccion", "coherencia", "lente"),
    "redes": ("enrutamiento", "latencia", "congestion", "protocolo", "topologia", "ancho"),
}

FILLER = (
    "el sistema",
    "en general",
    "se define como",
    "para el caso",
    "de manera que",
    "bajo condiciones",
    "the system",
    "in general",
    "is defined as",
)
"""Padding, so documents have realistic length variation for BM25's normalisation to matter."""


@dataclass(frozen=True, slots=True)
class Judgement:
    """
    One query and the blocks that genuinely answer it.

    Attributes:
        query (str): What to ask.
        relevant (frozenset[str]): Block identities that should be found. Known because the corpus was generated
            from them rather than judged afterwards.
        subject (str): Which subject's vocabulary the query was built from.
    """

    query: str
    relevant: frozenset[str]
    subject: str


@dataclass
class Corpus:
    """
    A generated brain and its ground truth.

    Attributes:
        brain (Brain): The brain, already committed.
        judgements (list[Judgement]): Queries with known answers.
        blocks (int): How many knowledge blocks were written.
    """

    brain: Brain
    judgements: list[Judgement] = field(default_factory=list)
    blocks: int = 0

    def recall_at(self, found: list[str], judgement: Judgement, k: int = 10) -> float:
        """
        What fraction of a query's relevant blocks appear in the top ``k``.

        Args:
            found (list[str]): Block identities returned, in rank order.
            judgement (Judgement): The query and its answer set.
            k (int): How deep to look.

        Returns:
            float: Recall in ``[0, 1]``.
        """
        if not judgement.relevant:
            return 1.0
        top = set(found[:k])
        return len(top & judgement.relevant) / len(judgement.relevant)


def generate(
    path: Path,
    *,
    blocks: int = 5000,
    seed: int = 1234,
    queries: int = 24,
) -> Corpus:
    """
    Build a brain of a given size, with a query set whose answers are known.

    Args:
        path (Path): Where to create the brain.
        blocks (int): How many semantic blocks to write. The default is deliberately above the few hundred at which
            an exhaustive scan stops winning, so index paths are actually exercised.
        seed (int): Makes the corpus reproducible. No wall-clock time is read, so two machines produce identical
            brains and a benchmark comparison is meaningful.
        queries (int): How many judged queries to build.

    Returns:
        Corpus: The brain and its ground truth.
    """
    from boltzmann.brain import Brain

    rng = random.Random(seed)
    actor = Actor(id="bench@vitruvio", kind=ActorKind.SERVICE, name="corpus generator")
    brain = Brain.open(path, actor, policy=PERMISSIVE_POLICY)

    source = brain.register(
        b"# Corpus sintetico\n\nGenerado por vitruvio.bench para medir recall y latencia.\n",
        RegistrationRequest(media_type="text/markdown", actor=actor, origin="vitruvio.bench"),
    )
    task = brain.define_task(source.block_id, allowed=[MemoryType.SEMANTIC, MemoryType.EPISODIC])

    by_subject: dict[str, list[str]] = {subject: [] for subject in SUBJECTS}
    candidates: list[Candidate] = []
    written = 0

    for position in range(blocks):
        subject = SUBJECTS[position % len(SUBJECTS)]
        terms = VOCABULARY[subject]
        # Two distinctive terms per block, so a two-term query has a small, precise answer set rather than matching
        # a third of the module.
        primary, secondary = rng.sample(terms, 2)
        padding = " ".join(rng.sample(FILLER, rng.randint(1, 3)))
        candidates.append(
            Candidate(
                memory_type=MemoryType.SEMANTIC,
                evidence=[source.block_id],
                locator=f"p{position}",
                payload={
                    "kind": "concept",
                    "label": f"{primary} {secondary} {position}",
                    "subject": subject,
                    "statement": f"El {primary} y el {secondary} {padding} en {subject}, caso {position}.",
                },
            )
        )
        written += 1

    # Committed in batches: one commit per block would write one snapshot per block, and the retained-root list would
    # dominate the runtime for reasons that have nothing to do with what is being measured.
    batch = 500
    for start in range(0, len(candidates), batch):
        chunk = CandidateSet(task_id=task.task_id, candidates=candidates[start : start + batch])
        report = brain.validate(chunk, task)
        result = brain.commit(report)
        module = brain.module(MemoryType.SEMANTIC)
        for identity in result.committed:
            block = module.get(identity)
            written_subject = getattr(block, "subject", None)
            if written_subject in by_subject:
                by_subject[written_subject].append(str(identity))

    judgements: list[Judgement] = []
    module = brain.module(MemoryType.SEMANTIC)
    for index in range(queries):
        subject = SUBJECTS[index % len(SUBJECTS)]
        primary, secondary = rng.sample(VOCABULARY[subject], 2)
        query = f"{primary} {secondary}"
        # The answer set is read back from what was written, so a judgement can never disagree with the corpus.
        # Named `rendered` rather than reusing `identity`: the earlier loop bound that name to a BlockId, and here it
        # is the string form.
        relevant: set[str] = set()
        for rendered in by_subject[subject]:
            block = module.get(_parse(rendered))
            label = getattr(block, "label", "")
            if all(term in label for term in (primary, secondary)):
                relevant.add(rendered)
        judgements.append(Judgement(query=query, relevant=frozenset(relevant), subject=subject))

    return Corpus(brain=brain, judgements=judgements, blocks=written)


def _parse(identity: str):
    """Parse a block identity string."""
    from boltzmann.identity.digest import BlockId

    return BlockId.parse(identity)
