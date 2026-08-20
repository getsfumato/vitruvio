"""Reading the composition itself: what is in it, what a block says, and whether the membership proves.

Every operation here opens at ``INSPECT``, which registers no index. That is the point of the capability rather
than an incidental property -- `vitruvio inspect module semantic` answers a question about a Merkle tree, and
standing up a vector index to answer it would import an embedder to read a pointer file.
"""

from __future__ import annotations

from typing import Any

from boltzmann.identity.digest import BlockId

from vitruvio.kernel import ResolvedConfig
from vitruvio.runtime import wire
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.coerce import memory_type as coerce_memory_type
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.session import BrainSession


class InspectionOps:
    """The composition, as operations."""

    def __init__(self, session: BrainSession) -> None:
        """
        Args:
            session (BrainSession): The shared session.
        """
        self.session = session

    @property
    def config(self) -> ResolvedConfig:
        """The resolved configuration, read through the session that owns it."""
        return self.session.config

    def resolvability(self) -> dict[str, Any]:
        """
        Which blocks are readable, which are tombstoned, and which are simply absent.

        The three are different and must not be conflated: a redacted block is a verifiable member whose bytes
        were destroyed under policy, and a caller has to be able to tell that from corruption.

        Returns:
            dict[str, Any]: The report, with counts per module.
        """
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            return wire.resolvability(brain.resolvability())

    def resolve(self, block_id: str) -> dict[str, Any]:
        """
        Read one block by identity, verified by hash on the way out of the store.

        Args:
            block_id (str): A ``sha256:...`` block identity.

        Returns:
            dict[str, Any]: The block's identity, memory type and payload.
        """
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            identity = BlockId.parse(block_id)
            block = brain.resolve(identity)
            return wire.block(block, block.MEMORY_TYPE)

    def prove(self, block_id: str, memory_type: str) -> dict[str, Any]:
        """
        A Merkle inclusion proof for one block, already checked against the module's root.

        Args:
            block_id (str): The block.
            memory_type (str): Which module should contain it.

        Returns:
            dict[str, Any]: The audit path, the root, and whether it verifies.
        """
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            kind = coerce_memory_type(memory_type)
            proof = brain.prove(BlockId.parse(block_id), kind)
            return wire.proof(proof, brain.root_of(kind))

    def module(self, memory_type: str, *, limit: int = 20) -> dict[str, Any]:
        """
        One module's shape and a sample of its block identities.

        Args:
            memory_type (str): Which module.
            limit (int): How many identities to list.

        Returns:
            dict[str, Any]: The module, plus a bounded list of block ids.
        """
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            module = brain.module(coerce_memory_type(memory_type))
            identities = [str(identity) for identity in module.block_ids]
            return {
                **wire.module(module),
                "block_ids": identities[:limit],
                "truncated": len(identities) > limit,
            }

    def roots(self) -> dict[str, Any]:
        """
        Every installed module's Merkle root.

        Returns:
            dict[str, Any]: Roots by memory type, plus the snapshot digest that pins the set.
        """
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            snapshot = brain.snapshot()
            return {
                "snapshot": str(snapshot.digest),
                "roots": {kind.value: str(snapshot.root_of(kind)) for kind in snapshot.installed},
            }
