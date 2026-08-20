"""``BrainService`` -- one method per protocol operation, each returning JSON-able data.

This is the layer that makes the CLI thin, and the MCP server and HTTP API that follow it thin as well. Three
properties are load-bearing:

**Every method returns a plain dictionary.** Built by :mod:`vitruvio.runtime.wire`, which is the only place SDK
models become JSON. The CLI's ``--json``, an MCP tool result and an API response body are then the same bytes
by construction rather than by discipline.

**Every method declares its capability.** Opening a brain at ``INSPECT`` registers no index, which is what
keeps ``brain state`` from loading a model. See :mod:`vitruvio.runtime.assembly`.

**Every SDK exception is translated on the way out.** A caller gets a code, an exit status and a hint, from the
one table in :mod:`vitruvio.runtime.mapping`, rather than a raw ``BoltzmannError`` whose type it would have to
know how to interpret.

Nothing here decides how a result is displayed, and nothing here writes prose. The brain returns evidence; the
caller writes the answer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import suppress
from functools import cached_property
from inspect import signature
from pathlib import Path
from typing import Any

from boltzmann.blocks.memory_type import MemoryType
from boltzmann.brain import Brain
from boltzmann.query.request import Query

from vitruvio.kernel import ResolvedConfig, VitruvioError
from vitruvio.runtime import wire
from vitruvio.runtime.assembly import Capability, open_brain
from vitruvio.runtime.coerce import block_id as _block_id
from vitruvio.runtime.coerce import memory_type as _memory_type
from vitruvio.runtime.mapping import translate
from vitruvio.runtime.mapping import translated as _translated
from vitruvio.runtime.ops.embedders import EmbedderOps
from vitruvio.runtime.session import BrainSession


def _require_vector_index_ignore(method: Any) -> None:
    """Fail clearly when the CLI feature is used with an SDK that predates the supporting pull contract."""
    if "ignore_vector_indices" in signature(method).parameters:
        return
    raise VitruvioError(
        "the installed pyboltzmann does not support ignoring vector indices during a pull",
        hint="upgrade pyboltzmann to a release whose Brain.pull exposes `ignore_vector_indices`, then retry",
    )


class BrainService:
    """
    The protocol, as operations a caller can drive without knowing the SDK.

    Attributes:
        config (ResolvedConfig): Which brain, who as, under what policy.
    """

    def __init__(self, config: ResolvedConfig) -> None:
        """
        Build a service over a resolved configuration.

        No brain is opened here. Each operation opens at its own capability, so constructing a service is free
        and a read never pays for a write's machinery.

        Args:
            config (ResolvedConfig): The resolved configuration.
        """
        self.config = config
        self.session = BrainSession(config)

    def brain(self, capability: Capability = Capability.INSPECT) -> Brain:
        """
        The opened brain, memoized per capability.

        Args:
            capability (Capability): How much to stand up.

        Returns:
            Brain: The brain.
        """
        return self.session.brain(capability)

    # --- Lifecycle ------------------------------------------------------------

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

        with _translated():
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
        brain = self.brain(Capability.INSPECT)
        with _translated():
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
        brain = self.brain(Capability.INSPECT)
        with _translated():
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
        brain = self.brain(Capability.INSPECT)
        with _translated():
            snapshots = brain.history()
        chosen = snapshots[:limit] if limit else snapshots
        return {"snapshots": [wire.snapshot(item) for item in chosen], "retained": len(snapshots)}

    def info(self) -> dict[str, Any]:
        """
        Per-module shape: roots, block counts, and which indices are registered.

        Returns:
            dict[str, Any]: The brain's anatomy.
        """
        brain = self.brain(Capability.INSPECT)
        with _translated():
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
        Which modules have a vector index persisted, by reading the sidecar headers.

        No embedder is constructed and no model is loaded: the header carries the population and the model tag, which is
        everything needed to answer "would a publish include this".

        Returns:
            list[str]: Memory types with a non-empty vector index on disk.
        """
        from vitruvio.indices import format as envelope

        found: list[str] = []
        home = self.config.derived / "indices"
        for path in sorted(home.glob("*.vector.vidx")):
            try:
                read = envelope.read(path)
            except envelope.IndexFormatError:
                continue
            if read is not None and read[0].population:
                found.append(read[0].memory_type)
        return found

    # --- Inspection -----------------------------------------------------------

    def resolvability(self) -> dict[str, Any]:
        """
        Which blocks are readable, which are tombstoned, and which are simply absent.

        The three are different and must not be conflated: a redacted block is a verifiable member whose bytes
        were destroyed under policy, and a caller has to be able to tell that from corruption.

        Returns:
            dict[str, Any]: The report, with counts per module.
        """
        brain = self.brain(Capability.INSPECT)
        with _translated():
            return wire.resolvability(brain.resolvability())

    def resolve(self, block_id: str) -> dict[str, Any]:
        """
        Read one block by identity, verified by hash on the way out of the store.

        Args:
            block_id (str): A ``sha256:...`` block identity.

        Returns:
            dict[str, Any]: The block's identity, memory type and payload.
        """
        from boltzmann.identity.digest import BlockId

        brain = self.brain(Capability.INSPECT)
        with _translated():
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
        from boltzmann.identity.digest import BlockId

        brain = self.brain(Capability.INSPECT)
        with _translated():
            kind = _memory_type(memory_type)
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
        brain = self.brain(Capability.INSPECT)
        with _translated():
            module = brain.module(_memory_type(memory_type))
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
        brain = self.brain(Capability.INSPECT)
        with _translated():
            snapshot = brain.snapshot()
            return {
                "snapshot": str(snapshot.digest),
                "roots": {kind.value: str(snapshot.root_of(kind)) for kind in snapshot.installed},
            }

    # --- Browsing -------------------------------------------------------------
    #
    # Reading a brain rather than querying it. `search` ranks and `explain` justifies; these three answer "what
    # is in here", which the planner has no opinion about and which no index is consulted for. They exist in the
    # service rather than in the TUI because the same three questions are what an MCP `brain/list` tool and an
    # HTTP `GET /module/{kind}` will ask, and one answer shared is one answer to keep correct.

    def blocks(
        self,
        memory_type: str,
        *,
        limit: int = 100,
        offset: int = 0,
        contains: str | None = None,
    ) -> dict[str, Any]:
        """
        One module's blocks, as rows, in the module's own order.

        Resolution is per block and failure is per block: a version that names a block whose bytes are gone --
        tombstoned under an erasure policy, or never installed by a selective pull -- still lists it, marked
        unreadable. Dropping those rows would make a redacted brain look like a smaller one.

        ``contains`` filters the rows after they are read. That is a filter and not a query: it names no index,
        cannot rank, and is bounded by ``limit`` the same way an unfiltered page is. Text retrieval is
        :meth:`search`, where there is a cost model behind the choice.

        Args:
            memory_type (str): Which module.
            limit (int): How many rows to return.
            offset (int): How many matching rows to skip.
            contains (str | None): Case-insensitive substring the row must contain.

        Returns:
            dict[str, Any]: The module's shape, the rows, and whether more remain.
        """
        from vitruvio.runtime import browse

        brain = self.brain(Capability.INSPECT)
        with _translated():
            kind = _memory_type(memory_type)
            if kind not in brain.snapshot().installed:
                # Not an error. A module absent from a selectively pulled brain is a permanent, legitimate state,
                # and browsing one has to answer "nothing is here" rather than fail -- an interface that raised
                # would make a partial install look like a broken brain, which is the one confusion the protocol
                # is explicit about avoiding.
                return {
                    "memory_type": kind.value,
                    "root": None,
                    "block_count": 0,
                    "matched": 0,
                    "offset": offset,
                    "limit": limit,
                    "rows": [],
                    "truncated": False,
                    "filter": contains,
                    "installed": False,
                }

            module = brain.module(kind)
            identities = module.block_ids
            resolvable = module.resolvable()
            # A canonical block is bytes plus a media type and holds no name -- deliberately, because its identity
            # must not depend on what anyone called the file. The name a reader recognises lives in the
            # registration record, so it is read from provenance and attached here rather than invented. One scan
            # of an append-only module, and only for the one memory type that needs it.
            origins = self._origins(brain) if kind is MemoryType.CANONICAL else {}

            rows: list[dict[str, Any]] = []
            seen = 0
            for identity in identities:
                if resolvable.get(identity, True):
                    try:
                        entry = browse.row(module.get(identity), kind, origin=origins.get(str(identity)))
                    except Exception as error:  # the store disagreed with the composition; say so, do not stop
                        entry = browse.unreadable(str(identity), kind.value, f"{type(error).__name__}: {error}")
                else:
                    entry = browse.unreadable(str(identity), kind.value, "not resolvable (redacted or not installed)")
                if contains and not browse.matches(entry, contains):
                    continue
                seen += 1
                if seen > offset and len(rows) < limit:
                    rows.append(entry)

            return {
                "memory_type": kind.value,
                "root": str(module.root),
                "block_count": len(identities),
                "matched": seen,
                "offset": offset,
                "limit": limit,
                "rows": rows,
                "truncated": seen > offset + len(rows),
                "filter": contains,
                "installed": True,
            }

    @staticmethod
    def _origins(brain: Brain) -> dict[str, str]:
        """
        Where each canonical block came from, according to its registration record.

        Args:
            brain (Brain): The opened brain.

        Returns:
            dict[str, str]: Block identity to origin. Empty when provenance is not installed -- a selectively
            pulled brain can hold canonical evidence with no provenance beside it, and that is a brain whose
            blocks are shown by media type rather than one that fails to list.
        """
        found: dict[str, str] = {}
        with suppress(Exception):
            module = brain.module(MemoryType.PROVENANCE)
            resolvable = module.resolvable()
            for identity in module.block_ids:
                if not resolvable.get(identity, True):
                    continue
                record = module.get(identity).payload().get("record")
                if not isinstance(record, dict) or record.get("record_type") != "registration":
                    continue
                block, origin = record.get("block"), record.get("origin")
                if isinstance(block, str) and isinstance(origin, str) and origin:
                    found[block] = origin
        return found

    def content(self, digest: str) -> bytes:
        """
        The bytes a block names, verified against the digest on the way out of the store.

        The one method here that does not return a dictionary, and the reason is that a preview needs *bytes*: a
        PDF page to rasterize, an image to draw, a transcript to display. Base64 inside an envelope would be a
        different operation with a different cost, and a caller that wants that is calling
        :meth:`export_content` and reading the file.

        Content is not evidence. A canonical block names the original it describes and the normalized view of
        it, and both are addressed here by the digest the block carries -- so what comes back is what the block
        says it is, or nothing.

        Args:
            digest (str): A ``sha256:...`` content address, as carried by ``blob`` or ``normalized_view.blob``.

        Returns:
            bytes: The content.

        Raises:
            VitruvioError: If the digest is malformed, or the store cannot produce those bytes.
        """
        from boltzmann.identity.digest import OciDigest

        brain = self.brain(Capability.INSPECT)
        with _translated():
            return brain.store.get_bytes(OciDigest.parse(digest))

    def export_content(self, digest: str, destination: Path) -> dict[str, Any]:
        """
        Write the bytes a block names to a file.

        For everything a terminal cannot draw: a video to hand to a player, a spreadsheet to open, an original
        PDF to keep. The brain stays the authority -- this is a copy out, not a move, and nothing about the
        block changes.

        Args:
            digest (str): The content address.
            destination (Path): Where to write. A directory is written into, under the digest's hex.

        Returns:
            dict[str, Any]: The digest, the path written, and how many bytes it holds.
        """
        data = self.content(digest)
        target = destination
        if destination.is_dir():
            target = destination / digest.replace(":", "-")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"digest": digest, "path": str(target), "size": len(data)}

    def related(self, block_id: str, *, limit: int = 50) -> dict[str, Any]:
        """
        The provenance records that name a block: how it got here, and what was done to it since.

        This is the brain's own link graph rather than a similarity neighbourhood. Registration, derivation,
        normalization, supersession, demotion and removal all land in provenance as records naming a block, so
        reading them back is how a reader answers "where did this come from" without trusting a summary of it.

        The provenance module is scanned. There is no index from block to the records about it -- provenance is
        append-only and small relative to canonical evidence -- and a scan that is honest about its cost is
        better than an index that has to be kept true.

        Args:
            block_id (str): The block to look up.
            limit (int): How many records to return.

        Returns:
            dict[str, Any]: The records naming it, most recently written last, and how many were found. Empty
            when provenance is not installed, which is a brain whose history was not pulled rather than a
            failure to read one.
        """
        brain = self.brain(Capability.INSPECT)
        with _translated():
            kind = _memory_type("provenance")
            if kind not in brain.snapshot().installed:
                return {"block": block_id, "records": [], "count": 0, "truncated": False}
            module = brain.module(kind)
            resolvable = module.resolvable()
            found: list[dict[str, Any]] = []
            for identity in module.block_ids:
                if not resolvable.get(identity, True):
                    continue
                payload = module.get(identity).payload()
                record = payload.get("record")
                if not isinstance(record, dict) or block_id not in _mentions(record):
                    continue
                found.append({"block_id": str(identity), "record": record})
            return {
                "block": block_id,
                "records": found[:limit],
                "count": len(found),
                "truncated": len(found) > limit,
            }

    # --- Canonical registration ----------------------------------------------

    def register(
        self,
        path: Path,
        *,
        media_type: str,
        origin: str | None = None,
        license_id: str | None = None,
        retention_policy: str | None = None,
        normalize_with: str | None = None,
    ) -> dict[str, Any]:
        """
        Register a source as canonical evidence.

        Registering does not declare the source *true*. The canonical module asserts that evidence was
        incorporated and preserved; every interpretation of it is a separate, cited block.

        Args:
            path (Path): The file to read.
            media_type (str): What the bytes are.
            origin (str | None): Where it came from.
            license_id (str | None): Under what licence it is held.
            retention_policy (str | None): Under what retention policy.
            normalize_with (str | None): A normalization pipeline to produce a deterministic view.

        Returns:
            dict[str, Any]: The block's identity, whether it was a duplicate, and the new version.
        """
        from boltzmann.ingest.register import RegistrationRequest

        brain = self.brain(Capability.WRITE)
        with _translated():
            data = path.read_bytes()
            request = RegistrationRequest(
                media_type=media_type,
                actor=self.config.actor(),
                origin=origin or str(path),
                license=license_id,
                retention_policy=retention_policy,
                normalize_with=normalize_with,
            )
            return wire.registration(brain.register(data, request))

    def replace(
        self,
        path: Path,
        *,
        supersedes: str,
        media_type: str,
        origin: str | None = None,
        license_id: str | None = None,
        normalize_with: str | None = None,
    ) -> dict[str, Any]:
        """
        Register a newer edition of a source, and record that it supersedes the old one.

        There is no in-place edit of evidence: a new edition is a new block, and the precedence between them is
        a provenance edge rather than a field of either.

        Args:
            path (Path): The new file.
            supersedes (str): The block the new edition takes precedence over.
            media_type (str): What the bytes are.
            origin (str | None): Where it came from.
            license_id (str | None): Under what licence.
            normalize_with (str | None): A normalization pipeline.

        Returns:
            dict[str, Any]: The new block's identity and the version this produced.
        """
        from boltzmann.identity.digest import BlockId
        from boltzmann.ingest.register import RegistrationRequest

        brain = self.brain(Capability.WRITE)
        with _translated():
            request = RegistrationRequest(
                media_type=media_type,
                actor=self.config.actor(),
                origin=origin or str(path),
                license=license_id,
                normalize_with=normalize_with,
            )
            result = brain.replace(path.read_bytes(), request, BlockId.parse(supersedes))
            return {**wire.registration(result), "supersedes": supersedes}

    def put_content(self, path: Path, *, media_type: str) -> dict[str, Any]:
        """
        Store bytes addressably without registering a canonical block.

        For content a block will *reference* -- a normalized view produced elsewhere, an image a canonical
        block points at -- rather than content that is itself evidence.

        Args:
            path (Path): The file.
            media_type (str): What the bytes are.

        Returns:
            dict[str, Any]: The content reference.
        """
        brain = self.brain(Capability.WRITE)
        with _translated():
            reference = brain.put_content(path.read_bytes(), media_type)
            return reference.model_dump(mode="json")

    # --- The task lifecycle ---------------------------------------------------
    #
    # Five steps, and they are separate commands on purpose: define, schema, propose, validate, commit. An agent
    # driving this needs to see the schema before it writes candidates and needs to read the gate's verdict before it
    # commits, so collapsing them into one call would remove the two places where it can be corrected. `ingest run`
    # exists for when a proposer is available in-process and none of that is needed.

    @staticmethod
    def _parse_candidates(document: dict[str, Any]) -> Any:
        """
        Parse a candidate set, turning a shape failure into a rejection rather than an internal error.

        This exists because of what the exit code means to whatever is driving. A candidate document with a float
        where the protocol wants a decimal string is *the caller's document being wrong* -- exit 7, repair and come
        back -- and letting pydantic's exception escape reported it as exit 1, a bug in vitruvio. That is the one
        distinction the exit-code contract exists to make, and it was inverted.

        The issues are reported as ``field: problem`` lines rather than as pydantic's rendering, because an agent
        repairing a payload needs the path and the rule, not a link to a validation library's documentation.

        Args:
            document (dict[str, Any]): The ``boltzmann.candidates/v1`` document.

        Returns:
            Any: The parsed ``CandidateSet``.

        Raises:
            CandidatesRejectedError: If the document does not have the shape of a candidate set.
        """
        from boltzmann.ingest.proposer import CandidateSet
        from pydantic import ValidationError

        from vitruvio.kernel import CandidatesRejectedError

        try:
            return CandidateSet.model_validate(document)
        except ValidationError as error:
            issues = "; ".join(
                f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
            )
            raise CandidatesRejectedError(
                f"the candidate set does not match boltzmann.candidates/v1 -- {issues}",
                hint=(
                    'the two that catch everyone: `confidence` is a decimal *string* ("0.85", never 0.85, because '
                    "these documents get hashed and a float does not hash reproducibly), and `evidence` is never "
                    "empty. Run `vitruvio task schema --task task.json` for the exact shape"
                ),
            ) from error

    def define_task(
        self,
        source: str,
        *,
        allowed: Iterable[str] | None = None,
        requirements: Iterable[str] | None = None,
        instructions: str | None = None,
        task_id: str | None = None,
        replacing: str | None = None,
    ) -> dict[str, Any]:
        """
        Define what an external model is being asked to do with one canonical block.

        Args:
            source (str): The canonical block to interpret. Must be installed -- otherwise the model would be asked
                to interpret evidence the brain does not hold.
            allowed (Iterable[str] | None): Which memory types may be proposed. Canonical and provenance never can.
            requirements (Iterable[str] | None): Constraints the proposal must respect.
            instructions (str | None): Free-form guidance.
            task_id (str | None): The identifier the resulting provenance records cite, so a batch is nameable
                afterwards.
            replacing (str | None): A derived block this is re-deriving. Turns the task into a rederivation, which
                records the supersession rather than leaving two competing interpretations installed.

        Returns:
            dict[str, Any]: The task document.
        """
        # RETRIEVE, not WRITE: defining a task writes nothing. Requiring an actor here would mean a reader could not
        # even ask what a task over someone else's brain would look like.
        brain = self.brain(Capability.RETRIEVE)
        types = [_memory_type(item) for item in allowed] if allowed else None
        with _translated():
            if replacing is not None:
                value = brain.define_rederivation(_block_id(source), _block_id(replacing), allowed=types)
            else:
                value = brain.define_task(
                    _block_id(source),
                    allowed=types,
                    requirements=list(requirements) if requirements else None,
                    instructions=instructions,
                    task_id=task_id,
                )
            return wire.task(value)

    def task_schema(self, task: dict[str, Any]) -> dict[str, Any]:
        """
        The JSON Schema a proposal for this task must satisfy.

        Generated from the same block classes the gate validates against, and narrowed to the types this task
        allows -- so a proposal the gate would reject on shape is not even expressible.

        Args:
            task (dict[str, Any]): The task document, as :meth:`define_task` returned it.

        Returns:
            dict[str, Any]: A self-contained JSON Schema.
        """
        from boltzmann.ingest.task import ProcessingTask

        brain = self.brain(Capability.RETRIEVE)
        with _translated():
            return brain.candidates_schema(ProcessingTask.model_validate(task))

    def validate_candidates(self, candidates: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
        """
        Run the validation gate over a candidate set, without committing anything.

        The separation is the point: a model reads the per-candidate codes, repairs what it can and comes back. A
        gate that committed what passed and discarded what did not would throw away the half that is repairable.

        Args:
            candidates (dict[str, Any]): The ``boltzmann.candidates/v1`` document.
            task (dict[str, Any]): The task it answers.

        Returns:
            dict[str, Any]: The report, with a count per status and ``is_clean``.
        """
        from boltzmann.ingest.task import ProcessingTask

        # WRITE, even though nothing is written: the gate includes the retention policy's validators, and a report
        # produced without them would say a candidate is committable when the commit will refuse it.
        brain = self.brain(Capability.WRITE)
        parsed = self._parse_candidates(candidates)
        with _translated():
            return wire.validation(brain.validate(parsed, ProcessingTask.model_validate(task)))

    DUPLICATE = "duplicate"
    """The one rejection code that is not a defect in the proposal.

    It means the brain already holds that block, which is the *correct* outcome of re-submitting a set after
    repairing one member of it -- and refusing on it turned an idempotent retry into a permanent exit 7."""

    @classmethod
    def _triage(cls, report: Any) -> tuple[int, int]:
        """
        Split the non-committable candidates into "already held" and "worth fixing".

        Args:
            report (Any): The validation report.

        Returns:
            tuple[int, int]: Duplicates, then blocking rejections.
        """
        duplicates = blocking = 0
        for result in report.results:
            if result.is_committable:
                continue
            if result.issues and all(issue.code == cls.DUPLICATE for issue in result.issues):
                duplicates += 1
            else:
                blocking += 1
        return duplicates, blocking

    def commit_candidates(self, candidates: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
        """
        Validate and commit in one step, refusing if anything was rejected for a reason worth fixing.

        Stricter than the SDK's commit, with one exemption. Strict because a partial commit leaves the brain holding
        half an interpretation with no record that the other half was refused, and ``task validate`` already exists
        to find that out beforehand. The exemption is a **duplicate**: it says the brain already holds the block, so
        nothing is lost by proceeding, and refusing on it makes the repair-one-and-resubmit loop -- which is exactly
        how an agent is meant to work -- fail forever after the first partial success.

        Args:
            candidates (dict[str, Any]): The candidate set.
            task (dict[str, Any]): The task it answers.

        Returns:
            dict[str, Any]: What was committed, the report that authorised it, and how many were already held.

        Raises:
            CandidatesRejectedError: If any candidate was rejected for anything other than being a duplicate.
        """
        from boltzmann.ingest.task import ProcessingTask

        from vitruvio.kernel import CandidatesRejectedError

        brain = self.brain(Capability.WRITE)
        parsed = self._parse_candidates(candidates)
        with _translated():
            report = brain.validate(parsed, ProcessingTask.model_validate(task))
            payload = wire.validation(report)

            duplicates, blocking = self._triage(report)
            if blocking:
                raise CandidatesRejectedError(
                    f"{blocking} of {len(report.results)} candidates were rejected, so nothing was committed",
                    hint="run `vitruvio task validate` to see each code, repair the payloads, and commit again",
                )
            return {**wire.commit(brain.commit(report)), "validation": payload, "already_held": duplicates}

    def ingest_run(
        self,
        path: Path,
        *,
        media_type: str,
        proposer: str = "structure",
        allowed: Iterable[str] | None = None,
        normalize_with: str | None = None,
        subject: str | None = None,
        origin: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        The whole path in one call: register, define, propose, validate, commit.

        Args:
            path (Path): The file to ingest.
            media_type (str): What the bytes are. It is what pipeline dispatch and projection both read, so a
                Markdown file filed as ``application/octet-stream`` is a file nothing will normalise.
            proposer (str): Which proposer, optionally with a model after a colon.
            allowed (Iterable[str] | None): Which memory types may be proposed.
            normalize_with (str | None): A normalization pipeline. Defaults to whatever suits the media type.
            subject (str | None): A subject to tag proposals with, which is what makes a subject filter useful later.
            origin (str | None): Where the source came from.
            dry_run (bool): Propose and validate, commit nothing. What to run before letting a model write.

        Returns:
            dict[str, Any]: The registration, the task, the validation report and -- unless this was a dry run --
                the commit.
        """
        from boltzmann.ingest.register import RegistrationRequest
        from boltzmann.ingest.task import ProcessingTask

        from vitruvio.ingest import resolve as resolve_proposer
        from vitruvio.ingest import suggest
        from vitruvio.kernel import CandidatesRejectedError

        brain = self.brain(Capability.WRITE)
        engine = resolve_proposer(proposer, **({"subject": subject} if proposer.startswith("structure") else {}))
        types = [_memory_type(item) for item in allowed] if allowed else None
        pipeline = normalize_with if normalize_with is not None else suggest(media_type)

        with _translated():
            data = path.read_bytes()
            registration = brain.register(
                data,
                RegistrationRequest(
                    media_type=media_type,
                    actor=self.config.actor(),
                    origin=origin or str(path),
                    normalize_with=pipeline,
                ),
            )
            task = brain.define_task(registration.block_id, allowed=types)

            # The normalized view when there is one, and this is not a preference. The view is what the pipeline
            # produced *for reading*; handing a proposer the original PDF bytes would ask a text model to read a
            # binary container.
            source = data
            block = brain.module(_memory_type("canonical")).get(registration.block_id)
            view = getattr(block, "normalized_view", None)
            if view is not None:
                source = brain.store.get_bytes(view.blob)

            candidates = engine(task, source)
            report = brain.validate(candidates, ProcessingTask.model_validate(task.model_dump()))
            duplicates, blocking = self._triage(report)
            result: dict[str, Any] = {
                "registration": wire.registration(registration),
                "task": wire.task(task),
                "pipeline": pipeline,
                "proposer": proposer,
                "proposed": len(candidates),
                "validation": wire.validation(report),
                # Re-ingesting an unchanged document is the normal case, not a failure: the registration
                # short-circuits and every candidate comes back a duplicate. Counted so the caller can say
                # "nothing new" rather than reporting a commit of zero blocks as if something went wrong.
                "already_held": duplicates,
                "committed": None,
                "dry_run": dry_run,
            }
            if dry_run:
                return result
            if blocking:
                raise CandidatesRejectedError(
                    f"{blocking} of {len(report.results)} candidates were rejected, so nothing was committed",
                    hint="re-run with --dry-run to read each code, or use `task define`/`validate` to repair by hand",
                )
            result["committed"] = wire.commit(brain.commit(report))
            return result

    def pipelines(self) -> dict[str, Any]:
        """
        Every normalization pipeline this build can run.

        Reported rather than assumed because one of them is behind an extra: ``pdf-text`` is listed as unavailable
        rather than absent, so "why did my PDF not get a view" has an answer that names the install.

        Returns:
            dict[str, Any]: The pipelines, and which are available.
        """
        from vitruvio.ingest import describe

        records = describe()
        return {"pipelines": records, "available": [item["name"] for item in records if item["available"]]}

    # --- Declared sources -----------------------------------------------------
    #
    # `pull` is `register` with the bytes fetched rather than handed over, and the interesting part is the three
    # things that stop it registering the same material twice -- or, in one case, registering material that was
    # deliberately destroyed.
    #
    # 1. **The tombstone guard.** `Brain.register` calls `store.put_bytes(data)` *before* its duplicate check, and
    #    `OciLayoutStore.has` returns True for a tombstoned digest while `tombstone()` unlinks the file. So
    #    re-fetching redacted bytes writes the destroyed bytes back onto disk and then quietly reports
    #    `duplicate=True`. A scheduled `source pull` is precisely the machine that would silently undo `retain
    #    redact` -- the command whose own docstring says it is for personal data, credentials and licensed
    #    material. Nothing else here may assume `register` is safe on these bytes.
    #
    # 2. **The origin index.** `origin` is projected as an identity key, so "have I acquired this?" is one
    #    hash-map probe. It runs *before* the fetch, which is what makes a repeated pull cheap rather than merely
    #    idempotent. A hit is compared against the declaration before it is trusted: changing a source's
    #    `media_type` or `normalize_with` must re-register, because both are part of a block's identity and a
    #    silent skip would make the correction do nothing at all.
    #
    # 3. **Content addressing**, which needs no code: identical bytes compute the same block identity and
    #    `register` returns `duplicate=True`. It is the backstop for every source that cannot produce a stable
    #    origin, at the cost of one wasted download.

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
                source = self._source(name, spec)
            except (VitruvioError, ValueError) as error:
                rows.append({**row, "available": False, "reason": str(error), "provenance": None})
                continue
            rows.append(
                {
                    **row,
                    "available": source.available,
                    "reason": source.unavailable_because(),
                    "provenance": self._kind_provenance(spec.kind),
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
        source = self._source(name, spec)

        with _translated():
            items = list(source.list())

        brain = self.brain(Capability.INSPECT if dry_run else Capability.WRITE)
        rows: list[dict[str, Any]] = []
        registered = 0
        for item in items:
            if limit is not None and registered >= limit:
                rows.append({**self._item_row(item), "outcome": "not-reached"})
                continue
            row = self._pull_one(brain, source, spec, item, dry_run=dry_run, refetch=refetch)
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

    # --- The parts of a pull --------------------------------------------------

    def _source(self, name: str, spec: Any) -> Any:
        """Construct one declared source, with its path resolved against the configuration file."""
        from vitruvio.ingest.sources import resolve_source

        return resolve_source(
            name,
            spec,
            root=self.config.source_root(name),
            cwd=self.config.config_file.parent if self.config.config_file else None,
        )

    def _kind_provenance(self, kind: str) -> str | None:
        """Where a kind came from -- built-in, a plugin file, an entry point -- for ``source status``."""
        from vitruvio.ingest.sources import kinds

        found = kinds().get(kind)
        return found.provenance if found else None

    @staticmethod
    def _item_row(item: Any) -> dict[str, Any]:
        """The reportable part of an item, before anything has been decided about it."""
        return {"id": item.id, "origin": item.origin, "title": item.title, "media_type": item.media_type}

    def _pull_one(
        self,
        brain: Brain,
        source: Any,
        spec: Any,
        item: Any,
        *,
        dry_run: bool,
        refetch: bool,
    ) -> dict[str, Any]:
        """
        Decide about one item, and register it if it is new.

        The order is the whole point: the cheap checks come first, so a repeated pull over a hundred unchanged
        files performs a hundred hash-map probes and no downloads.
        """
        from vitruvio.ingest.sources import FetchResult
        from vitruvio.kernel import SourceError, VitruvioError

        row = self._item_row(item)
        listed_media_type = spec.media_type or item.media_type
        media_type = listed_media_type or "application/octet-stream"

        if not refetch:
            held = self._registered_as(brain, item.origin)
            if held is not None:
                if listed_media_type is None:
                    # A source whose cheap listing cannot name the type may discover it while fetching. A generic
                    # old registration is therefore not enough to skip: fetch once so it can be corrected. Once a
                    # specific type is held, origin dedup is cheap again on every later pull.
                    held_media_type = held.get("media_type")
                    matches = held_media_type not in (None, "application/octet-stream") and self._matches_declaration(
                        held, str(held_media_type), spec.normalize_with
                    )
                else:
                    matches = self._matches_declaration(held, media_type, spec.normalize_with)
                if matches:
                    return {
                        **row,
                        "media_type": held.get("media_type") or row["media_type"],
                        "outcome": "skipped",
                        "reason": "origin already registered",
                        "block": held["block"],
                    }

        if dry_run:
            return {**row, "outcome": "would-fetch"}

        try:
            fetched = source.fetch(item)
        except (SourceError, OSError) as error:
            # Per-item, and accumulated rather than fatal: one unreadable file in a folder of forty must not cost
            # the other thirty-nine their registration.
            return {**row, "outcome": "failed", "reason": str(error)}

        if isinstance(fetched, FetchResult):
            data = fetched.data
            media_type = spec.media_type or fetched.media_type or item.media_type or "application/octet-stream"
            row = {
                **row,
                "title": fetched.title or row["title"],
                "media_type": media_type,
            }
        else:
            data = fetched

        guarded = self._tombstoned(brain, data)
        if guarded is not None:
            return {**row, "outcome": "skipped", "reason": guarded}

        try:
            result = self._register_bytes(brain, data, media_type=media_type, origin=item.origin, spec=spec)
        except VitruvioError as error:
            return {**row, "outcome": "failed", "reason": str(error)}
        return {**row, **result}

    def _register_bytes(
        self,
        brain: Brain,
        data: bytes,
        *,
        media_type: str,
        origin: str,
        spec: Any,
    ) -> dict[str, Any]:
        """Register fetched bytes as canonical evidence, under the source's declared licence and pipeline."""
        from boltzmann.ingest.register import RegistrationRequest

        with _translated():
            registration = brain.register(
                data,
                RegistrationRequest(
                    media_type=media_type,
                    actor=self.config.actor(),
                    origin=origin,
                    license=spec.license,
                    normalize_with=spec.normalize_with,
                ),
            )
        return {
            "outcome": "duplicate" if registration.duplicate else "registered",
            "block": str(registration.block_id),
            "size": len(data),
        }

    @staticmethod
    def _tombstoned(brain: Brain, data: bytes) -> str | None:
        """
        Whether these bytes were redacted, checked *before* anything writes them back.

        The one check here that cannot be moved or merged into another. ``Brain.register`` stores the blob before it
        decides whether the block is a duplicate, and a tombstoned digest still answers ``has()`` with True while
        its file is gone -- so calling ``register`` with redacted bytes re-materialises exactly what a retention
        policy destroyed, and then reports a duplicate as though nothing had happened. A scheduled pull would undo
        every redaction, quietly, on a schedule.

        Returns:
            str | None: Why these bytes must not be registered, or ``None`` when it is safe.
        """
        from boltzmann.identity.digest import OciDigest

        digest = OciDigest.of(data)
        store = brain.store
        if store.has(digest) and not store.is_resolvable(digest):
            return (
                f"{digest} was redacted; re-registering would restore the bytes a retention policy destroyed. "
                f"Undo the redaction deliberately if that is what you want"
            )
        return None

    def _registered_as(self, brain: Brain, origin: str) -> dict[str, Any] | None:
        """
        What was registered from this origin before, or ``None``.

        One hash-map probe on the provenance module, which is the reason ``origin`` is projected at all. Falls back
        to ``None`` -- never to a scan -- when no such index is registered: a pull that silently became O(n) per
        item over a large brain would look like a hang, and content addressing still catches the duplicate.
        """
        from boltzmann.blocks.memory_type import MemoryType

        from vitruvio.indices import HashMapIndex, IdentityKey, IdQuery, fold

        index = next(
            (
                candidate
                for candidate in brain.indices.get(MemoryType.PROVENANCE, [])
                if isinstance(candidate, HashMapIndex)
            ),
            None,
        )
        if index is None or not index.population:
            return None

        results = index.lookup(IdQuery(keys=((IdentityKey.ORIGIN, fold(origin)),)))
        for identity in results.identities():
            record = self._registration_record(brain, identity)
            if record is not None:
                return record
        return None

    def _registration_record(self, brain: Brain, provenance_id: str) -> dict[str, Any] | None:
        """The canonical block one registration record talks about, with what it was registered as."""
        from boltzmann.identity.digest import BlockId

        try:
            with _translated():
                block = brain.resolve(BlockId.parse(provenance_id))
        except VitruvioError:
            # A record whose provenance block is no longer resolvable tells us nothing useful, and a pull is not
            # the place to raise about it.
            return None
        record: Any = getattr(block, "record", None)
        target = getattr(record, "block", None)
        if target is None:
            return None
        return {"block": str(target), **self._canonical_shape(brain, str(target))}

    def _canonical_shape(self, brain: Brain, block_id: str) -> dict[str, Any]:
        """
        The two parts of a canonical block's identity that a declaration can change: media type and view.

        Read so that editing a source's ``media_type`` or ``normalize_with`` re-registers instead of being skipped
        by the origin check. Both are inputs to the block's identity, so a silent skip would make the correction do
        nothing -- and the block that was meant to be fixed would still be wrong.

        ``normalized`` is whether a view exists, not which pipeline produced it. A view carries only its blob,
        media type and size; the pipeline's name lives in a separate ``NormalizationRecord`` keyed by the canonical
        block, and nothing indexes that key -- reading it would make this the per-item scan that
        :meth:`_registered_as` refuses to become. ``readable`` is kept apart from the two values because "no view"
        and "could not be read" are different facts, and only one of them is evidence about a declaration.
        """
        from boltzmann.identity.digest import BlockId

        try:
            with _translated():
                block = brain.resolve(BlockId.parse(block_id))
        except VitruvioError:  # pragma: no cover - a record pointing at an absent block
            return {"media_type": None, "normalized": None, "readable": False}
        return {
            "media_type": getattr(block, "media_type", None),
            "normalized": getattr(block, "normalized_view", None) is not None,
            "readable": True,
        }

    @staticmethod
    def _matches_declaration(held: dict[str, Any], media_type: str, normalize_with: str | None) -> bool:
        """
        Whether what is already registered is what the declaration now asks for.

        A ``False`` here is what turns "I fixed the media type in vitruvio.toml" into a new block rather than into
        nothing at all. Nothing is compared when the held block could not be read, because an unreadable block is
        not evidence that the declaration changed.

        Normalization is compared as presence, which decides the two cases that occur in practice: declaring
        ``normalize_with`` on a source whose blocks predate it, and removing it from one whose blocks have a view.
        Both converge -- the block registered next matches what is now declared. *Swapping* one pipeline name for
        another is not detectable here, because which pipeline produced an existing view is not knowable in O(1)
        (see :meth:`_canonical_shape`); that correction needs ``--refetch``.
        """
        if not held.get("readable", True):
            return True
        if held.get("media_type") is not None and held["media_type"] != media_type:
            return False
        view = held.get("normalized")
        return view is None or view == (normalize_with is not None)

    # --- Retention ------------------------------------------------------------
    #
    # Every operation here is *shaped* by one asymmetry: a drop is cheap to state and expensive to undo, and its cost
    # is not local -- excluding one block excludes everything derived from it. So `plan_drop` exists as its own
    # operation, and `drop` runs the same plan again rather than trusting one it was handed. Two calls, on purpose:
    # between the plan a caller saw and the drop it authorised, the composition may have moved.

    def plan_drop(
        self,
        blocks: Iterable[str],
        *,
        memory_type: str,
        reason: str = "requested",
        rederive_against: str | None = None,
    ) -> dict[str, Any]:
        """
        What a drop would take with it, without writing anything.

        Args:
            blocks (Iterable[str]): The blocks to exclude.
            memory_type (str): Which module they belong to.
            reason (str): Why. Recorded in provenance by the drop, and required by the protocol -- an unexplained
                removal is a removal nobody can audit.
            rederive_against (str | None): Newer evidence the dependents could be re-derived from instead of dropped.

        Returns:
            dict[str, Any]: The cascade, its size, and whether it needs review.
        """
        brain = self.brain(Capability.WRITE)
        with _translated():
            return wire.cascade(brain.plan_drop(self._drop_request(blocks, memory_type, reason, rederive_against)))

    def drop(
        self,
        blocks: Iterable[str],
        *,
        memory_type: str,
        reason: str = "requested",
        rederive_against: str | None = None,
    ) -> dict[str, Any]:
        """
        Exclude blocks from a module, cascading through provenance.

        The cascade is returned alongside the result, not because a caller needs it to interpret the outcome, but
        because it is the record of what this drop actually took -- and the plan a caller saw beforehand was computed
        against a composition that may since have moved.

        Args:
            blocks (Iterable[str]): The blocks to exclude.
            memory_type (str): Which module.
            reason (str): Why.
            rederive_against (str | None): Newer evidence to re-derive dependents from.

        Returns:
            dict[str, Any]: What was dropped, the new roots, and the cascade it followed.
        """
        brain = self.brain(Capability.WRITE)
        request = self._drop_request(blocks, memory_type, reason, rederive_against)
        with _translated():
            plan = wire.cascade(brain.plan_drop(request))
            return {**wire.dropped(brain.drop(request)), "cascade": plan}

    def drop_by_producer(
        self,
        producer_id: str,
        *,
        kind: str = "model",
        version: str | None = None,
        memory_types: Iterable[str] | None = None,
        reason: str = "producer invalidated",
    ) -> dict[str, Any]:
        """
        Drop everything one producer derived.

        The operation a bad model version needs. It works only because the producer was recorded at commit time,
        which is why a proposer names itself and why ``vitruvio`` records the producer rather than trusting the
        candidate set's claim about it.

        Args:
            producer_id (str): The model name, pipeline name or batch id.
            kind (str): ``model``, ``pipeline``, ``batch`` or ``actor``.
            version (str | None): A specific version, so one bad release can be dropped without the others.
            memory_types (Iterable[str] | None): Which modules to sweep. Defaults to every derived module.
            reason (str): Why.

        Returns:
            dict[str, Any]: What was dropped, and the new roots.
        """
        from boltzmann.blocks.provenance import Producer, ProducerKind
        from boltzmann.retention.requests import ProducerDropRequest

        brain = self.brain(Capability.WRITE)
        types = (
            [_memory_type(item) for item in memory_types]
            if memory_types
            else [MemoryType.SEMANTIC, MemoryType.PROCEDURAL, MemoryType.EPISODIC]
        )
        with _translated():
            request = ProducerDropRequest(
                producer=Producer(kind=ProducerKind(kind), id=producer_id, version=version),
                memory_types=types,
                actor=self.config.actor(),
                reason=reason,
                policy_name=self.config.project.policy.profile.value,
            )
            return wire.dropped(brain.drop_by_producer(request))

    def supersede(self, block: str, *, superseded: str, memory_type: str, reason: str | None = None) -> dict[str, Any]:
        """
        Record that one block takes precedence over another, without changing membership.

        The superseded block stays in the composition and keeps proving into the root; only accessibility changes.
        It is the *only* removal path the episodic module has, because episodic memory is append-only by protocol --
        what happened cannot stop having happened.

        Args:
            block (str): The block that takes precedence.
            superseded (str): The block it replaces.
            memory_type (str): Which module both belong to.
            reason (str | None): Why.

        Returns:
            dict[str, Any]: The new version and the record written.
        """
        brain = self.brain(Capability.WRITE)
        with _translated():
            result = brain.supersede(_block_id(block), _block_id(superseded), _memory_type(memory_type), reason=reason)
            return {**wire.supersession(result), "block": block, "superseded": superseded}

    def demote(self, block: str, *, memory_type: str, reason: str | None = None) -> dict[str, Any]:
        """
        Lower a block's retrieval priority without removing it.

        Recorded in the ledger rather than on the block: a block is immutable, so accessibility as a *field* would
        change the block id and make a demoted block a different block.

        Args:
            block (str): The block to demote.
            memory_type (str): Which module.
            reason (str | None): Why.

        Returns:
            dict[str, Any]: The new version and the record written.
        """
        brain = self.brain(Capability.WRITE)
        with _translated():
            return {
                **wire.supersession(brain.demote(_block_id(block), _memory_type(memory_type), reason=reason)),
                "block": block,
            }

    def prune(self, *, apply: bool = False) -> dict[str, Any]:
        """
        Reclaim blobs unreachable from every retained root.

        Pruning decides nothing about what to forget -- a drop already did that. It reclaims what no retained
        composition still needs, which is what makes it irreversible and yet harmless.

        Args:
            apply (bool): Actually delete. Defaults to reporting, matching the SDK, because the safe direction is the
                one you can repeat.

        Returns:
            dict[str, Any]: What would be, or was, reclaimed.
        """
        brain = self.brain(Capability.WRITE)
        with _translated():
            return {**wire.prune(brain.prune(dry_run=not apply)), "applied": apply}

    def redact(self, block: str, *, memory_type: str, reason: str) -> dict[str, Any]:
        """
        Destroy a block's bytes while a retained root still names it.

        Not the cleanup path. Wrong or obsolete knowledge is *dropped*; redaction is for personal data, credentials
        or licensed material that has to disappear even from retained history. It punches a hole in a composition
        that still names the block: membership still verifies, and reconstruction of that one block is forfeited --
        which ``inspect resolvability`` reports as tombstoned rather than missing, so a lawful erasure is never
        mistaken for a corrupt store.

        Args:
            block (str): The block to redact.
            memory_type (str): Which module.
            reason (str): Why. Not optional here, and the protocol agrees: an unexplained destruction of evidence is
                indistinguishable from an attack on the record.

        Returns:
            dict[str, Any]: What was destroyed, and what was held back because another block still names it.
        """
        brain = self.brain(Capability.WRITE)
        with _translated():
            return {
                **wire.redaction(brain.redact(_block_id(block), _memory_type(memory_type), reason)),
                "block": block,
            }

    def policy(self) -> dict[str, Any]:
        """
        The retention policy in force, and what it permits.

        Returns:
            dict[str, Any]: The profile, the policy document, and which mechanisms it allows.
        """
        policy = self.config.policy()
        return {
            "profile": self.config.project.policy.profile.value,
            "policy": policy.model_dump(mode="json"),
            "config_file": str(self.config.config_file) if self.config.config_file else None,
        }

    def _drop_request(self, blocks: Iterable[str], memory_type: str, reason: str, rederive_against: str | None) -> Any:
        """
        Build a drop request. One place, so ``plan_drop`` and ``drop`` cannot disagree about what was asked.

        Args:
            blocks (Iterable[str]): The blocks.
            memory_type (str): Which module.
            reason (str): Why.
            rederive_against (str | None): Newer evidence.

        Returns:
            Any: The ``DropRequest``.
        """
        from boltzmann.retention.requests import DropRequest

        return DropRequest(
            blocks=[_block_id(item) for item in blocks],
            memory_type=_memory_type(memory_type),
            actor=self.config.actor(),
            reason=reason,
            policy_name=self.config.project.policy.profile.value,
            rederive_against=_block_id(rederive_against) if rederive_against else None,
        )

    # --- Indices --------------------------------------------------------------

    def _index_set(self) -> Any:
        """
        A detached index set, over the brain's derived directory.

        For reporting only. Anything that has to *vouch* for a travelling index must go through :meth:`_set_from`
        instead, because vouching only works on indices the brain has registered.
        """
        from vitruvio.indices import build_index_set

        return build_index_set(
            self.config.project.indices,
            home=self.config.derived / "indices",
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

        collected = IndexSet(self.config.derived / "indices")
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
        brain = self.brain(Capability.INSPECT)
        with _translated():
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
            "home": str(self.config.derived / "indices"),
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

        chosen = {_memory_type(item) for item in memory_types} if memory_types else None

        # Opened at RETRIEVE so the brain has the indices *registered*, and built through those rather than through a
        # separate set. That is not tidiness: vouching only works on indices the brain knows about, so building a
        # detached set left the vector index unvouched and every publish silently omitted it -- which is what running
        # the CLI showed.
        brain = self.brain(Capability.RETRIEVE)
        indices = self._set_from(brain)

        with _translated():
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
            "home": str(self.config.derived / "indices"),
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
        chosen = _memory_type(memory_type) if memory_type else None
        indices = self._index_set()
        brain = self.brain(Capability.INSPECT)
        with _translated():
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
        brain = self.brain(Capability.INSPECT)
        with _translated():
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
        home = self.config.derived / "indices"
        candidates = [path for path in sorted(home.glob("*.vidx")) if path.name.removesuffix(".vidx") not in keep]
        if apply:
            for path in candidates:
                path.unlink()
        return {"removed": [str(path) for path in candidates], "applied": apply}

    # --- Distribution ---------------------------------------------------------

    # --- Benchmarking ---------------------------------------------------------

    def bench(self, *, tier: int = 1000, seed: int = 1234, queries: int = 24, limit: int = 10) -> dict[str, Any]:
        """
        Generate a corpus with known answers, and measure four retrieval strategies over it.

        Runs against a **generated** brain rather than the configured one, and that is the point: recall can only be
        measured where the answers are known, and they are known here because the corpus was built from them. Pointing
        this at a real brain would produce latency numbers and no way to say whether the results were right.

        Args:
            tier (int): Corpus size, in blocks. Below a few hundred an exhaustive scan legitimately wins, so a small tier
                measures the scan rather than the indices -- which is why the default is above that.
            seed (int): Makes the corpus reproducible, so two runs are comparable.
            queries (int): How many judged queries.
            limit (int): Results per query.

        Returns:
            dict[str, Any]: One measurement per configuration, and the verdict on whether the planner earned its cost.
        """
        import tempfile
        from pathlib import Path

        from boltzmann.query.request import Query, QueryFilters, QueryHints, RetrievalMode

        from vitruvio.bench.corpus import generate
        from vitruvio.bench.harness import CONFIGURATIONS, compare, measure

        with tempfile.TemporaryDirectory(prefix="vitruvio-bench-") as workspace:
            root = Path(workspace) / "corpus"
            with _translated():
                corpus = generate(root, blocks=tier, seed=seed, queries=queries)

            # A service over the generated brain, sharing this project's embedder and index configuration -- so the
            # numbers describe *your* setup rather than a default one. Which is what makes the comparison actionable:
            # switching to Ollama and re-running is how you find out whether it helped.
            from vitruvio.kernel import resolve as resolve_config

            config = resolve_config(brain=root, config=self.config.config_file, actor_id="bench@vitruvio")
            service = BrainService(config)

            index_report = service.index_build()

            # A hint per configuration. `lexical` excludes the vector generator and `semantic` requires it, which is
            # what isolates each index -- and `auto` lets the cost model choose, which is the row under test.
            modes = {
                "scan": RetrievalMode.AUTO,
                "lexical": RetrievalMode.LEXICAL,
                "vector": RetrievalMode.SEMANTIC,
                "planner": RetrievalMode.AUTO,
            }

            def run(configuration: str, text: str) -> list[str]:
                """One query under one strategy, returning block identities in rank order."""
                brain = service.brain(Capability.RETRIEVE)
                query = Query(
                    text=text,
                    filters=QueryFilters(memory_types=[MemoryType.SEMANTIC]),
                    hints=QueryHints(limit=limit, mode=modes[configuration]),
                )
                if configuration == "scan":
                    from boltzmann.query.scan import scan

                    bundle = scan(query, brain.modules())
                else:
                    bundle = brain.search(query)
                return [str(match.block_id) for match in bundle.matches]

            measurements = [measure(corpus, run, name, limit=limit) for name in CONFIGURATIONS]

        return {
            "blocks": corpus.blocks,
            "queries": len(corpus.judgements),
            "seed": seed,
            "embedder": self.config.project.text_embedder.uri,
            "indices_built": index_report.get("written"),
            "measurements": [item.as_dict() for item in measurements],
            "verdict": compare(measurements),
        }

    # --- Embedders ------------------------------------------------------------

    @cached_property
    def embedder_ops(self) -> EmbedderOps:
        """The embedding operations."""
        return EmbedderOps(self.session)

    def embedders(self) -> dict[str, Any]:
        """Every embedding provider this build knows, whether it can run, and what is configured.

        See :meth:`vitruvio.runtime.ops.embedders.EmbedderOps.embedders`."""
        return self.embedder_ops.embedders()

    def test_embedder(self, *, which: str = "text", text: str | None = None) -> dict[str, Any]:
        """Actually embed something, and report what came back.

        See :meth:`vitruvio.runtime.ops.embedders.EmbedderOps.test_embedder`."""
        return self.embedder_ops.test_embedder(which=which, text=text)

    # --- The project ----------------------------------------------------------

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
            with _translated():
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

    def reference_for(self, given: str | None = None) -> str:
        """
        Which repository this brain publishes to or pulls from.

        Four layers, and the lookups get more expensive as they go, so each is tried only when the ones before it
        came up empty:

        1. what the command was given;
        2. this brain's own ``reference``, or one derived from ``[registry].namespace``;
        3. one derived from whichever registry account is logged in -- the case that makes
           ``registry login --from-docker`` once enough for a whole project;
        4. nothing, and an error that names all three ways to fix it.

        Args:
            given (str | None): An explicit reference from the command line.

        Returns:
            str: The repository, without a tag.

        Raises:
            VitruvioError: If no layer names one.
        """
        from vitruvio.runtime.distribution import require_reference

        if given:
            return given

        configured = self.config.repository()
        if configured is None:
            # Only now: this reads the keyring and possibly runs a credential helper, which is not something to
            # do on a command that already knew its destination.
            from vitruvio.runtime.registry import account_for

            configured = self.config.repository(account_for())
        return require_reference(configured, None)

    def _client(
        self,
        reference: str,
        *,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        allow_docker: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> tuple[Any, str, list[str]]:
        """
        A registry client, the effective reference, and anything worth warning about.

        ``local`` selects a filesystem registry of OCI layouts. Not a mock: it goes through the same
        ``resolve``/``pull_blob``/``push`` contract the SDK defines, so the travelling-index path can be exercised
        end to end with no network, no credentials and no rate limits -- which is the right thing to prove before
        pointing anything at Docker Hub.
        """
        from vitruvio.runtime.registry import build_client, credential_for, normalize_reference

        if local is not None:
            from vitruvio.runtime.distribution import local_registry

            # A local layout has no host, so the reference is used verbatim as a repository name under `local`.
            return local_registry(local), reference, []

        _, effective = normalize_reference(reference)
        credential = credential_for(
            reference, username=username, token=token, anonymous=anonymous, allow_docker=allow_docker
        )
        client, warnings = build_client(
            reference,
            credential,
            insecure=self.config.project.registry.insecure if insecure is None else insecure,
        )
        return client, effective, warnings

    def pack(self, *, tag: str | None = None, modules: Iterable[str] | None = None) -> dict[str, Any]:
        """
        Build the OCI artifact locally, without pushing.

        Vouches for the vector index first: without that, ``pack`` silently omits the one layer a consumer cannot
        rebuild. See :mod:`vitruvio.runtime.vouch`.

        Args:
            tag (str | None): The tag to file it under.
            modules (Iterable[str] | None): Publish only these modules.

        Returns:
            dict[str, Any]: The manifest, with the digest a registry would file it under.
        """
        from vitruvio.runtime.vouch import vouch_travelling

        chosen = [_memory_type(item) for item in modules] if modules else None
        brain = self.brain(Capability.WRITE)
        vouched = vouch_travelling(brain, chosen)

        with _translated():
            manifest = brain.pack(tag=tag or self.config.project.registry.tag, modules=chosen)
        return {**wire.manifest(manifest), "vouched": vouched}

    def registry_check(
        self,
        reference: str | None = None,
        *,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """
        Test a registry with an artifact shaped exactly like a brain.

        Answers the question that a first push otherwise answers the hard way: does this registry accept a custom
        ``config.mediaType``? Checked rather than assumed, because the manifest's shape is fixed by the protocol.

        Returns:
            dict[str, Any]: Per-check outcomes, and a hint naming the real alternatives when it fails.
        """
        import asyncio

        from vitruvio.runtime.distribution import preflight

        target = self.reference_for(reference)
        client, _, warnings = self._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )
        brain = self.brain(Capability.INSPECT)
        with _translated():
            result = asyncio.run(preflight(target, client, brain.store))
        return {**result, "warnings": warnings}

    def push(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
        force: bool = False,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """
        Publish the brain.

        The SDK's own guards apply: a push that would narrow the module set is refused, and a push that is not a
        fast-forward is refused -- the latter failing *closed* on any error that is not a 404, so a refusal that looks
        like an absence cannot disable the check.

        Returns:
            dict[str, Any]: The digest the registry filed the manifest under.

        Raises:
            PublishForbiddenError: If the brain declares ``publish = false``. Checked first, before the reference is
                resolved and before a credential is read, because a refusal that happens after a credential lookup
                has already told a keyring what you were about to do.
        """
        import asyncio

        from vitruvio.runtime.vouch import vouch_travelling

        self._require_publishable()
        target = self.reference_for(reference)
        chosen = [_memory_type(item) for item in modules] if modules else None
        client, effective, warnings = self._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )

        brain = self.brain(Capability.WRITE)
        vouched = vouch_travelling(brain, chosen)
        with _translated():
            digest = asyncio.run(
                brain.push(
                    client,
                    reference=effective,
                    tag=tag or self.config.project.registry.tag,
                    force=force,
                    modules=chosen,
                )
            )
        return {
            "reference": target,
            "effective": effective,
            "tag": tag or self.config.project.registry.tag,
            "digest": str(digest),
            "vouched": vouched,
            "warnings": warnings,
        }

    def _require_publishable(self) -> None:
        """
        Refuse a push the project declared off-limits.

        The mistake this prevents is one command long and made by someone who does not expect to make it. A pulled
        brain is a working copy like any other -- nothing in the protocol distinguishes a brain you authored from one
        you installed -- so a stray ``dist push`` publishes a fork of somebody else's brain under whichever
        repository this project derives, and the two lineages diverge with nobody informed.

        Raises:
            PublishForbiddenError: If the selected brain declares ``publish = false``.
        """
        from vitruvio.kernel import PublishForbiddenError

        if self.config.publish_allowed:
            return
        name = self.config.brain_name or str(self.config.brain)
        raise PublishForbiddenError(
            f"brain {name!r} declares publish = false, so it is not published from here",
            hint=(
                "this is usually somebody else's upstream. If you really mean to publish a fork, set "
                f"publish = true under [brains.{name}] and give it its own `reference` first"
            ),
        )

    def plan_pull(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
        ignore_vector_indices: bool = False,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """
        Report what a pull would transfer, before transferring it.

        A canonical layer can be gigabytes, so "how much will this cost" has to be answerable without paying it.

        Reports ``local_work`` as well as the transfer, because cost is not the only thing worth knowing before a
        pull: an install adopts the remote composition, so anything committed here since the last pull stops being a
        member of it. Answered from the local head and nothing else, so it costs no extra round trip.

        Returns:
            dict[str, Any]: The plan, with the byte count taken from the resolved manifest.
        """
        import asyncio

        target = self.reference_for(reference)
        chosen = [_memory_type(item) for item in modules] if modules else None
        client, effective, warnings = self._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )
        wanted_tag = tag or self.config.project.registry.tag

        brain = self.brain(Capability.INSPECT)
        with _translated():
            manifest = asyncio.run(client.resolve(effective, wanted_tag))
            if ignore_vector_indices:
                _require_vector_index_ignore(brain.plan_pull)
                plan = asyncio.run(
                    brain.plan_pull(
                        client,
                        effective,
                        wanted_tag,
                        modules=chosen,
                        ignore_vector_indices=True,
                    )
                )
            else:
                # Keep the ordinary pull compatible with the previous SDK API. Only the new opt-in path requires
                # the SDK release that added `ignore_vector_indices`.
                plan = asyncio.run(brain.plan_pull(client, effective, wanted_tag, modules=chosen))
        return {
            "reference": target,
            "tag": wanted_tag,
            **wire.install_plan(plan, manifest),
            "local_work": self._local_work(brain),
            "warnings": warnings,
        }

    def pull(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
        ignore_vector_indices: bool = False,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """
        Install a published brain.

        Returns:
            dict[str, Any]: The snapshot now installed.
        """
        import asyncio

        target = self.reference_for(reference)
        chosen = [_memory_type(item) for item in modules] if modules else None
        client, effective, warnings = self._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )
        wanted_tag = tag or self.config.project.registry.tag

        brain = self.brain(Capability.WRITE)
        # Captured before, because after the pull the composition is the remote's and there is nothing left to
        # compare against. This is the only place the count can be exact rather than estimated.
        before = self._composition_ids(brain)
        ignored: list[str] = []
        with _translated():
            if ignore_vector_indices:
                _require_vector_index_ignore(brain.pull)
                manifest = asyncio.run(client.resolve(effective, wanted_tag))
                wanted = chosen if chosen is not None else manifest.modules
                ignored = [
                    memory_type.value for memory_type in wanted if manifest.vector_index_for(memory_type) is not None
                ]
                snapshot = asyncio.run(
                    brain.pull(
                        client,
                        effective,
                        wanted_tag,
                        modules=chosen,
                        ignore_vector_indices=True,
                    )
                )
            else:
                snapshot = asyncio.run(brain.pull(client, effective, wanted_tag, modules=chosen))
        orphaned = sorted(before - self._composition_ids(brain))
        # `plan_pull` may already have memoized an INSPECT-capability brain at the old head. A pull advances the
        # pointer through the WRITE-capability instance, so every other cached view must be reopened before a caller
        # asks for state or verification on this same service object.
        self.session.invalidate()
        if ignored:
            named = ", ".join(ignored)
            warnings.append(
                f"ignored published vector indices for {named}; run `vitruvio index build --force` to build "
                "compatible local vectors before relying on semantic retrieval"
            )
        return {
            "reference": target,
            "tag": wanted_tag,
            "snapshot": wire.snapshot(snapshot),
            "partial": chosen is not None,
            "discarded": len(orphaned),
            "discarded_blocks": orphaned[:20],
            "ignored_vector_indices": ignored,
            "warnings": warnings,
        }

    # --- What a pull would replace ---------------------------------------------
    #
    # `pull` adopts the remote snapshot verbatim and moves the head to it, with no fast-forward check -- the
    # divergence guard lives on `push`, where overwriting means overwriting somebody *else's* work. That asymmetry
    # is right: an install installs the other side's version.
    #
    # What was missing is that the loss was silent. Blocks committed locally since the last pull stop being members
    # of any composition: they do not verify into a root, they do not appear in a search, and a pack does not carry
    # them. The blobs stay on disk and the previous snapshot stays in `retained`, so the state is recoverable by
    # hand -- but nothing said it happened, and the discovery came days later when a search returned nothing.

    def _local_work(self, brain: Brain) -> dict[str, Any]:
        """
        What is installed here that no pull put here.

        Answered from ``Origin``, which records the snapshot digest of the last pull, so the question "did I commit
        anything since?" is a local comparison and costs no round trip. The count is a delta between two snapshot
        documents rather than a set difference, because a plan must not download a composition to answer it.

        Args:
            brain (Brain): The opened brain.

        Returns:
            dict[str, Any]: ``diverged``, how many blocks are at stake, and which snapshot holds them.
        """
        snapshot = brain.snapshot()
        installed = sum(reference.block_count for reference in snapshot.modules.values())
        origin = brain.origin
        clean = {"diverged": False, "blocks": 0, "snapshot": None, "pulled": None}

        if installed == 0:
            return clean
        if origin is None:
            # Never pulled, and it holds blocks: everything in it is local, and a pull replaces the lot.
            return {"diverged": True, "blocks": installed, "snapshot": str(snapshot.digest), "pulled": None}
        if str(snapshot.digest) == str(origin.snapshot):
            return clean

        baseline = self._snapshot_at(brain, str(origin.snapshot))
        blocks = None if baseline is None else max(installed - baseline, 0)
        return {
            "diverged": True,
            "blocks": blocks,
            "snapshot": str(snapshot.digest),
            "pulled": str(origin.snapshot),
        }

    @staticmethod
    def _snapshot_at(brain: Brain, digest: str) -> int | None:
        """
        How many blocks one retained snapshot held, or ``None`` when it can no longer be read.

        ``None`` rather than zero: a missing baseline means the size of the local work is *unknown*, and reporting
        an unknown as "nothing" is the failure this whole report exists to prevent.
        """
        from boltzmann.brain import Snapshot
        from boltzmann.identity.digest import OciDigest

        try:
            document = brain.store.get_bytes(OciDigest.parse(digest))
        # Broad on purpose: a pruned or unreadable blob is not an error here, it is an unknown.
        except Exception:
            return None
        try:
            return sum(reference.block_count for reference in Snapshot.model_validate_json(document).modules.values())
        except ValueError:  # pragma: no cover - a blob that is not a snapshot document
            return None

    @staticmethod
    def _composition_ids(brain: Brain) -> set[str]:
        """Every block identity currently a member of some installed module."""
        found: set[str] = set()
        for kind in brain.snapshot().installed:
            with suppress(Exception):
                found.update(str(identity) for identity in brain.module(kind).block_ids)
        return found

    def tags(
        self,
        reference: str | None = None,
        *,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """
        Which tags a repository holds.

        Returns:
            dict[str, Any]: The tags, or an explanation when the registry does not offer a listing.
        """
        target = self.reference_for(reference)
        client, effective, warnings = self._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )
        from boltzmann.exceptions import DistributionError, ReferenceNotFoundError

        lister = getattr(client, "tags", None)
        try:
            if lister is None:
                found = sorted(client.registry.get_tags(effective))
            else:
                found = sorted(lister(effective))
        except (DistributionError, ReferenceNotFoundError):
            # A repository with nothing published is the ordinary state before a first push, and "no tags" is the
            # answer -- not an error, and certainly not an internal one, which is what an unwrapped raise produced.
            found = []
        except Exception as error:
            raise translate(error) from error

        return {"reference": target, "tags": found, "warnings": warnings, "published": bool(found)}

    # --- Retrieval ------------------------------------------------------------

    def _build_query(
        self,
        text: str,
        *,
        memory_types: Iterable[str] | None,
        subject: str | None,
        since: str | None,
        until: str | None,
        tags: Iterable[str] | None,
        evidence: Iterable[str] | None,
        include_superseded: bool,
        mode: str | None,
        limit: int,
        expand_depth: int,
    ) -> Query:
        """
        Build the declarative query.

        One place, so ``search`` and ``explain`` cannot drift on what a filter means -- an explanation of a different
        query than the one that ran would be worse than no explanation.

        Returns:
            Query: The query. It names no index, by protocol: which to consult is the planner's decision.
        """
        from boltzmann.query.request import Query, QueryFilters, QueryHints, RetrievalMode

        with _translated():
            return Query(
                text=text,
                filters=QueryFilters(
                    memory_types=[_memory_type(item) for item in memory_types] if memory_types else None,
                    subject=subject,
                    since=since,
                    until=until,
                    tags=list(tags) if tags else None,
                    evidence=[_block_id(item) for item in evidence] if evidence else None,
                    include_superseded=include_superseded,
                ),
                hints=QueryHints(
                    mode=RetrievalMode(mode) if mode else self.config.project.planner.mode_default,
                    limit=limit,
                    expand_depth=expand_depth,
                ),
            )

    def search(
        self,
        text: str = "",
        *,
        memory_types: Iterable[str] | None = None,
        subject: str | None = None,
        since: str | None = None,
        until: str | None = None,
        tags: Iterable[str] | None = None,
        evidence: Iterable[str] | None = None,
        include_superseded: bool = False,
        mode: str | None = None,
        limit: int = 10,
        expand_depth: int = 0,
        diagnostics: bool = False,
    ) -> dict[str, Any]:
        """
        Retrieve evidence.

        The query names no index. Which indices to consult, and how to combine them, is the planner's decision
        -- that is the protocol's rule, not an implementation convenience.

        Args:
            text (str): What to look for.
            memory_types (Iterable[str] | None): Restrict to these modules. This is the filter that stops
                "what happened in May" from competing with "define a Fourier series".
            subject (str | None): Restrict to one subject.
            since (str | None): RFC3339 lower bound on ``occurred_at``.
            until (str | None): RFC3339 upper bound.
            tags (Iterable[str] | None): Require these tags.
            evidence (Iterable[str] | None): Require citation of these canonical blocks.
            include_superseded (bool): Include blocks a newer one has superseded.
            mode (str | None): A retrieval hint. It restricts the plans considered; it does not choose one.
            limit (int): How many matches to return.
            expand_depth (int): How far to expand along graph edges.
            diagnostics (bool): Include query-scoped visual data for a human interface. Ordinary API calls leave it
                off because projecting embeddings has a cost and is not part of an Evidence Bundle.

        Returns:
            dict[str, Any]: An Evidence Bundle. Blocks, provenance and scores -- never prose.
        """
        query = self._build_query(
            text,
            memory_types=memory_types,
            subject=subject,
            since=since,
            until=until,
            tags=tags,
            evidence=evidence,
            include_superseded=include_superseded,
            mode=mode,
            limit=limit,
            expand_depth=expand_depth,
        )
        brain = self.brain(Capability.RETRIEVE)
        with _translated():
            bundle = brain.search(query)
            payload = wire.evidence(bundle)

        planner = getattr(brain, "planner", None)
        explanation = getattr(planner, "last_explanation", None)
        if explanation is not None:
            payload["plan"] = {
                "signature": explanation.chosen.signature,
                "intent": explanation.intent.kind,
                "indices_consulted": {scope: list(kinds) for scope, kinds in explanation.indices_consulted.items()},
                "indices_available": {scope: list(kinds) for scope, kinds in explanation.indices_available.items()},
                "operators": [item.model_dump(mode="json") for item in explanation.chosen.operators],
                "est_cost_us": explanation.chosen.total_est_cost_us,
                "est_recall": explanation.chosen.est_recall,
                "degradations": [item.model_dump(mode="json") for item in explanation.degradations],
            }
            if diagnostics:
                from vitruvio.runtime.query_diagnostics import query_diagnostics

                visual = query_diagnostics(brain, text, list(payload.get("matches", [])), explanation)
                payload["diagnostics"] = visual
                # GraphExpand executes over a federated view, so its operator scope is not the complete set of graph
                # indices touched. The human plan view names the actual scopes from the diagnostic pass.
                for scope in visual["graph"]["scopes"]:
                    kinds = payload["plan"]["indices_consulted"].setdefault(scope, [])
                    if "graph" not in kinds:
                        kinds.append("graph")
                        kinds.sort()
        return payload

    def explain(
        self,
        text: str = "",
        *,
        memory_types: Iterable[str] | None = None,
        subject: str | None = None,
        since: str | None = None,
        until: str | None = None,
        tags: Iterable[str] | None = None,
        evidence: Iterable[str] | None = None,
        include_superseded: bool = False,
        mode: str | None = None,
        limit: int = 10,
        expand_depth: int = 0,
        analyze: bool = False,
    ) -> dict[str, Any]:
        """
        Report how a query would be answered, or was.

        Args:
            analyze (bool): Execute and record actuals, so the estimates can be checked against them. Without it,
                nothing runs and only the estimates are reported.

        Returns:
            dict[str, Any]: The full explanation: the chosen plan, the alternatives with their costs, each
            predicate's disposition, and which indices were available but not chosen.
        """
        query = self._build_query(
            text,
            memory_types=memory_types,
            subject=subject,
            since=since,
            until=until,
            tags=tags,
            evidence=evidence,
            include_superseded=include_superseded,
            mode=mode,
            limit=limit,
            expand_depth=expand_depth,
        )
        brain = self.brain(Capability.RETRIEVE)
        planner = getattr(brain, "planner", None)
        if planner is None or not hasattr(planner, "explain"):
            raise VitruvioError(
                "no cost-based planner is configured, so there is no plan to explain",
                hint="the SDK's linear scan has no plan; register indices with `vitruvio index build`",
            )

        with _translated():
            modules = brain.modules()
            if analyze:
                _, explanation = planner.analyze(query, modules)
            else:
                explanation = planner.explain(query, modules)
        payload: dict[str, Any] = explanation.model_dump(mode="json")
        return payload


def _mentions(record: dict[str, Any]) -> set[str]:
    """
    Every block identity a provenance record names, at any depth.

    Walked rather than read field by field because the six record types name blocks under six different keys --
    ``block``, ``derived_from``, ``supersedes``, ``removed`` -- and a new record type would otherwise be a
    record whose links silently stop appearing. What is collected is anything that looks like an identity, which
    is a decision to over-report rather than to miss an edge.

    Args:
        record (dict[str, Any]): The record's payload.

    Returns:
        set[str]: The identities it mentions.
    """
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            if value.startswith("sha256:"):
                found.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(record)
    return found
