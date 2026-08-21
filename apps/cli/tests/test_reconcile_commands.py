"""``vitruvio reconcile`` and ``dist fetch`` at the command boundary: exit codes and envelope shape.

The behaviour is covered in `packages/runtime/tests/test_reconcile.py`, over the real protocol. What this file
holds is what only the CLI can get wrong: the status a caller branches on, the keys a caller reads, and the two
refusals that keep an interactive command from drawing control codes into a pipe.

The exit codes are the contract. `8` from a diverged push and `12` from an open reconciliation are what an agent
reads before it reads anything else, and both were reachable only through prose before this feature landed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vitruvio.cli.main import main
from vitruvio.kernel import ExitCode, resolve
from vitruvio.runtime import BrainService


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    """Invoke the CLI in-process and return its status and streams."""
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def envelope(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    """Invoke the CLI in JSON mode and parse the single object it printed."""
    code, out, _ = run(capsys, "--json", *args)
    return code, json.loads(out)


INDICES = "\n".join(
    f'[[index]]\nmemory_type = "{module}"\nkind = "{kind}"'
    for module in ("canonical", "semantic", "provenance")
    for kind in ("hash_map", "btree", "bitmap")
)
"""Structural indices only, and no vector index -- declared rather than left to the defaults.

Not an optimisation. A vector index owns a sqlite-backed embedding cache, and `Resolver` holds its service
inside a Textual widget graph that refers back to the app, so the whole thing is freed by the *cycle* collector
-- within which a `sqlite3.Connection` can be finalized before whatever would have closed it, emitting
`ResourceWarning: unclosed database`. Python 3.13 says so where earlier versions did not, and
`filterwarnings = error` then failed the suite, attributing it to whichever test the collector happened to
interrupt: it first appeared as a failure in `test_tui`, caused from here.

Closing them at teardown does not work -- by then the services are unreachable, a sweep for live
`EmbeddingCache` instances finds none, and the connections are held by an `lru_cache` inside `sqlite3` itself.
So the fix is upstream of the resource: nothing here searches semantically, so nothing here needs the index
that opens a database.
"""

PROJECT = (
    """
[brain]
path = "./brain"
{reconcile}

[actor]
id = "shared@example.com"

[policy]
profile = "permissive"
"""
    + INDICES
)


def add_evidence(service: Any, text: str, name: str) -> str:
    """Register a canonical block from a directory every brain in the test shares.

    Shared because a registration record carries the ``origin`` path, so the same bytes read from two
    directories are two different provenance blocks. Defined here rather than imported from the runtime suite:
    a test reaching across packages for a fixture couples two suites that have no reason to move together.
    (This file is `test_reconcile_commands` rather than `test_reconcile` for a duller reason -- pytest imports
    test modules by basename, so two files called the same thing in two packages collide on collection.)
    """
    incoming = Path(service.config.brain).parents[1] / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    path = incoming / name
    path.write_text(text, encoding="utf-8")
    return str(service.register(path, media_type="text/markdown")["block_id"])


def derive(service: Any, source: str, label: str) -> str:
    """Commit one semantic block derived from a canonical one."""
    from boltzmann.blocks.memory_type import MemoryType
    from boltzmann.identity.digest import BlockId
    from boltzmann.ingest.proposer import Candidate, CandidateSet

    from vitruvio.runtime.assembly import Capability

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
    return str(brain.commit(brain.validate(candidates, task)).committed[0])


def make(tmp_path: Path, name: str, *, reconcile: str | None = None) -> Path:
    """A brain with its own configuration file, and the config path the CLI should be pointed at."""
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    config_file = root / "vitruvio.toml"
    config_file.write_text(
        PROJECT.format(reconcile=f'reconcile = "{reconcile}"' if reconcile else ""), encoding="utf-8"
    )
    BrainService(resolve(brain=root / "brain", config=config_file, require_layout=False)).init()
    return config_file


@pytest.fixture
def diverged(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Two histories from a common ancestor, the other side's published. Returns registry and both configs."""
    registry = tmp_path / "registry"
    registry.mkdir()

    ana_config = make(tmp_path, "ana")
    ana = BrainService(resolve(brain=tmp_path / "ana" / "brain", config=ana_config))
    shared = add_evidence(ana, "# Fourier\n\nSenos y cosenos.\n", "fourier.md")
    derive(ana, shared, "Serie de Fourier")
    ana.push("demo/brain", tag="base", local=registry)

    beto_config = make(tmp_path, "beto", reconcile="merge")
    beto = BrainService(resolve(brain=tmp_path / "beto" / "brain", config=beto_config))
    beto.pull("demo/brain", tag="base", local=registry)

    extra = add_evidence(ana, "# Laplace\n\nDe lo diferencial.\n", "laplace.md")
    derive(ana, extra, "Transformada de Laplace")
    ana.push("demo/brain", tag="v2", local=registry)

    own = add_evidence(beto, "# Nyquist\n\nMuestreo.\n", "nyquist.md")
    derive(beto, own, "Teorema de Nyquist")
    return registry, ana_config, beto_config


@pytest.fixture
def halting(tmp_path: Path) -> tuple[Path, Path]:
    """A divergence that cannot settle mechanically. Returns the registry and Beto's config."""
    registry = tmp_path / "registry"
    registry.mkdir()

    ana_config = make(tmp_path, "ana")
    ana = BrainService(resolve(brain=tmp_path / "ana" / "brain", config=ana_config))
    shared = add_evidence(ana, "# Fourier\n\nSenos y cosenos.\n", "fourier.md")
    derive(ana, shared, "Serie de Fourier")
    ana.push("demo/brain", tag="base", local=registry)

    beto_config = make(tmp_path, "beto", reconcile="merge")
    beto = BrainService(resolve(brain=tmp_path / "beto" / "brain", config=beto_config))
    beto.pull("demo/brain", tag="base", local=registry)

    ana.drop([shared], memory_type="canonical", reason="bad scan")
    ana.push("demo/brain", tag="v2", local=registry)

    # Derived here, after the pull, so it is a block Ana never held and cannot have cascaded away herself.
    derive(beto, shared, "Nucleo de Dirichlet")
    return registry, beto_config


@pytest.fixture
def halted(halting: tuple[Path, Path]) -> tuple[Any, Path]:
    """A reconciliation already open, and the service to drive it with.

    Synchronous on purpose. `fetch`, like `push` and `pull`, drives the registry through `asyncio.run`, which
    cannot be called from inside a running event loop -- so the setup for an async interface test has to happen
    before the loop exists. The resolver itself only calls synchronous operations, which is why it can.
    """
    registry, beto_config = halting
    beto = BrainService(resolve(brain=registry.parent / "beto" / "brain", config=beto_config))
    fetched = beto.fetch("demo/brain", tag="v2", reconcile=False, local=registry)
    halted_result = beto.reconcile_ops.reconcile(fetched["digest"], strategy="merge", reason="incorporate Ana")
    assert halted_result["halted"] is True, "the fixture must halt for the interface tests to have anything to show"
    return beto, beto_config


class TestExitCodes:
    def test_a_diverged_push_exits_8(
        self, capsys: pytest.CaptureFixture[str], diverged: tuple[Path, Path, Path]
    ) -> None:
        """The code three documents promised and nothing emitted, because `DivergenceError` fell through to its
        parent and came back as a retryable registry failure."""
        registry, _, beto = diverged
        code, payload = envelope(
            capsys, "--config", str(beto), "dist", "push", "demo/brain", "--tag", "v2", "--local", str(registry)
        )

        assert code == ExitCode.DIVERGED
        assert payload["ok"] is False
        assert payload["error"]["code"] == "DIVERGED"

    def test_an_open_reconciliation_refuses_a_write_with_12(
        self, capsys: pytest.CaptureFixture[str], halting: tuple[Path, Path]
    ) -> None:
        """A read is still allowed; a write is not. And the hint must not hand over the SDK's method names.

        The shape that halts reliably: Ana withdraws the evidence, and Beto has derived a block from it that Ana
        never had. Each module's arithmetic is individually correct and the result still strands Beto's block.
        """
        registry, beto_config = halting
        beto = BrainService(resolve(brain=registry.parent / "beto" / "brain", config=beto_config))
        fetched = beto.fetch("demo/brain", tag="v2", reconcile=False, local=registry)
        halted = beto.reconcile_ops.reconcile(fetched["digest"], strategy="merge", reason="incorporate Ana")
        assert halted["halted"] is True, "the fixture must halt for this to be testing anything"

        code, _ = envelope(capsys, "--config", str(beto_config), "brain", "state")
        assert code == ExitCode.OK, "a read stays available while a reconciliation is open"

        code, payload = envelope(capsys, "--config", str(beto_config), "reconcile", "continue")
        assert code == ExitCode.RECONCILE
        assert payload["error"]["code"] in {"RECONCILE_OPEN", "RECONCILE_BLOCKED"}
        assert "reconcile_abort()" not in json.dumps(payload), "the SDK's API must not reach the user"
        assert "vitruvio reconcile" in json.dumps(payload), "the hint must name a command someone can run"

    def test_status_lists_what_is_open_and_abort_clears_it(
        self, capsys: pytest.CaptureFixture[str], halting: tuple[Path, Path]
    ) -> None:
        registry, beto_config = halting
        beto = BrainService(resolve(brain=registry.parent / "beto" / "brain", config=beto_config))
        fetched = beto.fetch("demo/brain", tag="v2", reconcile=False, local=registry)
        beto.reconcile_ops.reconcile(fetched["digest"], strategy="merge", reason="x")

        code, payload = envelope(capsys, "--config", str(beto_config), "reconcile", "status")
        assert code == ExitCode.OK, "reporting an open reconciliation is an answer, not a failure"
        assert payload["data"]["open"] is True

        code, payload = envelope(capsys, "--config", str(beto_config), "reconcile", "abort")
        assert code == ExitCode.OK
        assert payload["data"]["aborted"] is True

        code, payload = envelope(capsys, "--config", str(beto_config), "reconcile", "status")
        assert payload["data"] == {"open": False}


class TestFetchEnvelope:
    def test_it_reports_what_it_did_about_the_history(
        self, capsys: pytest.CaptureFixture[str], diverged: tuple[Path, Path, Path]
    ) -> None:
        registry, _, beto = diverged
        code, payload = envelope(
            capsys, "--config", str(beto), "dist", "fetch", "demo/brain", "--tag", "v2", "--local", str(registry)
        )

        assert code == ExitCode.OK
        data = payload["data"]
        assert data["block_count"] > 0
        assert data["reconciliation"]["attempted"] is True
        assert data["reconciliation"]["strategy"] == "merge"

    def test_no_reconcile_fetches_and_stops(
        self, capsys: pytest.CaptureFixture[str], diverged: tuple[Path, Path, Path]
    ) -> None:
        registry, _, beto = diverged
        code, payload = envelope(
            capsys,
            "--config",
            str(beto),
            "dist",
            "fetch",
            "demo/brain",
            "--tag",
            "v2",
            "--local",
            str(registry),
            "--no-reconcile",
        )

        assert code == ExitCode.OK
        assert payload["data"]["reconciliation"] == {"attempted": False, "why": "not requested"}


class TestPlanAndTree:
    def test_the_plan_carries_all_three_strategies(
        self, capsys: pytest.CaptureFixture[str], diverged: tuple[Path, Path, Path]
    ) -> None:
        registry, _, beto = diverged
        _, fetched = envelope(
            capsys,
            "--config",
            str(beto),
            "dist",
            "fetch",
            "demo/brain",
            "--tag",
            "v2",
            "--local",
            str(registry),
            "--no-reconcile",
        )
        theirs = fetched["data"]["digest"]

        code, payload = envelope(capsys, "--config", str(beto), "reconcile", "plan", theirs)

        assert code == ExitCode.OK
        assert set(payload["data"]["attribution"]) == {"merge", "rebase", "squash"}

    def test_status_with_nothing_open_is_not_an_error(
        self, capsys: pytest.CaptureFixture[str], diverged: tuple[Path, Path, Path]
    ) -> None:
        """Asking is how a caller finds out, so "nothing in progress" is an answer and exit 0."""
        _, _, beto = diverged
        code, payload = envelope(capsys, "--config", str(beto), "reconcile", "status")

        assert code == ExitCode.OK
        assert payload["data"] == {"open": False}

    def test_the_history_graph_reports_both_parents(
        self, capsys: pytest.CaptureFixture[str], diverged: tuple[Path, Path, Path]
    ) -> None:
        registry, _, beto = diverged
        envelope(capsys, "--config", str(beto), "dist", "fetch", "demo/brain", "--tag", "v2", "--local", str(registry))

        code, payload = envelope(capsys, "--config", str(beto), "brain", "history", "--graph")

        assert code == ExitCode.OK
        assert len(payload["data"]["snapshots"][0]["parents"]) >= 2
        assert payload["data"]["ancestry"]

    def test_the_graph_renders_without_raising(
        self, capsys: pytest.CaptureFixture[str], diverged: tuple[Path, Path, Path]
    ) -> None:
        """The human rendering, not the envelope. `render.graph` reads `parents`; the renderer it replaced read
        `parent`, which 0.6 removed, and `Mapping.get` returned None rather than failing."""
        registry, _, beto = diverged
        run(capsys, "--config", str(beto), "dist", "fetch", "demo/brain", "--tag", "v2", "--local", str(registry))

        code, out, _ = run(capsys, "--config", str(beto), "brain", "history", "--graph")

        assert code == ExitCode.OK
        assert "first-parent chain" in out, "the legend is what makes the glyphs readable"
        assert "M" in out


class TestTheInteractiveRefusals:
    def test_resolve_without_a_block_refuses_json_mode(
        self, capsys: pytest.CaptureFixture[str], diverged: tuple[Path, Path, Path]
    ) -> None:
        """`--json` names an output mode the workspace has none of, which is worth saying rather than opening an
        interface whose result no caller can read."""
        _, _, beto = diverged
        code, payload = envelope(capsys, "--config", str(beto), "reconcile", "resolve")

        assert code == ExitCode.USAGE
        assert "interactive" in payload["error"]["message"]
        assert "resolve" in (payload["error"]["hint"] or "")

    def test_resolve_without_a_terminal_refuses(
        self, capsys: pytest.CaptureFixture[str], diverged: tuple[Path, Path, Path]
    ) -> None:
        """capsys makes stdout a pipe, which is exactly the condition being checked."""
        _, _, beto = diverged
        code, _, err = run(capsys, "--config", str(beto), "reconcile", "resolve")

        assert code == ExitCode.USAGE
        assert "terminal" in err

    def test_a_decision_needs_a_block(
        self, capsys: pytest.CaptureFixture[str], diverged: tuple[Path, Path, Path]
    ) -> None:
        _, _, beto = diverged
        code, payload = envelope(capsys, "--config", str(beto), "reconcile", "resolve", "--admit")

        assert code == ExitCode.USAGE
        assert "block" in payload["error"]["message"]

    def test_two_decisions_at_once_are_refused(
        self, capsys: pytest.CaptureFixture[str], diverged: tuple[Path, Path, Path]
    ) -> None:
        _, _, beto = diverged
        code, payload = envelope(
            capsys, "--config", str(beto), "reconcile", "resolve", "sha256:" + "ab" * 32, "--admit", "--reject"
        )

        assert code == ExitCode.USAGE
        assert "exactly one decision" in payload["error"]["message"]


async def _settle(pilot: Any, ticks: int = 25) -> None:
    """Let the worker threads finish. Same reason `test_tui` has one: every read here runs off the event loop."""
    from textual.app import App

    app: App[Any] = pilot.app
    for _ in range(ticks):
        await pilot.pause()
        if not any(worker.is_running for worker in app.workers):
            await pilot.pause()
            return


class TestTheResolverInterface:
    """The workspace itself, driven by keys.

    Worth exercising rather than trusting: it is the one surface that *starts* a reconciliation, and its footer
    hides decisions the protocol forbids -- a claim that is only true if `check_action` really is consulted.
    """

    async def test_it_lists_the_open_questions_and_hides_admit_on_a_rejection(self, halted: tuple[Any, Path]) -> None:
        from textual.widgets import DataTable

        from vitruvio.cli.tui.reconcile import Resolver

        beto, _ = halted
        app = Resolver(beto, strategy="merge")
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)

            assert app.status["open"] is True
            assert app.query_one("#questions", DataTable).row_count == len(app.questions)

            for entry in app.questions:
                available = app._available(entry)
                assert "reject" in available, "declining is always available"
                if entry["status"] == "rejected":
                    assert "admit" not in available
                    assert app.check_action("admit", ()) in {False, None} or app.selected is not entry

    async def test_rejecting_and_concluding_walks_the_whole_loop(self, halted: tuple[Any, Path]) -> None:
        """`r` then `k` then `c`, which is the sequence a person actually types."""
        from vitruvio.cli.tui.reconcile import Resolver

        beto, _ = halted
        before = beto.state()["snapshot"]["digest"]

        app = Resolver(beto, strategy="merge")
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            for _ in range(len(app.questions)):
                await pilot.press("r")
                await _settle(pilot)
            if not app.status.get("removals_accepted", True):
                await pilot.press("k")
                await _settle(pilot)
            assert app.status["is_resolved"] is True, "every question answered"
            await pilot.press("c")
            await _settle(pilot)

        assert beto.reconcile_ops.status()["open"] is False, "the loop concluded"
        assert beto.state()["snapshot"]["digest"] != before
        assert beto.verify()["verified"] is True

    async def test_it_says_so_when_there_is_nothing_open(self, tmp_path: Path) -> None:
        """An empty table would read as "no questions"; the difference matters, so it is stated."""
        from vitruvio.cli.tui.reconcile import Resolver

        config = make(tmp_path, "solo", reconcile="merge")
        service = BrainService(resolve(brain=tmp_path / "solo" / "brain", config=config))

        app = Resolver(service, strategy="merge")
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            assert app.sub_title == "nothing open"
            assert app.questions == []
