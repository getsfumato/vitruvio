"""The service layer every vitruvio interface shares.

One method per protocol operation, each returning JSON-able data. The CLI renders it, and the MCP server and
HTTP API that come later serialize the same dictionaries -- which is what stops the three from drifting apart,
and what keeps them thin enough to be uninteresting.

An app may import this package and the kernel. An app may never import ``boltzmann`` directly: if it needs an
SDK type, the seam is in the wrong place. That rule is enforced by import-linter in CI.
"""

from __future__ import annotations

from vitruvio.runtime.assembly import Capability, build_indices, open_brain
from vitruvio.runtime.mapping import FALLBACK, Report, known_codes, report_for, translate
from vitruvio.runtime.service import BrainService
from vitruvio.runtime.vouch import vouch_travelling

__all__ = [
    "FALLBACK",
    "BrainService",
    "Capability",
    "Report",
    "build_indices",
    "known_codes",
    "open_brain",
    "report_for",
    "translate",
    "vouch_travelling",
]
