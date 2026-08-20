"""Building, verifying and collecting the indices.



Where the sidecars live is :mod:`vitruvio.runtime.indexset`'s to say, not this module's.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from boltzmann.brain import Brain

from vitruvio.kernel import ResolvedConfig
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.coerce import memory_type as coerce_memory_type
from vitruvio.runtime.indexset import indices_home
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.session import BrainSession


class IndexOps:
    """The indices, as operations."""

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

    def _index_set(self) -> Any:
        """
        A detached index set, over the brain's derived directory.

        For reporting only. Anything that has to *vouch* for a travelling index must go through :meth:`_set_from`
        instead, because vouching only works on indices the brain has registered.
        """
        from vitruvio.indices import build_index_set

        return build_index_set(
            self.config.project.indices,
            home=indices_home(self.config),
            config=self.config,
        )

    def _set_from(self, brain: Brain) -> Any:
        """
        The index set the brain itself holds.

        The same objects, so building through them and then vouching describes one state rather than two. A detached
        set builds indices the brain has never seen, and the vector layer is then omitted from every publish -- which
        is what running the CLI showed before this existed.

        Args:
            brain (Brain): The opened brain, at a capability that registers indices.

        Returns:
            Any: An ``IndexSet`` over the brain's own index objects.
        """
        from vitruvio.indices import IndexSet, VitruvioIndex

        collected = IndexSet(indices_home(self.config))
        for registered in brain.indices.values():
            for index in registered:
                if isinstance(index, VitruvioIndex):
                    collected.add(index)
        return collected

    def index_list(self) -> dict[str, Any]:
        """
        Every registered index, with what it holds and where it lives.

        Returns:
            dict[str, Any]: A row per index, plus any declared kind this build cannot construct -- reported
            rather than dropped, because an index the user asked for and did not get is exactly what must not
            pass unnoticed.
        """
        indices = self._index_set()
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            modules = brain.modules()
        capabilities = {
            (entry.memory_type, entry.kind): entry
            for entries in indices.capabilities(modules).values()
            for entry in entries
        }
        rows = []
        for row in indices.report():
            capability = capabilities.get((row["memory_type"], row["kind"]))
            rows.append({**row, "state": capability.state if capability else "absent"})
        return {
            "brain": str(self.config.brain),
            "home": str(indices_home(self.config)),
            "indices": rows,
            "unavailable": indices.unavailable,
        }

    def index_build(
        self,
        *,
        memory_types: Iterable[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """
        Build or refresh the indices, and persist the statistics they measured.

        Args:
            memory_types (Iterable[str] | None): Restrict to these modules.
            force (bool): Discard held state and rebuild from scratch.

        Returns:
            dict[str, Any]: A row per index, and where the files went.
        """
        from vitruvio.stats import save

        chosen = {coerce_memory_type(item) for item in memory_types} if memory_types else None

        # Opened at RETRIEVE so the brain has the indices *registered*, and built through those rather than through a
        # separate set. That is not tidiness: vouching only works on indices the brain knows about, so building a
        # detached set left the vector index unvouched and every publish silently omitted it -- which is what running
        # the CLI showed.
        brain = self.session.brain(Capability.RETRIEVE)
        indices = self._set_from(brain)

        with translated():
            modules = brain.modules()
            for memory_type, module in modules.items():
                if chosen is not None and memory_type not in chosen:
                    continue
                readable = [
                    module.get(identity) for identity in module.block_ids if module.store.is_resolvable(identity)
                ]
                for index in indices.for_module(memory_type):
                    if force:
                        # A rebuild-from-scratch is expressed by dropping the held state, not by a flag on
                        # build(): the incremental path is an internal optimisation and must stay invisible.
                        index.build([], module.store)
                    index.build(readable, module.store)
            indices.bind(modules)

        # Tell the SDK the vector index it now holds describes this composition. Without this, `pack()` silently omits
        # the one layer a consumer cannot rebuild -- see vitruvio.runtime.vouch for why the workaround exists.
        from vitruvio.runtime.vouch import vouch_travelling

        vouched = vouch_travelling(brain, chosen)

        written = indices.flush()
        statistics = indices.statistics(modules)
        for memory_type, stats in statistics.items():
            save(stats, self.config.derived / "stats" / f"{memory_type.value}.json")

        return {
            "home": str(indices_home(self.config)),
            "written": len(written),
            "indices": indices.report(),
            "statistics": [stats.summary() for stats in statistics.values()],
            "travelling": [kind.value for kind in brain.travelling_indices],
            "vouched": vouched,
        }

    def index_stats(self, *, memory_type: str | None = None) -> dict[str, Any]:
        """
        The statistics catalogue, as the planner sees it.

        Args:
            memory_type (str | None): Restrict to one module.

        Returns:
            dict[str, Any]: A summary per module.
        """
        chosen = coerce_memory_type(memory_type) if memory_type else None
        indices = self._index_set()
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            modules = brain.modules()
        statistics = indices.statistics(modules)
        return {
            "statistics": [
                stats.summary()
                for kind, stats in sorted(statistics.items(), key=lambda pair: pair[0].value)
                if chosen is None or kind is chosen
            ]
        }

    def index_verify(self) -> dict[str, Any]:
        """
        Check each index against the composition it claims to describe.

        Returns:
            dict[str, Any]: A capability per index, and how many are stale.
        """
        indices = self._index_set()
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            modules = brain.modules()
        from dataclasses import asdict

        # `asdict` rather than `__dict__`: Capability is a slots dataclass, so it has no instance dictionary.
        rows = [
            asdict(entry) | {"usable": entry.usable}
            for entries in indices.capabilities(modules).values()
            for entry in entries
        ]
        return {
            "capabilities": rows,
            "stale": sum(1 for row in rows if row["state"] == "stale"),
            "empty": sum(1 for row in rows if row["state"] == "empty"),
        }

    def index_gc(self, *, apply: bool = False) -> dict[str, Any]:
        """
        Remove index files no declared index owns.

        Args:
            apply (bool): Actually delete. A dry run otherwise.

        Returns:
            dict[str, Any]: What was removed, or what would be.
        """
        indices = self._index_set()
        keep = {f"{row['memory_type']}.{row['kind']}" for row in indices.report()}
        home = indices_home(self.config)
        candidates = [path for path in sorted(home.glob("*.vidx")) if path.name.removesuffix(".vidx") not in keep]
        if apply:
            for path in candidates:
                path.unlink()
        return {"removed": [str(path) for path in candidates], "applied": apply}
