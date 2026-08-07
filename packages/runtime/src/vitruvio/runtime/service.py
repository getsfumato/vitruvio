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

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from boltzmann.blocks.memory_type import MemoryType

from vitruvio.kernel import VitruvioError
from vitruvio.runtime import wire
from vitruvio.runtime.assembly import Capability, open_brain
from vitruvio.runtime.mapping import translate

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

    from boltzmann.brain import Brain
    from boltzmann.identity.digest import BlockId
    from boltzmann.query.request import Query

    from vitruvio.kernel import ResolvedConfig


@contextmanager
def _translated() -> Iterator[None]:
    """Re-raise anything the SDK throws as the error type every interface reports."""
    try:
        yield
    except VitruvioError:
        raise
    except Exception as error:
        raise translate(error) from error


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
        self._cache: dict[Capability, Brain] = {}

    def brain(self, capability: Capability = Capability.INSPECT) -> Brain:
        """
        The opened brain, memoized per capability.

        Args:
            capability (Capability): How much to stand up.

        Returns:
            Brain: The brain.
        """
        if capability not in self._cache:
            with _translated():
                self._cache[capability] = open_brain(self.config, capability)
        return self._cache[capability]

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

        from vitruvio.runtime.distribution import preflight, require_reference

        target = require_reference(self.config.project.registry.reference, reference)
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
        """
        import asyncio

        from vitruvio.runtime.distribution import require_reference
        from vitruvio.runtime.vouch import vouch_travelling

        target = require_reference(self.config.project.registry.reference, reference)
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

    def plan_pull(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """
        Report what a pull would transfer, before transferring it.

        A canonical layer can be gigabytes, so "how much will this cost" has to be answerable without paying it.

        Returns:
            dict[str, Any]: The plan, with the byte count taken from the resolved manifest.
        """
        import asyncio

        from vitruvio.runtime.distribution import require_reference

        target = require_reference(self.config.project.registry.reference, reference)
        chosen = [_memory_type(item) for item in modules] if modules else None
        client, effective, warnings = self._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )
        wanted_tag = tag or self.config.project.registry.tag

        brain = self.brain(Capability.INSPECT)
        with _translated():
            manifest = asyncio.run(client.resolve(effective, wanted_tag))
            plan = asyncio.run(brain.plan_pull(client, effective, wanted_tag, modules=chosen))
        return {
            "reference": target,
            "tag": wanted_tag,
            **wire.install_plan(plan, manifest),
            "warnings": warnings,
        }

    def pull(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
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

        from vitruvio.runtime.distribution import require_reference

        target = require_reference(self.config.project.registry.reference, reference)
        chosen = [_memory_type(item) for item in modules] if modules else None
        client, effective, warnings = self._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )
        wanted_tag = tag or self.config.project.registry.tag

        brain = self.brain(Capability.WRITE)
        with _translated():
            snapshot = asyncio.run(brain.pull(client, effective, wanted_tag, modules=chosen))
        return {
            "reference": target,
            "tag": wanted_tag,
            "snapshot": wire.snapshot(snapshot),
            "partial": chosen is not None,
            "warnings": warnings,
        }

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
        from vitruvio.runtime.distribution import require_reference

        target = require_reference(self.config.project.registry.reference, reference)
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
                "indices_consulted": explanation.indices_consulted,
                "est_cost_us": explanation.chosen.total_est_cost_us,
                "est_recall": explanation.chosen.est_recall,
                "degradations": [item.model_dump(mode="json") for item in explanation.degradations],
            }
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


def _memory_type(value: str) -> MemoryType:
    """
    Coerce a string into a memory type, listing the valid ones when it will not coerce.

    Args:
        value (str): The name.

    Returns:
        MemoryType: The coerced value.

    Raises:
        VitruvioError: If the string names no module.
    """
    try:
        return MemoryType(value)
    except ValueError as error:
        permitted = ", ".join(item.value for item in MemoryType)
        raise VitruvioError(f"{value!r} is not a memory type; expected one of: {permitted}") from error


def _block_id(value: str) -> BlockId:
    """Parse a block identity, reporting a malformed one as a usage error rather than a protocol failure."""
    from boltzmann.identity.digest import BlockId

    with _translated():
        return BlockId.parse(value)
