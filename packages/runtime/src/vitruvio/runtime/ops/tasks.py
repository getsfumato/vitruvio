"""The task lifecycle: define, propose, validate, commit -- and who is allowed to do which.

The boundary this enforces is the protocol's: a proposer suggests, the gate decides, and only what the gate passes
is committed. So `validate_candidates` and `commit_candidates` are separate operations rather than one call with a
flag, because an agent that could commit its own proposal is an agent whose proposals were never validated.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from vitruvio.kernel import ResolvedConfig
from vitruvio.runtime import wire
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.coerce import block_id
from vitruvio.runtime.coerce import memory_type as coerce_memory_type
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.session import BrainSession


class TaskOps:
    """The task lifecycle, as operations."""

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
        brain = self.session.brain(Capability.RETRIEVE)
        types = [coerce_memory_type(item) for item in allowed] if allowed else None
        with translated():
            if replacing is not None:
                value = brain.define_rederivation(block_id(source), block_id(replacing), allowed=types)
            else:
                value = brain.define_task(
                    block_id(source),
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

        brain = self.session.brain(Capability.RETRIEVE)
        with translated():
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
        parsed = self._parse_candidates(candidates)
        with self.session.write() as brain, translated():
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

        parsed = self._parse_candidates(candidates)
        with self.session.write() as brain, translated():
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

        engine = resolve_proposer(proposer, **({"subject": subject} if proposer.startswith("structure") else {}))
        types = [coerce_memory_type(item) for item in allowed] if allowed else None
        pipeline = normalize_with if normalize_with is not None else suggest(media_type)

        with self.session.write() as brain, translated():
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
            block = brain.module(coerce_memory_type("canonical")).get(registration.block_id)
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
