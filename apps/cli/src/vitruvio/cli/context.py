"""The global options, carried from the launcher to whichever command runs.

``--brain``, ``--json`` and friends are declared once on the meta app rather than repeated on forty
commands. That leaves the question of how a command reads them, and there are two bad answers: thread a
context parameter through every signature (which puts a parameter in the ``--help`` of every command that
is not really a parameter of any of them), or reach for a module-level global (which two tests running in
one process will fight over).

A :class:`~contextvars.ContextVar` is the third answer. It is set once by the launcher, read by whatever
command it dispatched to, and isolated per context -- so an in-process CLI test can set its own without
leaking into the next one.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from vitruvio.cli.output import Console

if TYPE_CHECKING:
    from pathlib import Path

    from vitruvio.kernel import ResolvedConfig
    from vitruvio.runtime import BrainService


@dataclass
class Context:
    """
    What every command may need to know, independent of its own arguments.

    Attributes:
        brain (Path | None): ``--brain``.
        config (Path | None): ``--config``.
        actor_id (str | None): ``--actor``.
        actor_kind (str | None): ``--actor-kind``, coerced by the kernel.
        console (Console): Where to write, already carrying the ``--json`` decision.
        verbosity (int): How many times ``-v`` was given.
    """

    brain: Path | None = None
    config: Path | None = None
    actor_id: str | None = None
    actor_kind: str | None = None
    console: Console = field(default_factory=Console)
    verbosity: int = 0

    def service(self, *, require_layout: bool = True) -> BrainService:
        """
        The service layer, over the resolved configuration.

        Every command past `config` and `brain use` goes through this. Constructing it is free -- no brain is
        opened until an operation asks, and each operation opens at its own capability.

        Args:
            require_layout (bool): Whether the selected path must already be a brain.

        Returns:
            BrainService: The service.
        """
        from vitruvio.runtime import BrainService

        return BrainService(self.resolve(require_layout=require_layout))

    def resolve(self, *, require_layout: bool = True) -> ResolvedConfig:
        """
        Merge these options with the environment, the project file and saved state.

        Args:
            require_layout (bool): Whether the selected path must already be a brain. ``brain init`` is the
                one caller that says no, because it is about to create one.

        Returns:
            ResolvedConfig: The resolved configuration.

        Raises:
            VitruvioError: If no brain is selected, or the configuration is invalid.
        """
        from vitruvio.kernel import resolve

        return resolve(
            brain=self.brain,
            config=self.config,
            actor_id=self.actor_id,
            actor_kind=self.actor_kind,
            require_layout=require_layout,
        )


_CURRENT: ContextVar[Context | None] = ContextVar("vitruvio_cli_context", default=None)
"""The installed context.

The default is ``None`` rather than a ``Context()``. A mutable default would be *one* object shared by every
context that never installed its own -- so a command that reached for ``console.warn`` outside a CLI run
would accumulate warnings into an object the next caller then inherits.
"""


def current() -> Context:
    """The context the launcher installed, or a fresh all-defaults one outside a CLI run."""
    context = _CURRENT.get()
    if context is None:
        context = Context()
        _CURRENT.set(context)
    return context


def install(context: Context) -> None:
    """
    Make ``context`` the one commands see.

    Args:
        context (Context): The context to install.
    """
    _CURRENT.set(context)
