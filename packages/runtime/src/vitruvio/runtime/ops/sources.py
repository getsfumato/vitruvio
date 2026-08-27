"""Sources declared in ``vitruvio.toml``: listing them, editing them, and pulling from them.

`pull` is `register` with the bytes fetched rather than handed over, and the interesting part is the three
things that stop it registering the same material twice -- or, in one case, registering material that was
deliberately destroyed.
1. **The tombstone guard.** `Brain.register` calls `store.put_bytes(data)` *before* its duplicate check, and
`OciLayoutStore.has` returns True for a tombstoned digest while `tombstone()` unlinks the file. So
re-fetching redacted bytes writes the destroyed bytes back onto disk and then quietly reports
`duplicate=True`. A scheduled `source pull` is precisely the machine that would silently undo `retain
redact` -- the command whose own docstring says it is for personal data, credentials and licensed
material. Nothing else here may assume `register` is safe on these bytes.
2. **The origin index.** `origin` is projected as an identity key, so "have I acquired this?" is one
hash-map probe. It runs *before* the fetch, which is what makes a repeated pull cheap rather than merely
idempotent. A hit is compared against the declaration before it is trusted: changing a source's
`media_type` or `normalize_with` must re-register, because both are part of a block's identity and a
silent skip would make the correction do nothing at all.
3. **Content addressing**, which needs no code: identical bytes compute the same block identity and
`register` returns `duplicate=True`. It is the backstop for every source that cannot produce a stable
origin, at the cost of one wasted download.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any

from vitruvio.kernel import ResolvedConfig
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.ops.fetch import FetchOps
from vitruvio.runtime.session import BrainSession


class SourceOps:
    """Declared sources, as operations."""

    def __init__(self, session: BrainSession) -> None:
        """
        Args:
            session (BrainSession): The shared session.
        """
        self.session = session
        self.fetch = FetchOps(session)

    @property
    def config(self) -> ResolvedConfig:
        """The resolved configuration, read through the session that owns it."""
        return self.session.config

    def sources(self) -> dict[str, Any]:
        """
        Every declared source, whether it can be used, and where its kind came from.

        Constructing each source is what tells you it is usable, so this constructs them -- and a construction that
        fails is reported as a row rather than raised, because one broken declaration must not hide the other five
        that are fine. That is the difference between ``status`` and ``pull``.

        Returns:
            dict[str, Any]: A row per source, plus the installed kinds.
        """
        from vitruvio.ingest.sources import describe as describe_sources
        from vitruvio.kernel import VitruvioError

        rows: list[dict[str, Any]] = []
        for name, spec in sorted(self.config.sources.items()):
            row: dict[str, Any] = {
                "name": name,
                "kind": spec.kind,
                "brain": self.config.brain_name or str(self.config.brain),
                "path": str(self.config.source_root(name) or "") or None,
                "normalize_with": spec.normalize_with,
            }
            try:
                source = self.fetch._source(name, spec)
            except (VitruvioError, ValueError) as error:
                rows.append({**row, "available": False, "reason": str(error), "provenance": None})
                continue
            rows.append(
                {
                    **row,
                    "available": source.available,
                    "reason": source.unavailable_because(),
                    "provenance": self.fetch._kind_provenance(spec.kind),
                }
            )
        return {
            "brain": self.config.brain_name or str(self.config.brain),
            "sources": rows,
            "kinds": describe_sources(),
            "config_file": str(self.config.config_file or ""),
        }

    def source_kinds(self) -> dict[str, Any]:
        """
        Every source kind this installation can construct.

        Returns:
            dict[str, Any]: The kinds, and where a hand-written one would go.
        """
        from vitruvio.ingest.sources import describe as describe_sources
        from vitruvio.kernel import plugin_dir

        return {"kinds": describe_sources(), "plugin_dir": str(plugin_dir())}

    def scaffold_source(self, kind: str, *, force: bool = False) -> dict[str, Any]:
        """
        Write a starter plugin for one kind into the user's plugin directory.

        Args:
            kind (str): The kind name, as it will appear in ``vitruvio.toml``.
            force (bool): Overwrite an existing file.

        Returns:
            dict[str, Any]: Where it was written.

        Raises:
            UsageError: If a file for that kind already exists and ``force`` was not given. Refusing rather than
                overwriting: that file is hand-written code, and it is the one thing here no content address can
                recover.
        """
        from vitruvio.ingest.sources import scaffold
        from vitruvio.kernel import UsageError, plugin_dir

        directory = plugin_dir()
        target = directory / f"{kind.replace('-', '_')}.py"
        existed = target.exists()
        if existed and not force:
            raise UsageError(
                f"{target} already exists",
                hint="edit it, or pass --force to overwrite what is there",
            )
        directory.mkdir(parents=True, exist_ok=True)
        target.write_text(scaffold(kind), encoding="utf-8")
        return {"kind": kind, "path": str(target), "overwritten": existed}

    def add_source(
        self,
        name: str,
        *,
        kind: str,
        path: str | None = None,
        media_type: str | None = None,
        normalize_with: str | None = None,
        license_id: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Declare a source in ``vitruvio.toml``.

        Args:
            name (str): What to call it.
            kind (str): Which strategy acquires from it.
            path (str | None): Its root, recorded as given and resolved against the configuration file.
            media_type (str | None): Override the inferred media type.
            normalize_with (str | None): A normalization pipeline.
            license_id (str | None): Recorded on every block from this source.
            options (dict[str, Any] | None): Kind-specific fields.

        Returns:
            dict[str, Any]: The declaration, and a warning when its path sits outside the project.

        Raises:
            ConfigError: If there is no configuration file to write to.
            UsageError: If the name is already taken.
        """
        import pathlib

        from vitruvio.kernel import ConfigError, SourceSpec, UsageError, update_config

        config_path = self.config.config_file
        if config_path is None:
            raise ConfigError(
                "this project has no vitruvio.toml to declare a source in",
                hint="run `vitruvio project init <name>` first, or `vitruvio brain init`",
            )
        if name in self.config.sources:
            raise UsageError(
                f"this brain already declares a source called {name!r}",
                hint="pick another name, or `vitruvio source remove` it first",
            )

        spec = SourceSpec(
            kind=kind,
            path=path,
            media_type=media_type,
            normalize_with=normalize_with,
            license=license_id,
            options=options or {},
        )
        # One call for the whole table, not one per field: `update_config` validates the entire document before
        # writing, so writing `kind` first would submit an intermediate document missing required fields.
        update_config(config_path, self.config.source_config_key(name), spec.model_dump(exclude_none=True, mode="json"))

        # Resolved here rather than through `source_root`, which reads the configuration this process loaded -- and
        # that copy predates the write above, so it does not know about this source yet.
        root = (
            (config_path.parent / pathlib.Path(path).expanduser()).expanduser().resolve() if path is not None else None
        )
        warning = None
        if root is not None and not root.is_relative_to(config_path.parent):
            # Worth saying out loud once. A directory source composes with `dist push` into a way to publish
            # something nobody meant to: point one at the wrong folder and a private key becomes a canonical block,
            # content-addressed and Merkle-committed in a public repository.
            warning = f"{root} is outside the project directory; everything matching will become canonical evidence"
        return {
            "name": name,
            "kind": kind,
            "brain": self.config.brain_name or str(self.config.brain),
            "path": str(root) if root else None,
            "config_file": str(config_path),
            "warning": warning,
        }

    def remove_source(self, name: str) -> dict[str, Any]:
        """
        Undeclare a source. Nothing it ever registered is touched.

        Args:
            name (str): The source's name.

        Returns:
            dict[str, Any]: What was removed.

        Raises:
            UsageError: If the selected brain declares no such source.
        """
        from vitruvio.kernel import UsageError, update_config

        config_path = self.config.config_file
        if config_path is None or name not in self.config.sources:
            raise UsageError(
                f"this brain declares no source called {name!r}",
                hint=f"declared: {', '.join(sorted(self.config.sources)) or '(none)'}",
            )
        update_config(config_path, self.config.source_config_key(name), None)
        return {
            "name": name,
            "brain": self.config.brain_name or str(self.config.brain),
            "config_file": str(config_path),
        }

    def pull_source(
        self,
        name: str,
        *,
        dry_run: bool = False,
        limit: int | None = None,
        refetch: bool = False,
        option_overrides: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """
        Acquire from one declared source and register what is new as canonical evidence.

        Args:
            name (str): The source's name in the project.
            dry_run (bool): List and decide, fetch nothing and register nothing. What to run first when a source
                has just been pointed at a directory.
            limit (int | None): Stop after this many *registrations*, not this many items -- a limit that counted
                skips would do nothing on the second run, which is the run people repeat.
            refetch (bool): Ignore the origin index. For a source whose addresses turned out to be unstable, or to
                bring back a block that was dropped.
            option_overrides (Mapping[str, object] | None): Kind-specific values for this invocation. They override
                the declaration's ``options`` without rewriting ``vitruvio.toml``.

        Returns:
            dict[str, Any]: A row per item with what happened to it, and the totals.

        Raises:
            UsageError: If the selected brain does not declare the source.
        """
        from vitruvio.kernel import UsageError

        declared = self.config.sources.get(name)
        if declared is None:
            raise UsageError(
                f"this brain declares no source called {name!r}",
                hint=f"declared: {', '.join(sorted(self.config.sources)) or '(none)'}",
            )
        overrides = dict(option_overrides or {})
        spec = declared.model_copy(update={"options": {**declared.options, **overrides}})
        source = self.fetch._source(name, spec)

        with translated():
            items = list(source.list())

        scope = nullcontext(self.session.brain(Capability.INSPECT)) if dry_run else self.session.write()
        with scope as brain:
            rows: list[dict[str, Any]] = []
            registered = 0
            for item in items:
                if limit is not None and registered >= limit:
                    rows.append({**self.fetch._item_row(item), "outcome": "not-reached"})
                    continue
                row = self.fetch._pull_one(brain, source, spec, item, dry_run=dry_run, refetch=refetch)
                rows.append(row)
                if row["outcome"] == "registered":
                    registered += 1

            counts: dict[str, int] = {}
            for row in rows:
                counts[str(row["outcome"])] = counts.get(str(row["outcome"]), 0) + 1
            return {
                "source": name,
                "kind": spec.kind,
                "brain": self.config.brain_name or str(self.config.brain),
                "listed": len(items),
                "registered": registered,
                "dry_run": dry_run,
                "option_overrides": sorted(overrides),
                "counts": counts,
                "items": rows,
            }

    def pull_all(self, *, dry_run: bool = False, limit: int | None = None, refetch: bool = False) -> dict[str, Any]:
        """
        Pull every source declared by the selected brain.

        Keeps going past a failed source, for the same reason ``dist push --all`` does: being told which one of six
        failed is better than stopping at the first and leaving four that would have worked unpulled and
        unmentioned.

        The loop lives here rather than in the CLI on purpose. ``dist``'s equivalent sits in the CLI and is already
        the thinnest part of that boundary; repeating it would mean the MCP server reimplements "which failures are
        fatal", and a second implementation of that is a second set of answers.

        Args:
            dry_run (bool): Decide without fetching or registering.
            limit (int | None): Per source, not in total.
            refetch (bool): Ignore the origin index.

        Returns:
            dict[str, Any]: A result per source, and whether every one succeeded.

        Raises:
            ConfigError: If the selected brain declares no sources at all.
        """
        from vitruvio.kernel import ConfigError, VitruvioError

        declared = self.config.sources
        if not declared:
            raise ConfigError(
                "this brain declares no sources",
                hint="declare one with `vitruvio source add <name> --kind directory --path ...`",
            )

        results: list[dict[str, Any]] = []
        for name, spec in sorted(declared.items()):
            try:
                outcome = self.pull_source(name, dry_run=dry_run, limit=limit, refetch=refetch)
                results.append({"ok": True, **outcome})
            except VitruvioError as error:
                # Per-source failures accumulate; per-item ones already did, inside `pull_source`. A source that is
                # down, or a tool that is not installed, says so and the next source still runs.
                results.append(
                    {
                        "ok": False,
                        "source": name,
                        "kind": spec.kind,
                        "brain": self.config.brain_name or str(self.config.brain),
                        "error": str(error),
                        "code": error.code,
                    }
                )
        return {
            "sources": results,
            "brain": self.config.brain_name or str(self.config.brain),
            "ok": all(bool(result["ok"]) for result in results),
            "registered": sum(int(result.get("registered", 0)) for result in results),
            "dry_run": dry_run,
        }
