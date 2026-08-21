"""Creating a brain, and reading what it currently is.

Every operation opens at ``INSPECT`` -- or, for `init`, creates -- and none registers an index. That is the whole
reason the capability exists: `state` reads a pointer file, and standing up a vector index to do it would import
an embedder.

`init` calls ``open_brain`` directly, with ``create=True``: a session hands out brains that already exist.
"""

from __future__ import annotations

from typing import Any

from vitruvio.kernel import ResolvedConfig, VitruvioError
from vitruvio.runtime import wire
from vitruvio.runtime.assembly import Capability, open_brain
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.session import BrainSession


class LifecycleOps:
    """The brain's lifecycle, as operations."""

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

    def init(self, *, force: bool = False) -> dict[str, Any]:
        """
        Create a brain, and a ``vitruvio.toml`` beside it.

        Writing the configuration file is the part that makes the brain reproducible: it records the actor, the
        policy and the embedder, so a second clone retrieves comparably rather than by coincidence.

        Args:
            force (bool): Overwrite an existing ``vitruvio.toml``. The layout itself is never overwritten --
                ``Brain`` opening an existing layout is a normal open, not a clobber.

        Returns:
            dict[str, Any]: The brain path, the configuration written, and the empty snapshot's digest.

        Raises:
            VitruvioError: If the path exists and holds something that is not a brain.
        """
        from vitruvio.kernel import CONFIG_FILE, is_layout, update_config

        path = self.config.brain
        existed = is_layout(path)
        if path.exists() and not path.is_dir():
            raise VitruvioError(f"{path} is a file, not a directory", hint="choose a path for the brain directory")
        if path.exists() and any(path.iterdir()) and not existed:
            raise VitruvioError(
                f"{path} is not empty and is not a brain",
                hint="choose an empty directory, or an existing brain",
            )

        with translated():
            brain = open_brain(self.config, Capability.INSPECT, create=True)

        config_path = (self.config.config_file or path.parent / CONFIG_FILE).resolve()
        wrote_config = False
        if force or not config_path.exists():
            try:
                relative = path.resolve().relative_to(config_path.parent)
                declared = f"./{relative}"
            except ValueError:
                # The brain is not under the configuration file's directory. An absolute path is correct here,
                # and honest: this project is not self-contained.
                declared = str(path.resolve())
            update_config(config_path, "brain.path", declared)
            if self.config.project.actor.id:
                update_config(config_path, "actor.id", self.config.project.actor.id)
                update_config(config_path, "actor.kind", self.config.project.actor.kind.value)
            update_config(config_path, "policy.profile", self.config.project.policy.profile.value)
            wrote_config = True

        return {
            "brain": str(path),
            "created": not existed,
            "config_file": str(config_path) if wrote_config else None,
            "snapshot": wire.snapshot(brain.snapshot()),
        }

    def state(self) -> dict[str, Any]:
        """
        The brain's head pointer, snapshot and installed modules.

        Returns:
            dict[str, Any]: Enough to answer "what is installed, at which version, pulled from where".
        """
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            snapshot = brain.snapshot()
            return {
                "brain": str(self.config.brain),
                "brain_origin": self.config.brain_origin.value,
                "state": brain.state(),
                "snapshot": wire.snapshot(snapshot),
                "installed": [kind.value for kind in snapshot.installed],
                "block_count": snapshot.block_count,
                "origin": brain.origin.model_dump(mode="json") if brain.origin else None,
                "ancestry": [str(digest) for digest in brain.ancestry()],
                "actor": self.config.project.actor.model_dump(mode="json"),
            }

    def verify(self) -> dict[str, Any]:
        """
        Recompute every module's Merkle root from its blocks and compare.

        Returns:
            dict[str, Any]: Whether the brain verifies, and each module's root.
        """
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            snapshot = brain.snapshot()
            return {
                "verified": brain.verify(),
                "roots": {kind.value: str(snapshot.root_of(kind)) for kind in snapshot.installed},
                "block_count": snapshot.block_count,
            }

    def history(self, *, limit: int | None = None) -> dict[str, Any]:
        """
        The retained snapshots, most recent first.

        Args:
            limit (int | None): How many to return.

        Returns:
            dict[str, Any]: The chain a prune walks, and an audit reads.
        """
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            snapshots = brain.history()
            # The two answer different questions and a reader needs both to make sense of the list. `ancestry` is
            # the first-parent chain -- what the protocol reads as *what this brain is*, and what an audit walks.
            # `reachable` is containment across every parent, which is what a fast-forward check asks: a merged-in
            # history is genuinely in here without appearing on the chain. Reported rather than derived, because
            # being *a* parent of something retained does not put a snapshot on either.
            chain = [str(digest) for digest in brain.ancestry()]
            reachable = sorted(str(digest) for digest in brain.reachable_history())
        chosen = snapshots[:limit] if limit else snapshots
        return {
            "snapshots": [wire.snapshot(item) for item in chosen],
            "retained": len(snapshots),
            "ancestry": chain,
            "reachable": reachable,
        }

    def info(self) -> dict[str, Any]:
        """
        Per-module shape: roots, block counts, and which indices are registered.

        Returns:
            dict[str, Any]: The brain's anatomy.
        """
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            modules = brain.modules()
            return {
                "brain": str(self.config.brain),
                "modules": [wire.module(module) for module in modules.values()],
                # Read from disk rather than from `brain.travelling_indices`. At INSPECT capability no index is
                # registered -- deliberately, so this command never constructs an embedder -- so the brain's own answer
                # would be an honest "none about this session" and a misleading answer to the question actually being
                # asked, which is whether a publish would carry a vector index.
                "travelling_indices": self._travelling_on_disk(),
                "policy": self.config.policy().model_dump(mode="json"),
            }

    def _travelling_on_disk(self) -> list[str]:
        """
        Which modules have a vector index persisted.

        Delegated to :func:`vitruvio.runtime.indexset.travelling_on_disk`, which owns where the sidecars live.
        Reading the directory here as well is how the same path came to be written in six places.

        Returns:
            list[str]: Memory types with a non-empty vector index on disk.
        """
        from vitruvio.runtime.indexset import travelling_on_disk

        return travelling_on_disk(self.config)
