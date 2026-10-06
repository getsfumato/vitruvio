"""Branches, through the service layer, over a filesystem registry.

The test that matters most is `test_two_people_on_their_own_branches_never_exit_8`: it is the situation branches
exist for. A team on one tag has every push after the first refused as diverged; on branches of their own nobody
is refused, and the joining is one deliberate `branch_merge`.

The rest pin what vitruvio adds over the SDK: that ``main`` keeps publishing to ``[registry].tag``, that ``--tag``
overrides without renaming, that the head moves on the view that rebuilds nothing, and that a merge which needs a
reconciliation behaves exactly like ``reconcile`` -- halting with exit 12, and refusing to pick a strategy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vitruvio.ingest.evidence import Evidence
from vitruvio.kernel import ExitCode, VitruvioError, resolve
from vitruvio.runtime import BrainService
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.branches import current_branch_tag

REFERENCE = "demo/brain"

PROJECT = """
[brain]
path = "./brain"
{reconcile}

[actor]
id = "{actor}"

[policy]
profile = "permissive"
"""


# The three helpers below are the ones `test_reconcile.py` defines, repeated rather than imported: a test module is
# not a package mypy can resolve, and two suites sharing a fixture by import would have to move together.
def make(tmp_path: Path, name: str, *, reconcile: str | None = None) -> BrainService:
    """A brain of its own, with its own configuration file, optionally declaring a strategy."""
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    config_file = root / "vitruvio.toml"
    config_file.write_text(
        PROJECT.format(actor=f"{name}@example.com", reconcile=f'reconcile = "{reconcile}"' if reconcile else ""),
        encoding="utf-8",
    )
    BrainService(resolve(brain=root / "brain", config=config_file, require_layout=False)).init()
    return BrainService(resolve(brain=root / "brain", config=config_file))


def add_evidence(service: BrainService, text: str, name: str) -> str:
    """Register a canonical block from a directory every brain in the test shares, and return its identity."""
    incoming = Path(service.config.brain).parents[1] / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    path = incoming / name
    path.write_text(text, encoding="utf-8")
    return str(service.register(Evidence.from_path(path, media_type="text/markdown"))["block_id"])


def derive(service: BrainService, source: str, label: str) -> str:
    """Commit one semantic block derived from a canonical one, and return its identity."""
    from boltzmann.blocks.memory_type import MemoryType
    from boltzmann.identity.digest import BlockId
    from boltzmann.ingest.proposer import Candidate, CandidateSet

    brain = service.brain(Capability.WRITE)
    evidence = BlockId.parse(source)
    task = brain.define_task(evidence, allowed=[MemoryType.SEMANTIC])
    payload = {"kind": "concept", "label": label, "subject": "senales", "statement": f"{label} explicado."}
    candidates = CandidateSet(
        task_id=task.task_id,
        candidates=[Candidate(memory_type=MemoryType.SEMANTIC, evidence=[evidence], locator="p1", payload=payload)],
    )
    return str(brain.commit(brain.validate(candidates, task)).committed[0])


def members(service: BrainService, module: str = "semantic") -> set[str]:
    """One module's composition, as identities."""
    return set(service.inspection_ops.module(module, limit=10_000)["block_ids"])


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    root = tmp_path / "registry"
    root.mkdir()
    return root


@pytest.fixture
def ana(tmp_path: Path, registry: Path) -> BrainService:
    """A brain with one fact, published to the project's default tag."""
    service = make(tmp_path, "ana")
    shared = add_evidence(service, "# Fourier\n\nSenos y cosenos.\n", "fourier.md")
    derive(service, shared, "Serie de Fourier")
    service.push(REFERENCE, local=registry)
    return service


def add_fact(service: BrainService, label: str) -> None:
    source = add_evidence(service, f"# {label}\n\n{label}.\n", f"{label.lower().replace(' ', '-')}.md")
    derive(service, source, label)


def labels(service: BrainService) -> int:
    return len(members(service))


class TestTheDefaultTag:
    """`main` publishes where it always did; every other branch publishes to its own tag."""

    def test_a_brain_without_branches_publishes_to_the_registry_tag(self, ana: BrainService, registry: Path) -> None:
        assert ana.branch_current() == "main"
        assert current_branch_tag(ana.config).tag == ana.config.project.registry.tag
        assert ana.tags(REFERENCE, local=registry)["tags"] == ["latest"]

    def test_a_branch_publishes_to_its_own_tag_and_main_does_not_move(self, ana: BrainService, registry: Path) -> None:
        main_digest = ana.tags(REFERENCE, local=registry)
        ana.branch_create("ana/nyquist", switch=True)
        add_fact(ana, "Nyquist")
        pushed = ana.push(REFERENCE, local=registry)

        assert (pushed["branch"], pushed["tag"]) == ("ana/nyquist", "br.ana.nyquist")
        listing = ana.tags(REFERENCE, local=registry)
        assert {entry["tag"]: entry["kind"] for entry in listing["entries"]} == {
            "latest": "default",
            "br.ana.nyquist": "branch",
        }
        assert main_digest["tags"] == ["latest"]

    def test_an_explicit_tag_overrides_without_renaming_the_branch(self, ana: BrainService, registry: Path) -> None:
        ana.branch_create("x", switch=True)
        ana.push(REFERENCE, tag="v1.0", local=registry)
        add_fact(ana, "Nyquist")
        assert ana.push(REFERENCE, local=registry)["tag"] == "br.x"
        assert set(ana.tags(REFERENCE, local=registry)["tags"]) == {"latest", "v1.0", "br.x"}

    def test_a_project_tag_other_than_latest_stays_mains(self, ana: BrainService, registry: Path) -> None:
        """The SDK's own default for main is `latest`; a project that configured `stable` must keep it."""
        config = ana.config.model_copy(
            update={
                "project": ana.config.project.model_copy(
                    update={"registry": ana.config.project.registry.model_copy(update={"tag": "stable"})}
                )
            }
        )
        stable = BrainService(config)
        stable.branch_create("x")
        assert stable.push(REFERENCE, local=registry)["tag"] == "stable"
        rows = {row["name"]: row["tag"] for row in stable.branch_list()["branches"]}
        assert rows == {"main": "stable", "x": "br.x"}


class TestLocalBranches:
    def test_switch_round_trips_both_heads(self, ana: BrainService) -> None:
        before = labels(ana)
        assert ana.branch_switch("ana/nyquist", create=True)["outcome"] == "created"
        add_fact(ana, "Nyquist")
        assert labels(ana) == before + 1

        assert ana.branch_switch("main")["outcome"] == "switched"
        assert labels(ana) == before
        assert ana.verify()["verified"]

        ana.branch_switch("ana/nyquist")
        assert labels(ana) == before + 1

    def test_list_marks_the_current_branch_and_unpublished_work(self, ana: BrainService, registry: Path) -> None:
        ana.branch_create("x", switch=True)
        add_fact(ana, "Nyquist")
        listed = ana.branch_list()
        assert listed["current"] == "x"
        rows = {row["name"]: row for row in listed["branches"]}
        assert rows["x"]["current"]
        assert rows["x"]["unpublished"]
        assert not rows["main"]["unpublished"]

        ana.push(REFERENCE, local=registry)
        assert not {row["name"]: row for row in ana.branch_list()["branches"]}["x"]["unpublished"]

    def test_an_unknown_branch_without_track_is_not_found(self, ana: BrainService) -> None:
        with pytest.raises(VitruvioError) as caught:
            ana.branch_switch("nowhere", track=False)
        assert caught.value.code == "BRANCH_NOT_FOUND"
        assert caught.value.exit_code is ExitCode.NOT_FOUND

    def test_an_invalid_name_is_a_usage_error(self, ana: BrainService) -> None:
        with pytest.raises(VitruvioError) as caught:
            ana.branch_create("not.valid")
        assert caught.value.code == "BRANCH_NAME_INVALID"
        assert caught.value.exit_code is ExitCode.USAGE

    def test_deleting_unpushed_work_needs_force(self, ana: BrainService) -> None:
        ana.branch_create("x", switch=True)
        add_fact(ana, "Nyquist")
        ana.branch_switch("main")

        with pytest.raises(VitruvioError) as caught:
            ana.branch_delete("x")
        assert caught.value.code == "BRANCH_UNMERGED"
        assert "--force" in (caught.value.hint or "")

        assert ana.branch_delete("x", force=True)["deleted"] == "x"
        assert [row["name"] for row in ana.branch_list()["branches"]] == ["main"]

    def test_deleting_the_current_branch_is_refused(self, ana: BrainService) -> None:
        with pytest.raises(VitruvioError) as caught:
            ana.branch_delete("main")
        assert caught.value.code == "BRANCH_REFUSED"


class TestTeams:
    def test_two_people_on_their_own_branches_never_exit_8(
        self, tmp_path: Path, ana: BrainService, registry: Path
    ) -> None:
        beto = make(tmp_path, "beto", reconcile="merge")
        beto.pull(REFERENCE, local=registry)
        beto.branch_create("beto/nyquist", switch=True)
        add_fact(beto, "Nyquist")
        beto.push(REFERENCE, local=registry)

        add_fact(ana, "Laplace")
        ana.push(REFERENCE, local=registry)  # On one shared tag this is the push that exits 8.

        listed = ana.branch_list(remote=True, reference=REFERENCE, local=registry)
        assert {row["name"]: row["local"] for row in listed["remote"]} == {"main": True, "beto/nyquist": False}

    def test_a_teammates_branch_is_tracked_by_name(self, tmp_path: Path, ana: BrainService, registry: Path) -> None:
        beto = make(tmp_path, "beto")
        beto.pull(REFERENCE, local=registry)
        beto.branch_create("beto/nyquist", switch=True)
        add_fact(beto, "Nyquist")
        beto.push(REFERENCE, local=registry)

        tracked = ana.branch_switch("beto/nyquist", reference=REFERENCE, local=registry)
        assert tracked["outcome"] == "tracked"
        assert tracked["pull"]["tag"] == "br.beto.nyquist"
        assert ana.branch_current() == "beto/nyquist"
        assert labels(ana) == labels(beto)

    def test_one_shared_tag_still_exits_8(self, tmp_path: Path, ana: BrainService, registry: Path) -> None:
        """The contrast, kept honest."""
        beto = make(tmp_path, "beto")
        beto.pull(REFERENCE, local=registry)
        add_fact(beto, "Nyquist")
        beto.push(REFERENCE, local=registry)

        add_fact(ana, "Laplace")
        with pytest.raises(VitruvioError) as caught:
            ana.push(REFERENCE, local=registry)
        assert caught.value.exit_code is ExitCode.DIVERGED


class TestMerge:
    def test_a_branch_ahead_fast_forwards(self, ana: BrainService) -> None:
        ana.branch_create("x", switch=True)
        add_fact(ana, "Nyquist")
        head = ana.state()["snapshot"]
        ana.branch_switch("main")

        merged = ana.branch_merge("x")
        assert merged["outcome"] == "fast-forward"
        assert ana.state()["snapshot"] == head
        assert ana.branch_merge("x")["outcome"] == "up-to-date"

    def test_diverged_branches_need_a_strategy(self, ana: BrainService) -> None:
        ana.branch_create("x", switch=True)
        add_fact(ana, "Nyquist")
        ana.branch_switch("main")
        add_fact(ana, "Laplace")

        with pytest.raises(VitruvioError) as caught:
            ana.branch_merge("x")
        assert caught.value.exit_code is ExitCode.USAGE
        assert "--strategy" in (caught.value.hint or "")

        with pytest.raises(VitruvioError) as only:
            ana.branch_merge("x", strategy="merge", fast_forward="only")
        assert only.value.code == "BRANCH_REFUSED"

        before = labels(ana)
        merged = ana.branch_merge("x", strategy="merge", reason="join nyquist")
        assert merged["outcome"] == "reconciled"
        assert merged["reconciliation"]["attribution"]["parents"] == 2
        assert labels(ana) == before + 1

    def test_a_declared_strategy_is_used(self, tmp_path: Path, registry: Path) -> None:
        beto = make(tmp_path, "beto", reconcile="merge")
        add_fact(beto, "Fourier")
        beto.branch_create("x", switch=True)
        add_fact(beto, "Nyquist")
        beto.branch_switch("main")
        add_fact(beto, "Laplace")

        assert beto.branch_merge("x")["outcome"] == "reconciled"

    def test_a_merge_of_the_current_or_an_unknown_branch_is_refused(self, ana: BrainService) -> None:
        with pytest.raises(VitruvioError) as current:
            ana.branch_merge("main")
        assert current.value.code == "BRANCH_REFUSED"
        with pytest.raises(VitruvioError) as unknown:
            ana.branch_merge("nowhere")
        assert unknown.value.code == "BRANCH_NOT_FOUND"


def with_registry_tag(service: BrainService, tag: str) -> BrainService:
    """The same brain under a project that publishes main to ``tag``."""
    project = service.config.project
    registry = project.registry.model_copy(update={"tag": tag})
    return BrainService(
        service.config.model_copy(update={"project": project.model_copy(update={"registry": registry})})
    )


class TestReviewRegressions:
    """Each one reproduced against the branch before it was fixed (review of #93)."""

    def test_main_keeps_the_project_tag_when_a_branch_comes_before_the_first_push(
        self, tmp_path: Path, registry: Path
    ) -> None:
        """The SDK records `latest` for main when nothing says otherwise. Read back as main's tag, it made a pull of
        `stable` install into whichever branch was current, instead of moving to main."""
        stable = with_registry_tag(make(tmp_path, "solo"), "stable")
        add_fact(stable, "Fourier")
        stable.branch_create("x")
        assert stable.push(REFERENCE, local=registry)["tag"] == "stable"
        stable.branch_switch("x")

        stable.pull(REFERENCE, tag="stable", local=registry)
        assert stable.branch_current() == "main"

    def test_switch_create_also_keeps_the_project_tag(self, tmp_path: Path, registry: Path) -> None:
        stable = with_registry_tag(make(tmp_path, "solo"), "stable")
        add_fact(stable, "Fourier")
        stable.branch_switch("x", create=True)
        stable.branch_switch("main")
        stable.push(REFERENCE, local=registry)
        stable.branch_switch("x")

        stable.pull(REFERENCE, tag="stable", local=registry)
        assert stable.branch_current() == "main"

    def test_the_default_tag_follows_a_checkout_the_sdk_would_finish(self, ana: BrainService) -> None:
        """A checkout interrupted after the head moved is completed by the SDK on its next read; the default tag must
        not be chosen from the stale `current` before that, or a push publishes the branch over main's tag."""
        from vitruvio.runtime.assembly import Capability

        ana.branch_create("x", switch=True)
        add_fact(ana, "Nyquist")
        ana.branch_switch("main")
        brain = ana.brain(Capability.WRITE)
        table = brain._read_refs()
        assert table is not None
        brain._write_refs(table.model_copy(update={"switching": "x"}))
        brain._write_head(table.branches["x"].snapshot, origin=None)

        assert current_branch_tag(ana.config).branch == "x"
        assert current_branch_tag(ana.config).tag == "br.x"

    def test_an_interrupted_checkout_that_never_moved_keeps_the_branch(self, ana: BrainService) -> None:
        from vitruvio.runtime.assembly import Capability

        ana.branch_create("x")
        brain = ana.brain(Capability.WRITE)
        table = brain._read_refs()
        assert table is not None
        add_fact(ana, "Nyquist")  # Main moves on, so x's head is not the head pointer.
        table = brain._read_refs()
        assert table is not None
        brain._write_refs(table.model_copy(update={"switching": "x"}))

        assert current_branch_tag(ana.config).branch == "main"

    def test_an_unpublished_repository_lists_no_remote_branches(self, tmp_path: Path, registry: Path) -> None:
        solo = make(tmp_path, "solo")
        add_fact(solo, "Fourier")
        listed = solo.branch_list(remote=True, reference=REFERENCE, local=registry)
        assert listed["remote"] == []
        assert [row["name"] for row in listed["branches"]] == ["main"]

    def test_a_branch_missing_from_a_published_repository_is_branch_not_found(
        self, ana: BrainService, registry: Path
    ) -> None:
        with pytest.raises(VitruvioError) as caught:
            ana.branch_switch("absent", reference=REFERENCE, local=registry)
        assert caught.value.code == "BRANCH_NOT_FOUND"
        assert caught.value.exit_code is ExitCode.NOT_FOUND
        assert "br.absent" in caught.value.message

    def test_no_ff_on_a_contained_branch_is_still_up_to_date(self, ana: BrainService) -> None:
        ana.branch_create("x")
        add_fact(ana, "Nyquist")
        merged = ana.branch_merge("x", fast_forward="never")
        assert merged["outcome"] == "up-to-date"
