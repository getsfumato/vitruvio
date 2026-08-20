"""Every delegator on `BrainService` must match the operation it delegates to.

`BrainService` keeps one method per protocol operation -- ADR-0003 -- while the implementations move into
`vitruvio.runtime.ops`. That means the signature of each operation is now written twice, and a facade whose
signature has drifted from its operation is a second place to keep in sync forever: strictly worse than the single
large class it replaced.

The failure is not hypothetical. `apps/cli/tests/test_project.py` exists because `dist push --all` forwarded six of
`push`'s seven options and silently dropped `anonymous`, so a user who asked for no credentials published with
whatever was in the keyring. That is the same defect one layer down, and only a signature comparison catches it --
mypy will not, because a delegator that drops a keyword argument is internally consistent.

Written as data rather than by introspecting the class, so that adding an operation to a domain and forgetting the
delegator is a failure here rather than something nobody notices.
"""

from __future__ import annotations

from inspect import signature
from typing import Any

import pytest

from vitruvio.kernel import ResolvedConfig
from vitruvio.runtime import BrainService
from vitruvio.runtime.ops.embedders import EmbedderOps
from vitruvio.runtime.ops.inspection import InspectionOps
from vitruvio.runtime.ops.registration import RegistrationOps
from vitruvio.runtime.ops.retention import RetentionOps

DOMAINS: tuple[tuple[type, tuple[str, ...]], ...] = (
    (EmbedderOps, ("embedders", "test_embedder")),
    (InspectionOps, ("resolvability", "resolve", "prove", "module", "roots")),
    (RegistrationOps, ("register", "replace", "put_content")),
    (RetentionOps, ("plan_drop", "drop", "drop_by_producer", "supersede", "demote", "prune", "redact", "policy")),
)
"""Each operations class, and the names `BrainService` must expose for it.

Extended as each domain moves. The second element is deliberately explicit rather than derived from the class: a
list computed from the class cannot tell the difference between "this operation has no delegator" and "this
operation was never meant to have one".
"""

CASES = [(domain, name) for domain, names in DOMAINS for name in names]


@pytest.mark.parametrize(("domain", "name"), CASES, ids=[f"{d.__name__}.{n}" for d, n in CASES])
class TestTheFacadeMatchesItsOperations:
    def test_the_facade_exposes_it(self, domain: type, name: str) -> None:
        assert callable(getattr(BrainService, name, None)), (
            f"{domain.__name__}.{name} has no delegator on BrainService. Every protocol operation is reachable "
            "from the service -- see ADR-0003 -- and the CLI, the TUI and the future MCP server all reach it there."
        )

    def test_the_signatures_are_identical(self, domain: type, name: str) -> None:
        """Including keyword-only arguments and their defaults, which is where the silent drop happens."""
        assert signature(getattr(BrainService, name)) == signature(getattr(domain, name)), (
            f"BrainService.{name} and {domain.__name__}.{name} disagree. A delegator that drops a keyword argument "
            "type-checks clean and fails only at runtime, for whichever caller happened to pass it."
        )

    def test_both_carry_a_docstring(self, domain: type, name: str) -> None:
        """The operation documents itself; the delegator summarises and points at it.

        `help(BrainService.push)` is the documented public API, so a facade of bare stubs would empty it.
        """
        assert (getattr(BrainService, name).__doc__ or "").strip()
        assert (getattr(domain, name).__doc__ or "").strip()


def test_a_delegator_actually_delegates(config: ResolvedConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    """The signatures could match while the body calls the wrong thing, or drops an argument on the way.

    Checked on `test_embedder` because it is the one with keyword-only arguments, which is the shape that broke in
    `dist push --all`.
    """
    seen: list[dict[str, Any]] = []

    def spy(self: EmbedderOps, *, which: str = "text", text: str | None = None) -> dict[str, Any]:
        seen.append({"which": which, "text": text})
        return {}

    monkeypatch.setattr(EmbedderOps, "test_embedder", spy)
    BrainService(config).test_embedder(which="vision", text="senos")
    assert seen == [{"which": "vision", "text": "senos"}]


def test_the_operations_object_is_built_once(config: ResolvedConfig) -> None:
    """Lazily, and then memoized: constructing a service must stay free, and two calls must not build two."""
    service = BrainService(config)
    assert "embedder_ops" not in vars(service), "constructing a service built an operations object"
    assert service.embedder_ops is service.embedder_ops


def test_every_operations_object_shares_the_service_session(config: ResolvedConfig) -> None:
    """The property `invalidate()` depends on. One cache, reachable from everything that reads a brain."""
    service = BrainService(config)
    assert service.embedder_ops.session is service.session
