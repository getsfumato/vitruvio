"""The project: which brains it holds, and adding or removing one.

`project` deliberately opens **no** brain. A project of six subjects would otherwise pay six index rebuilds to
answer "what is in here", and the answer is a configuration question -- what it reads is each layout's own snapshot
pointer, which is a file.

`add_brain` opens one, and not through the session: the brain being added has its own `ResolvedConfig`, so it is
not a brain this session owns and caching it under this session's key would be wrong.
"""

from __future__ import annotations

from typing import Any

from vitruvio.kernel import ResolvedConfig, VitruvioError
from vitruvio.runtime.assembly import Capability, open_brain
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.session import BrainSession


class ProjectOps:
    """The project, as operations."""

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

    def project(self) -> dict[str, Any]:
        """
        Every brain this project holds, where each one lives, and where each one publishes.

        Deliberately does **not** open any brain. A project of six subjects would otherwise pay six index
        rebuilds to answer "what is in here", and the answer is a configuration question. What it does read is
        each layout's own snapshot pointer, which is a file.

        Returns:
            dict[str, Any]: The project, its brains, and the account their repositories derive from.
        """
        from vitruvio.kernel import is_layout
        from vitruvio.runtime.registry import account_for

        # `project` is the whole file and `project.project` is the [project] section -- named apart here, because
        # `self.config.project.project.name` is a sentence nobody should have to parse.
        document = self.config.project
        identity = document.project
        account = None
        if not document.registry.namespace and not document.registry.reference:
            account = account_for()

        brains = []
        for name in sorted(document.brains):
            path = document.brain_path(name)
            spec = document.brains[name]
            brains.append(
                {
                    "name": name,
                    "path": str(path) if path else None,
                    "description": spec.description,
                    "exists": bool(path and is_layout(path)),
                    "repository": document.repository_for(name, account=account),
                    "explicit_reference": spec.reference,
                    "publish": spec.publish,
                    "selected": path == self.config.brain,
                }
            )

        return {
            "name": identity.name,
            "description": identity.description,
            "config_file": str(self.config.config_file) if self.config.config_file else None,
            "namespace": document.registry.namespace,
            "account": account,
            "tag": document.registry.tag,
            "brains": brains,
        }

    def add_brain(
        self,
        name: str,
        *,
        path: str | None = None,
        description: str | None = None,
        reference: str | None = None,
        create: bool = True,
        publish: bool = True,
    ) -> dict[str, Any]:
        """
        Register a brain in the project, creating its layout when it does not exist yet.

        Args:
            name (str): The brain's name. Becomes part of its derived repository, so it lives under OCI's
                naming rules -- ``analisis-ii`` rather than ``Análisis II``.
            path (str | None): Where the layout goes. Defaults to ``./brains/<name>`` beside the config.
            description (str | None): What it holds, for ``project show``.
            reference (str | None): An explicit repository, when the derived one is not wanted.
            create (bool): Create the layout if it is absent.
            publish (bool): Whether ``dist push`` may publish it. ``False`` for somebody else's upstream.

        Returns:
            dict[str, Any]: The registered brain.

        Raises:
            VitruvioError: If the project has no configuration file to write to, or the name is already taken.
        """
        from vitruvio.kernel import NamedBrainSpec, is_layout, update_config

        config_path = self.config.config_file
        if config_path is None:
            raise VitruvioError(
                "this project has no vitruvio.toml to add a brain to",
                hint="run `vitruvio project init <name>` first",
            )
        if name in self.config.project.brains:
            raise VitruvioError(
                f"this project already has a brain called {name!r}",
                hint="pick another name, or `vitruvio project remove` it first",
            )

        # Validated before anything is written, so a rejected name does not leave a half-registered project.
        spec = NamedBrainSpec(
            path=path or f"./brains/{name}", description=description, reference=reference, publish=publish
        )
        NamedBrainSpec.model_validate(spec.model_dump())
        from vitruvio.kernel import ProjectConfig

        ProjectConfig.model_validate({"brains": {name: spec.model_dump(exclude_none=True)}})

        target = (config_path.parent / spec.path).expanduser().resolve()
        created = False
        if create and not is_layout(target):
            from vitruvio.kernel import resolve as resolve_config

            sub = resolve_config(brain=target, config=config_path, require_layout=False)
            with translated():
                open_brain(sub, Capability.INSPECT, create=True)
            created = True

        update_config(config_path, f"brains.{name}.path", spec.path)
        if description:
            update_config(config_path, f"brains.{name}.description", description)
        if reference:
            update_config(config_path, f"brains.{name}.reference", reference)
        if not publish:
            update_config(config_path, f"brains.{name}.publish", False)

        return {
            "name": name,
            "project": self.config.project.project.name,
            "path": str(target),
            "created": created,
            "description": description,
            "publish": publish,
            "config_file": str(config_path),
        }

    def remove_brain(self, name: str) -> dict[str, Any]:
        """
        Unregister a brain from the project. The layout on disk is left alone.

        Never deletes data, and that is not timidity: a brain is content-addressed knowledge that may be the only
        copy, and "remove it from this project" and "destroy it" are different requests. The path is reported so
        the caller can act on the second one deliberately.

        Args:
            name (str): The brain's name.

        Returns:
            dict[str, Any]: What was unregistered, and where its layout still is.

        Raises:
            VitruvioError: If the project has no such brain.
        """
        from vitruvio.kernel import update_config

        config_path = self.config.config_file
        if config_path is None or name not in self.config.project.brains:
            raise VitruvioError(
                f"this project has no brain called {name!r}",
                hint=f"known: {', '.join(sorted(self.config.project.brains)) or '(none)'}",
            )

        path = self.config.project.brain_path(name)
        update_config(config_path, f"brains.{name}", None)
        return {"name": name, "path": str(path) if path else None, "config_file": str(config_path)}
