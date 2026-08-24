"""Reconciling two histories, over a filesystem registry. No network, no credentials, same code path.

The test that matters most here is `test_all_three_strategies_land_the_same_blocks`. Every claim vitruvio makes
about reconciliation rests on it -- that the choice between merge, rebase and squash is about attribution and not
about the outcome -- and it is the claim a reader is least likely to believe, because in version control it is
false. It is asserted rather than quoted.

The second is `test_a_dirty_fetch_leaves_the_brain_writable`. A fetch that opened a reconciliation would set the
`reconcile` pointer, after which the SDK refuses every ordinary write on that brain; that would turn a command
somebody ran to look at a remote into a command that stops them working. The design decision is invisible in the
payload, so it is pinned by writing afterwards.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from vitruvio.kernel import ExitCode, UsageError, VitruvioError, resolve
from vitruvio.runtime import BrainService
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.coerce import snapshot_digest

PROJECT = """
[brain]
path = "./brain"
{reconcile}

[actor]
id = "{actor}"

[policy]
profile = "permissive"
"""


def make(tmp_path: Path, name: str, *, reconcile: str | None = None, actor: str | None = None) -> BrainService:
    """
    A brain of its own, with its own configuration file, optionally declaring a strategy.

    ``actor`` is separable from ``name`` because provenance records the actor, so two brains that registered
    identical bytes under different identities hold *different* provenance blocks. Comparing compositions across
    brains therefore needs the actor held fixed, or the comparison measures the fixture instead of the protocol.
    """
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    config_file = root / "vitruvio.toml"
    config_file.write_text(
        PROJECT.format(
            actor=actor or f"{name}@example.com",
            reconcile=f'reconcile = "{reconcile}"' if reconcile else "",
        ),
        encoding="utf-8",
    )
    service = BrainService(resolve(brain=root / "brain", config=config_file, require_layout=False))
    service.init()
    return BrainService(resolve(brain=root / "brain", config=config_file))


def add_evidence(service: BrainService, text: str, name: str) -> str:
    """
    Register a canonical block, and return its identity.

    Every brain in a test registers from **one shared directory**, which is both truer to what is being modelled
    -- two people ingesting the same document -- and necessary for comparing compositions. A registration record
    carries the ``origin`` path, so reading the same bytes from two directories produces two different provenance
    blocks, and a comparison across brains would then be measuring the fixture's directory layout.
    """
    incoming = Path(service.config.brain).parents[1] / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    path = incoming / name
    path.write_text(text, encoding="utf-8")
    return str(service.register(path, media_type="text/markdown")["block_id"])


def derive(service: BrainService, source: str, label: str) -> str:
    """Commit one semantic block derived from a canonical one, and return its identity."""
    from boltzmann.blocks.memory_type import MemoryType
    from boltzmann.identity.digest import BlockId
    from boltzmann.ingest.proposer import Candidate, CandidateSet

    brain = service.brain(Capability.WRITE)
    evidence = BlockId.parse(source)
    task = brain.define_task(evidence, allowed=[MemoryType.SEMANTIC])
    candidates = CandidateSet(
        task_id=task.task_id,
        candidates=[
            Candidate(
                memory_type=MemoryType.SEMANTIC,
                evidence=[evidence],
                locator="p1",
                payload={
                    "kind": "concept",
                    "label": label,
                    "subject": "senales",
                    "statement": f"{label} explicado.",
                },
            )
        ],
    )
    result = brain.commit(brain.validate(candidates, task))
    return str(result.committed[0])


def members(service: BrainService, module: str = "semantic") -> set[str]:
    """One module's composition, as identities, for comparing two reconciliations."""
    return set(service.inspection_ops.module(module, limit=10_000)["block_ids"])


def composition(service: BrainService) -> dict[str, set[str]]:
    """Every installed module's composition. What the three strategies must agree on."""
    return {name: members(service, name) for name in service.state()["installed"]}


def evidential(service: BrainService) -> dict[str, set[str]]:
    """
    The composition, minus provenance.

    Provenance is excluded from any comparison *across brains* for the reason a test on this branch was just
    fixed for: a provenance record carries ``at``, a wall clock read when the record is written, so its identity
    is not a function of the knowledge. Three brains doing the same registrations produce identical provenance
    only when all three happen to land in the same second, which is a property of how fast the suite runs.

    The claim being tested is about the blocks the arithmetic decides on, so this asserts over those, and the
    caller checks provenance by shape instead.
    """
    return {name: blocks for name, blocks in composition(service).items() if name != "provenance"}


@pytest.fixture
def diverged(tmp_path: Path) -> tuple[Path, str, BrainService]:
    """
    Two histories from a common ancestor, the other side's already published.

    Ana and Beto both start from the same canonical block, then each adds work the other has not seen. Ana
    publishes. Beto is handed back, holding a history that diverged from the tag.
    """
    registry = tmp_path / "registry"
    registry.mkdir()

    ana = make(tmp_path, "ana")
    shared = add_evidence(ana, "# Fourier\n\nSenos y cosenos.\n", "fourier.md")
    derive(ana, shared, "Serie de Fourier")
    ana.push("demo/brain", tag="base", local=registry)

    beto = make(tmp_path, "beto", reconcile="merge")
    beto.pull("demo/brain", tag="base", local=registry)

    # Ana advances and republishes; Beto advances without seeing it. Now neither is an ancestor of the other.
    extra = add_evidence(ana, "# Laplace\n\nDe lo diferencial a lo algebraico.\n", "laplace.md")
    derive(ana, extra, "Transformada de Laplace")
    ana.push("demo/brain", tag="v2", local=registry)

    own = add_evidence(beto, "# Nyquist\n\nMuestreo.\n", "nyquist.md")
    derive(beto, own, "Teorema de Nyquist")
    return registry, "demo/brain", beto


class TestTheDivergenceItself:
    def test_a_diverged_push_exits_8_and_is_not_retryable(self, diverged: tuple[Path, str, BrainService]) -> None:
        """The regression this feature was built on top of.

        `DivergenceError` was absent from the mapping table, so it matched its parent `DistributionError` and
        came back as `REGISTRY_FAILED`, exit 9, retryable -- against a refusal that is identical every time. Three
        documents promised exit 8 and nothing emitted it.
        """
        registry, reference, beto = diverged
        with pytest.raises(VitruvioError) as caught:
            beto.push(reference, tag="v2", local=registry)

        assert caught.value.code == "DIVERGED"
        assert caught.value.exit_code == ExitCode.DIVERGED
        from vitruvio.runtime.mapping import report_for

        assert report_for(caught.value).retryable is False
        assert "fetch" in (caught.value.hint or ""), "the hint must name the operation that resolves this"


class TestFetch:
    def test_it_brings_a_history_without_moving_the_pointer(self, diverged: tuple[Path, str, BrainService]) -> None:
        registry, reference, beto = diverged
        before = beto.state()["snapshot"]["digest"]

        result = beto.fetch(reference, tag="v2", reconcile=False, local=registry)

        assert result["block_count"] > 0, "their new blocks must arrive"
        assert result["reconciliation"]["attempted"] is False
        assert beto.state()["snapshot"]["digest"] == before, "a fetch must not adopt anything"

    def test_it_reconciles_a_clean_plan_under_the_declared_strategy(
        self, diverged: tuple[Path, str, BrainService]
    ) -> None:
        registry, reference, beto = diverged
        before = beto.state()["snapshot"]["digest"]

        outcome = beto.fetch(reference, tag="v2", local=registry)["reconciliation"]

        assert outcome["attempted"] is True
        assert outcome["why"] == "clean"
        assert outcome["strategy"] == "merge"
        assert beto.state()["snapshot"]["digest"] != before
        assert beto.verify()["verified"] is True

    def test_an_undeclared_strategy_reconciles_nothing(self, tmp_path: Path) -> None:
        """The property the SDK protects by making `strategy` required, and that a default would have undone.

        Choosing decides whose name stays on the incoming work, so an absent declaration means nobody has chosen
        -- not that vitruvio may choose the safest-looking one.
        """
        registry = tmp_path / "registry"
        registry.mkdir()
        ana = make(tmp_path, "ana")
        shared = add_evidence(ana, "# Fourier\n\nx\n", "fourier.md")
        derive(ana, shared, "Serie de Fourier")
        ana.push("demo/brain", tag="base", local=registry)

        beto = make(tmp_path, "beto")  # no `reconcile` key
        beto.pull("demo/brain", tag="base", local=registry)
        extra = add_evidence(ana, "# Laplace\n\ny\n", "laplace.md")
        derive(ana, extra, "Transformada de Laplace")
        ana.push("demo/brain", tag="v2", local=registry)
        add_evidence(beto, "# Nyquist\n\nz\n", "nyquist.md")
        before = beto.state()["snapshot"]["digest"]

        outcome = beto.fetch("demo/brain", tag="v2", local=registry)["reconciliation"]

        assert outcome["attempted"] is False
        assert outcome["why"] == "no strategy declared"
        assert "reconcile" in outcome["hint"]
        assert beto.state()["snapshot"]["digest"] == before

    def test_fetching_a_contained_history_twice_mints_nothing(self, diverged: tuple[Path, str, BrainService]) -> None:
        """Idempotence, and it was not free.

        `ReconcilePlan.is_noop` reads like "their history is already in here" and is not: it reports that the
        arithmetic against the ancestor the search found changes nothing, and after a merge that search does not
        settle on their head. Trusting it minted a fresh snapshot on every repeated fetch -- each one a
        reconciliation of a history already held. Containment is a reachability question.
        """
        registry, reference, beto = diverged
        beto.fetch(reference, tag="v2", local=registry)
        settled = beto.state()["snapshot"]["digest"]
        versions = beto.history()["retained"]

        for _ in range(3):
            outcome = beto.fetch(reference, tag="v2", local=registry)["reconciliation"]
            assert outcome["why"] == "already contained"

        assert beto.state()["snapshot"]["digest"] == settled
        assert beto.history()["retained"] == versions, "a repeated fetch must not add versions"

    def test_a_reconciled_push_is_a_fast_forward_again(self, diverged: tuple[Path, str, BrainService]) -> None:
        """The whole point, end to end: the push that was refused now succeeds, with both sides' work in it."""
        registry, reference, beto = diverged
        mine = members(beto)
        beto.fetch(reference, tag="v2", local=registry)

        beto.push(reference, tag="v2", local=registry)

        after = members(beto)
        assert mine <= after, "reconciling must not cost the local side its own work"
        assert len(after) > len(mine), "and must bring theirs in"


class TestTheThreeStrategies:
    """The claim everything rests on, and the one a reader arrives disbelieving."""

    def test_all_three_strategies_land_the_same_blocks(self, tmp_path: Path) -> None:
        """A snapshot states a whole composition rather than a patch, so there is nothing to replay
        sequentially and the three cannot disagree about the result. They differ in lineage alone."""
        registry = tmp_path / "registry"
        registry.mkdir()
        ana = make(tmp_path, "ana")
        shared = add_evidence(ana, "# Fourier\n\nx\n", "fourier.md")
        derive(ana, shared, "Serie de Fourier")
        ana.push("demo/brain", tag="base", local=registry)
        extra = add_evidence(ana, "# Laplace\n\ny\n", "laplace.md")
        derive(ana, extra, "Transformada de Laplace")
        ana.push("demo/brain", tag="v2", local=registry)

        seen: dict[str, dict[str, Any]] = {}
        for strategy in ("merge", "rebase", "squash"):
            # A brain each, so the three run against identical starting states rather than in sequence -- but
            # one actor across all three, because provenance records it and three actors would make the three
            # compositions differ for a reason that has nothing to do with the strategy.
            beto = make(tmp_path, f"beto-{strategy}", actor="beto@example.com")
            beto.pull("demo/brain", tag="base", local=registry)
            own = add_evidence(beto, "# Nyquist\n\nz\n", "nyquist.md")
            derive(beto, own, "Teorema de Nyquist")
            fetched = beto.fetch("demo/brain", tag="v2", reconcile=False, local=registry)

            result = beto.reconcile_ops.reconcile(fetched["digest"], strategy=strategy, reason=f"testing {strategy}")
            assert result["halted"] is False, f"{strategy} should not have halted on a clean plan"
            seen[strategy] = {
                "evidential": evidential(beto),
                "provenance": len(composition(beto)["provenance"]),
                "parents": result["parents"],
                "snapshots": result["snapshots"],
                "verified": beto.verify()["verified"],
            }

        shapes = [
            tuple(sorted((name, tuple(sorted(blocks))) for name, blocks in entry["evidential"].items()))
            for entry in seen.values()
        ]
        assert shapes[0] == shapes[1] == shapes[2], (
            "the three strategies must produce identical block sets; only the recorded lineage may differ"
        )
        counts = {entry["provenance"] for entry in seen.values()}
        assert len(counts) == 1, f"the audit ledger must not depend on the strategy either: {counts}"
        assert all(entry["verified"] for entry in seen.values())

        # And now the difference that is real: lineage. A merge names both histories; the other two name one.
        assert len(seen["merge"]["parents"]) >= 2, "a merge must record both histories as parents"
        assert len(seen["rebase"]["parents"]) == 1, "a rebase records one parent: mine"
        assert len(seen["squash"]["parents"]) == 1, "a squash records one parent: mine"

    def test_the_plan_prices_every_strategy_before_one_is_chosen(
        self, diverged: tuple[Path, str, BrainService]
    ) -> None:
        """The table is only useful beforehand, so the plan carries all three rather than the chosen one."""
        registry, reference, beto = diverged
        fetched = beto.fetch(reference, tag="v2", reconcile=False, local=registry)

        plan = beto.reconcile_ops.plan(fetched["digest"])

        assert set(plan["attribution"]) == {"merge", "rebase", "squash"}
        assert plan["attribution"]["merge"]["their_signatures_survive"] is True
        assert plan["attribution"]["rebase"]["their_signatures_survive"] is False
        assert plan["attribution"]["squash"]["their_signatures_survive"] is False
        assert plan["is_noop"] is False

    def test_an_unknown_strategy_is_a_usage_error_naming_the_three(
        self, diverged: tuple[Path, str, BrainService]
    ) -> None:
        registry, reference, beto = diverged
        fetched = beto.fetch(reference, tag="v2", reconcile=False, local=registry)

        with pytest.raises(VitruvioError, match="merge, rebase, squash"):
            beto.reconcile_ops.reconcile(fetched["digest"], strategy="fast-forward", reason="x")


class TestTheMessagesUseOurVocabulary:
    def test_a_diverged_push_does_not_tell_the_user_to_pass_force_true(
        self, diverged: tuple[Path, str, BrainService]
    ) -> None:
        """The SDK's message ends "or pass force=True to overwrite the remote", which is a keyword argument on a
        method the reader cannot call. The flag here is `--force`."""
        registry, reference, beto = diverged
        with pytest.raises(VitruvioError) as caught:
            beto.push(reference, tag="v2", local=registry)

        text = f"{caught.value.message} {caught.value.hint or ''}"
        assert "force=True" not in text
        assert "--force" in text, "the advice itself is sound and must survive the translation"

    def test_no_sdk_method_name_survives_into_a_message(self, dirty: tuple[Path, str, BrainService]) -> None:
        """Every reconciliation message names an operation to run next. None may name it as Python."""
        registry, reference, beto = dirty
        fetched = beto.fetch(reference, tag="v2", reconcile=False, local=registry)
        beto.reconcile_ops.reconcile(fetched["digest"], strategy="merge", reason="x")

        with pytest.raises(VitruvioError) as caught:
            add_evidence(beto, "# Nyquist\n\nz\n", "nyquist.md")

        text = f"{caught.value.message} {caught.value.hint or ''}"
        assert "()" not in text, f"an SDK method name reached the user: {text}"
        assert "vitruvio reconcile" in text


class TestEveryRefusalIsMapped:
    """A reconciliation refusal must not report as an integrity failure."""

    def test_the_reconciliation_errors_all_carry_exit_12(self) -> None:
        """`ResolutionRefusedError` and the `ReconciliationError` base were absent from the table, so they fell
        through to `PROTOCOL_ERROR`, exit 5, HTTP 500 -- which the exit-code reference documents as a Merkle
        root that did not match. A caller reading that goes looking for corruption."""
        from boltzmann.exceptions import (
            ReconciliationBlockedError,
            ReconciliationError,
            ReconciliationHaltedError,
            ResolutionRefusedError,
        )

        from vitruvio.runtime.mapping import report_for

        for kind in (
            ResolutionRefusedError,
            ReconciliationError,
            ReconciliationHaltedError,
            ReconciliationBlockedError,
        ):
            report = report_for(kind("x"))
            assert report.exit_code == ExitCode.RECONCILE, f"{kind.__name__} reports exit {report.exit_code}"
            assert report.http_status == 409
            assert report.code != "PROTOCOL_ERROR", f"{kind.__name__} reads as an integrity failure"
            assert report.hint, f"{kind.__name__} carries no next action"

    def test_admitting_a_rejection_is_a_reconciliation_refusal_not_a_protocol_error(
        self, rejecting: tuple[Path, str, BrainService]
    ) -> None:
        """The single most-documented refusal in this feature, and it reported as exit 5 / HTTP 500."""
        registry, reference, beto = rejecting
        fetched = beto.fetch(reference, tag="v2", reconcile=False, local=registry)
        halted = beto.reconcile_ops.reconcile(fetched["digest"], strategy="merge", reason="x")
        rejected = [e["block"] for e in halted["plan"]["incoming"]["verdicts"] if e["status"] == "rejected"]
        assert rejected

        with pytest.raises(VitruvioError) as caught:
            beto.reconcile_ops.resolve(rejected[0], kind="admit")

        assert caught.value.exit_code == ExitCode.RECONCILE
        assert caught.value.code != "PROTOCOL_ERROR"
        assert caught.value.hint, "the skill says not to report this as a bug, so it has to say what to do"


class TestTheEnumsAgree:
    def test_the_kernel_enum_matches_the_sdk(self) -> None:
        """The kernel declares its own so it stays importable without the SDK. Two declarations can drift, and
        the drift would surface as a strategy the config accepts and `coerce` rejects."""
        from boltzmann.reconcile import ReconcileStrategy as Sdk

        from vitruvio.kernel import ReconcileStrategy

        assert {item.value for item in ReconcileStrategy} == {item.value for item in Sdk}


@pytest.fixture
def dirty(tmp_path: Path) -> tuple[Path, str, BrainService]:
    """
    A divergence that cannot be settled mechanically.

    Ana drops the canonical block both sides started from; Beto has meanwhile derived a semantic block from it.
    Each module's arithmetic is individually correct -- the drop is respected in canonical, the addition in
    semantic -- and the result still strands a derived block citing evidence the composition no longer holds.
    """
    registry = tmp_path / "registry"
    registry.mkdir()

    ana = make(tmp_path, "ana")
    shared = add_evidence(ana, "# Fourier\n\nSenos y cosenos.\n", "fourier.md")
    derive(ana, shared, "Serie de Fourier")
    ana.push("demo/brain", tag="base", local=registry)

    beto = make(tmp_path, "beto", reconcile="merge")
    beto.pull("demo/brain", tag="base", local=registry)

    ana.drop([shared], memory_type="canonical", reason="wrong scan")
    ana.push("demo/brain", tag="v2", local=registry)

    derive(beto, shared, "Nucleo de Dirichlet")
    return registry, "demo/brain", beto


class TestWhenItCannotBeSettledMechanically:
    def test_a_dirty_fetch_leaves_the_brain_writable(self, dirty: tuple[Path, str, BrainService]) -> None:
        """The design decision that is invisible in the payload, so it is pinned by writing afterwards.

        Opening the reconciliation would set the `reconcile` pointer, after which the SDK refuses every ordinary
        write. A command someone ran to look at a remote must not do that to them.
        """
        registry, reference, beto = dirty

        outcome = beto.fetch(reference, tag="v2", local=registry)["reconciliation"]

        assert outcome["attempted"] is False
        assert outcome["why"] == "not clean"
        assert beto.reconcile_ops.status()["open"] is False, "a fetch must not open a reconciliation"
        # The proof: an ordinary write still works.
        add_evidence(beto, "# Nyquist\n\nz\n", "nyquist.md")

    def test_the_hint_names_a_command_that_opens_one(self, dirty: tuple[Path, str, BrainService]) -> None:
        """Since this branch deliberately opens nothing, the hint must not point at the resolver.

        `reconcile resolve` resolves a reconciliation and cannot originate one -- it has no history to reconcile
        against, and nothing persists which was fetched. Sending somebody there would land them on "nothing in
        progress" with no way forward, which is the one thing a hint must not do.
        """
        registry, reference, beto = dirty

        outcome = beto.fetch(reference, tag="v2", local=registry)["reconciliation"]

        assert outcome["why"] == "not clean"
        hint = outcome["hint"]
        assert "reconcile merge" in hint, "the hint has to name the command that opens one"
        assert outcome["theirs"] in hint, "and it has to carry the digest, which is not stored anywhere else"

    def test_the_plan_names_what_would_leave(self, dirty: tuple[Path, str, BrainService]) -> None:
        registry, reference, beto = dirty
        fetched = beto.fetch(reference, tag="v2", reconcile=False, local=registry)

        plan = beto.reconcile_ops.plan(fetched["digest"])

        assert plan["is_clean"] is False
        leaving = sum(len(blocks) for blocks in plan["withdrawn"].values())
        assert leaving > 0, "the dropped evidence, and the block of Beto's that cited it, must be reported"

    def test_it_halts_rather_than_deciding_and_the_loop_concludes_it(
        self, dirty: tuple[Path, str, BrainService]
    ) -> None:
        """A halt is the operation asking, not failing: nothing written, and somewhere to put the answer."""
        registry, reference, beto = dirty
        fetched = beto.fetch(reference, tag="v2", reconcile=False, local=registry)
        before = beto.state()["snapshot"]["digest"]

        halted = beto.reconcile_ops.reconcile(fetched["digest"], strategy="merge", reason="incorporate Ana")

        assert halted["halted"] is True
        assert halted["open"] is True
        assert beto.state()["snapshot"]["digest"] == before, "a halt must write nothing"

        status = beto.reconcile_ops.status()
        assert status["open"] is True

        for block in list(status["unresolved"]):
            status = beto.reconcile_ops.resolve(block, kind="reject")
        if not status["removals_accepted"]:
            status = beto.reconcile_ops.accept_removals()

        assert status["is_resolved"] is True
        result = beto.reconcile_ops.continue_()
        assert result["halted"] is False
        assert beto.state()["snapshot"]["digest"] != before
        assert beto.verify()["verified"] is True
        assert beto.reconcile_ops.status()["open"] is False

    def test_an_open_reconciliation_refuses_an_ordinary_write(self, dirty: tuple[Path, str, BrainService]) -> None:
        """Exit 12, and the message must not hand the user the SDK's own method names."""
        registry, reference, beto = dirty
        fetched = beto.fetch(reference, tag="v2", reconcile=False, local=registry)
        beto.reconcile_ops.reconcile(fetched["digest"], strategy="merge", reason="incorporate Ana")

        with pytest.raises(VitruvioError) as caught:
            add_evidence(beto, "# Nyquist\n\nz\n", "nyquist.md")

        assert caught.value.code == "RECONCILE_OPEN"
        assert caught.value.exit_code == ExitCode.RECONCILE
        hint = caught.value.hint or ""
        assert "vitruvio reconcile" in hint
        assert "reconcile_abort()" not in hint, "the hint must not leak the SDK's API surface"

    def test_concluding_with_a_question_open_is_refused(self, dirty: tuple[Path, str, BrainService]) -> None:
        """The protocol declined to decide it; committing would decide it on the operator's behalf."""
        registry, reference, beto = dirty
        fetched = beto.fetch(reference, tag="v2", reconcile=False, local=registry)
        halted = beto.reconcile_ops.reconcile(fetched["digest"], strategy="merge", reason="x")
        if not halted["unresolved"] and halted["removals_accepted"]:
            pytest.skip("nothing was left open, so there is nothing to refuse")

        with pytest.raises(VitruvioError) as caught:
            beto.reconcile_ops.continue_()
        assert caught.value.exit_code == ExitCode.RECONCILE

    def test_abort_writes_nothing_and_reopens_the_brain(self, dirty: tuple[Path, str, BrainService]) -> None:
        registry, reference, beto = dirty
        fetched = beto.fetch(reference, tag="v2", reconcile=False, local=registry)
        before = beto.state()["snapshot"]["digest"]
        beto.reconcile_ops.reconcile(fetched["digest"], strategy="merge", reason="x")

        abandoned = beto.reconcile_ops.abort()

        assert abandoned["aborted"] is True
        assert beto.state()["snapshot"]["digest"] == before
        assert beto.reconcile_ops.status()["open"] is False
        add_evidence(beto, "# Nyquist\n\nz\n", "nyquist.md")  # writable again

    def test_abort_works_when_the_head_moved_underneath_it(self, dirty: tuple[Path, str, BrainService]) -> None:
        """The one state abandoning exists for, and the one it could not abandon.

        `reconcile_status` refuses when the head no longer matches the one the reconciliation was started
        against, and its own message says to abandon it. Reading the status first therefore made `abort` raise
        in exactly the situation it is the remedy for -- and `status` raises for the same reason, so both
        commands the hint names were dead and the brain stayed locked against every ordinary write.
        """
        from boltzmann.exceptions import ReconciliationError

        registry, reference, beto = dirty
        fetched = beto.fetch(reference, tag="v2", reconcile=False, local=registry)
        beto.reconcile_ops.reconcile(fetched["digest"], strategy="merge", reason="x")

        # Move the head underneath the open reconciliation, which is what a restored layout looks like.
        brain = beto.brain(Capability.WRITE)
        state = brain._reconcile_state()
        assert state is not None
        brain._put_reconcile_state(state.model_copy(update={"head": snapshot_digest(fetched["digest"])}))

        with pytest.raises(ReconciliationError):
            brain.reconcile_status()  # the SDK refuses, which is the precondition this test is about

        abandoned = beto.reconcile_ops.abort()

        assert abandoned["aborted"] is True
        assert abandoned["stale"] is True, "the detail could not be read, and the report says so"
        assert beto.reconcile_ops.status()["open"] is False
        add_evidence(beto, "# Nyquist\n\nz\n", "nyquist.md")  # writable again, which is the whole point

    def test_a_second_reconciliation_is_refused_as_itself(self, dirty: tuple[Path, str, BrainService]) -> None:
        """`Brain.reconcile` refuses a second one by raising the class a *halt* raises.

        Reported as a halt, that labelled somebody else's open merge with the strategy and history just
        requested -- `strategy: rebase` over `state.strategy: merge`, and the operator believing B was being
        reconciled when A still is.
        """
        registry, reference, beto = dirty
        fetched = beto.fetch(reference, tag="v2", reconcile=False, local=registry)
        first = beto.reconcile_ops.reconcile(fetched["digest"], strategy="merge", reason="first")
        assert first["halted"] is True

        with pytest.raises(UsageError) as caught:
            beto.reconcile_ops.reconcile(fetched["digest"], strategy="rebase", reason="second")

        assert "already unresolved" in caught.value.message or "still unresolved" in caught.value.message
        assert "abort" in (caught.value.hint or "")
        assert beto.reconcile_ops.status()["state"]["strategy"] == "merge", "the open one is untouched"

    def test_a_store_failure_during_abort_is_mapped_not_reported_as_our_bug(
        self, dirty: tuple[Path, str, BrainService], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`reconcile_status` reads blocks, so what it can raise is not only `ReconciliationError`.

        The special case for the head-mismatch has to stay narrow: anything else escaping the `translated()`
        boundary reaches the CLI's last-resort handler, which reports an unmapped exception as "internal error
        -- this is a bug in vitruvio". A corrupt store would have been denounced as our defect instead of
        `INTEGRITY_FAILED`.
        """
        from boltzmann.exceptions import BlockIntegrityError

        registry, reference, beto = dirty
        fetched = beto.fetch(reference, tag="v2", reconcile=False, local=registry)
        beto.reconcile_ops.reconcile(fetched["digest"], strategy="merge", reason="x")

        brain = beto.brain(Capability.WRITE)
        monkeypatch.setattr(
            type(brain),
            "reconcile_status",
            lambda self, *args, **kwargs: (_ for _ in ()).throw(BlockIntegrityError("bytes do not hash")),
        )

        with pytest.raises(VitruvioError) as caught:
            beto.reconcile_ops.abort()

        assert caught.value.code == "INTEGRITY_FAILED", "a corrupt store is not a bug in vitruvio"
        assert caught.value.exit_code == ExitCode.PROTOCOL

    def test_aborting_nothing_is_a_usage_error(self, tmp_path: Path) -> None:
        beto = make(tmp_path, "beto")
        with pytest.raises(UsageError, match="no reconciliation"):
            beto.reconcile_ops.abort()


@pytest.fixture
def rejecting(tmp_path: Path) -> tuple[Path, str, BrainService]:
    """
    The mirror of ``dirty``: the block that cannot enter is the *incoming* one.

    Beto drops the canonical evidence deliberately; Ana, not having seen that, derives a semantic block from it
    and publishes. Ana's block is what arrives, and it cites evidence Beto's composition does not hold -- so the
    gate rejects it and the diagnosis is ``dropped_deliberately``. ``dirty`` cannot produce this: there the
    derived block is Beto's own, nobody proposed it, and it leaves by cascade rather than by verdict.
    """
    registry = tmp_path / "registry"
    registry.mkdir()

    ana = make(tmp_path, "ana", actor="shared@example.com")
    shared = add_evidence(ana, "# Fourier\n\nSenos y cosenos.\n", "fourier.md")
    ana.push("demo/brain", tag="base", local=registry)

    beto = make(tmp_path, "beto", reconcile="merge", actor="shared@example.com")
    beto.pull("demo/brain", tag="base", local=registry)

    derive(ana, shared, "Serie de Fourier")
    ana.push("demo/brain", tag="v2", local=registry)

    beto.drop([shared], memory_type="canonical", reason="bad scan")
    return registry, "demo/brain", beto


class TestARejectionCannotBeAdmitted:
    """The one place this departs from version control on purpose, and the claim the docs lean on hardest."""

    def test_the_incoming_block_is_rejected_and_diagnosed(self, rejecting: tuple[Path, str, BrainService]) -> None:
        registry, reference, beto = rejecting
        fetched = beto.fetch(reference, tag="v2", reconcile=False, local=registry)

        plan = beto.reconcile_ops.plan(fetched["digest"])
        rejected = [entry for entry in plan["incoming"]["verdicts"] if entry["status"] == "rejected"]

        assert rejected, "a derived block citing deliberately dropped evidence must be refused"
        advice = {why for entry in rejected for why in entry["missing_evidence"].values()}
        assert advice == {"dropped_deliberately"}, (
            "the diagnosis is what turns one verdict into actionable advice: dropped deliberately means tell "
            "them not to resend, never held means tell them to resend it whole"
        )

    def test_admitting_it_is_refused(self, rejecting: tuple[Path, str, BrainService]) -> None:
        """A derived block whose evidence is absent from the composition breaks R1, and nothing downstream would
        catch it -- `verify` recomputes hashes and compositions, not citations across modules."""
        registry, reference, beto = rejecting
        fetched = beto.fetch(reference, tag="v2", reconcile=False, local=registry)
        halted = beto.reconcile_ops.reconcile(fetched["digest"], strategy="merge", reason="incorporate Ana")
        assert halted["halted"] is True

        verdicts = halted["plan"]["incoming"]["verdicts"]
        rejected = [entry["block"] for entry in verdicts if entry["status"] == "rejected"]
        assert rejected, "the fixture must produce a rejection for this to be testing anything"

        with pytest.raises(VitruvioError):
            beto.reconcile_ops.resolve(rejected[0], kind="admit")

        # Rejecting it is always available, and concludes.
        for block in list(beto.reconcile_ops.status()["unresolved"]):
            beto.reconcile_ops.resolve(block, kind="reject")
        status = beto.reconcile_ops.status()
        if not status["removals_accepted"]:
            beto.reconcile_ops.accept_removals()
        beto.reconcile_ops.continue_()
        assert beto.verify()["verified"] is True


class TestTheIndices:
    def test_the_structural_indices_track_the_reconciled_composition(
        self, diverged: tuple[Path, str, BrainService]
    ) -> None:
        """The SDK rebuilds them on a reconciliation, the same as on a commit -- checked rather than assumed,
        because the alternative is a brain whose search silently answers from the pre-merge composition.

        The vector index is the exception and is *not* a reconciliation problem: it is stale after any write, so
        the answer here is the answer everywhere, `vitruvio index build`.
        """
        registry, reference, beto = diverged

        def usable(report: dict[str, Any]) -> dict[tuple[str, str], int]:
            return {
                (entry["memory_type"], entry["kind"]): entry["population"]
                for entry in report["capabilities"]
                if entry["kind"] != "vector" and entry["state"] == "ready"
            }

        before = usable(beto.index_verify())
        beto.fetch(reference, tag="v2", local=registry)
        after = usable(beto.index_verify())

        expected = len(members(beto, "semantic"))
        assert after[("semantic", "hash_map")] == expected, (
            "a structural index that kept the pre-merge population would answer searches from a composition "
            "the brain no longer holds"
        )
        assert after[("semantic", "hash_map")] > before[("semantic", "hash_map")]
        assert after[("canonical", "hash_map")] == len(members(beto, "canonical"))


class TestTheHistoryIsAGraph:
    def test_a_merge_records_both_parents_and_history_reports_them(
        self, diverged: tuple[Path, str, BrainService]
    ) -> None:
        """`brain history` reads `parents`, plural. The renderer read `parent` until 0.6 removed it, and
        `Mapping.get` returns None rather than raising, so every snapshot printed as though it had none."""
        registry, reference, beto = diverged
        beto.fetch(reference, tag="v2", local=registry)

        history = beto.history()
        head = history["snapshots"][0]

        assert len(head["parents"]) >= 2, "a merge names both histories"
        assert history["ancestry"][0] == head["digest"]
        assert set(history["ancestry"]) <= set(history["reachable"])
        assert len(history["reachable"]) > len(history["ancestry"]), (
            "a merged-in history is contained without being on the first-parent chain -- which is exactly why "
            "containment is a reachability question and `ancestry` cannot answer it"
        )

    def test_the_tree_reports_where_the_two_parted(self, diverged: tuple[Path, str, BrainService]) -> None:
        registry, reference, beto = diverged
        fetched = beto.fetch(reference, tag="v2", reconcile=False, local=registry)

        tree = beto.reconcile_ops.tree(fetched["digest"])

        assert tree["ancestor"]
        assert tree["ours"] != tree["theirs"]
        assert tree["is_noop"] is False
        assert tree["modules"], "the per-module arithmetic is what a decision is made on"

    def test_the_tree_needs_a_history_to_compare_against(self, tmp_path: Path) -> None:
        beto = make(tmp_path, "beto")
        with pytest.raises(UsageError, match="no history to compare"):
            beto.reconcile_ops.tree()
