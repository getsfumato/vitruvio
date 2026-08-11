"""The terminal interface: a brain, read rather than queried.

Everything under this package is imported only by ``vitruvio browse``. That is deliberate -- Textual is a
larger import than the rest of the CLI put together, and ``config show`` must still start in tens of
milliseconds -- so nothing outside the ``browse`` command body may import this package at module level.

The interface holds **no protocol logic**. Every screen calls :class:`~vitruvio.runtime.BrainService` and
renders what comes back, exactly as a command body does, which is what keeps the TUI from becoming a second
implementation of anything. When a screen needs something the service cannot answer, the method belongs in the
service -- where the CLI and the future MCP server get it too.
"""

from __future__ import annotations

from vitruvio.cli.tui.app import MODULES, BrainBrowser, run

__all__ = ["MODULES", "BrainBrowser", "run"]
