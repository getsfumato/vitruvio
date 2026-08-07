"""Human rendering. Nothing here is reached in ``--json`` mode.

Kept separate from the command bodies so that a command reads as "resolve, call the service, hand the result
to a renderer" -- and so that rendering churn, which is expected and frequent, never touches the code that
decides what an operation does.
"""

from __future__ import annotations

from vitruvio.cli.render.evidence import bundle, modules, short, snapshot

__all__ = ["bundle", "modules", "short", "snapshot"]
