"""``BrainService`` -- one generated facade over domain operations.

The public operation surface is generated from :mod:`vitruvio.runtime.operation_catalogue`. Domain logic stays in
``runtime.ops``; this module owns only the resolved configuration and the shared :class:`BrainSession`.
"""

from __future__ import annotations

from boltzmann.brain import Brain

from vitruvio.kernel import ResolvedConfig
from vitruvio.runtime._generated_facade import GeneratedFacade
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.session import BrainSession


class BrainService(GeneratedFacade):
    """The protocol, as operations a caller can drive without knowing the SDK."""

    def __init__(self, config: ResolvedConfig) -> None:
        """Build a lazy service over a resolved configuration."""
        self.config = config
        self.session = BrainSession(config)

    def brain(self, capability: Capability = Capability.INSPECT) -> Brain:
        """Return the session-owned brain opened at ``capability``."""
        return self.session.brain(capability)
