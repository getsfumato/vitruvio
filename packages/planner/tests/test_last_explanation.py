"""The explanation a caller reads after ``plan`` is the one of its own search, however many threads share the planner."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from vitruvio.kernel import PlannerConfig
from vitruvio.planner.planner import CostBasedPlanner


def test_each_thread_reads_the_explanation_of_its_own_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two searches interleave exactly as an HTTP service runs them: both plan, then both read ``last_explanation``.

    With one shared slot the second plan replaced the first before it was read, so a canonical search reported the
    plan of a semantic one.
    """
    planner = CostBasedPlanner(PlannerConfig())
    planned = threading.Barrier(2)

    def run(self: CostBasedPlanner, query: Any, modules: Any, **_: Any) -> tuple[Any, Any]:
        return f"bundle:{query}", f"explanation:{query}"

    monkeypatch.setattr(CostBasedPlanner, "_run", run)
    seen: dict[str, Any] = {}

    def search(name: str) -> None:
        bundle = planner.plan(name, {})  # type: ignore[arg-type]
        planned.wait()  # Both plans have run before either reads its explanation.
        seen[name] = (bundle, planner.last_explanation)

    threads = [threading.Thread(target=search, args=(name,)) for name in ("canonical", "semantic")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert seen["canonical"] == ("bundle:canonical", "explanation:canonical")
    assert seen["semantic"] == ("bundle:semantic", "explanation:semantic")


def test_a_thread_that_never_planned_reads_nothing() -> None:
    planner = CostBasedPlanner(PlannerConfig())
    assert planner.last_explanation is None
