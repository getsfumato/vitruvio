"""``vitruvio task`` -- the agent-as-proposer loop, one step per command.

This is the group an agent uses most, and its shape is the protocol's central rule made operational: *the model
proposes, the protocol governs what is stored*. Five steps, deliberately not collapsed into one:

1. `task define BLOCK` — what is being asked, over which evidence, and which memory types may be proposed.
2. `task schema --task task.json` — the JSON Schema the answer must satisfy. Hand it to a model as structured
   output; a proposal the gate would reject on shape then cannot even be expressed.
3. *the model writes candidates.json* — outside vitruvio, which is the point.
4. `task validate candidates.json --task task.json` — the gate's verdict, per candidate, with a code.
5. `task commit candidates.json --task task.json` — refused entirely if anything was rejected.

Steps 2 and 4 are why this is not one command. An agent needs the schema before it writes and needs the verdict
before it commits, and those are exactly the two places where it can be corrected instead of guessing.

Three rules that a proposal fails on more often than anything else, stated here because the error messages are
after the fact: `evidence` is never empty, no payload contains a float (these documents get hashed, and a float
does not hash reproducibly — `confidence` is a *string*), and a `locator` says where in the source the claim came
from.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter
from rich.text import Text

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.cli.render import short
from vitruvio.kernel import ExitCode, VitruvioError

VALIDATED = "validated"
"""The one verdict that earns a commit. Spelled out here rather than imported, because importing it would mean this
app imports the SDK -- which is the boundary the service layer exists to keep."""

PENDING_REVIEW = "pending_review"
"""Admissible, and not decidable by the protocol alone. Distinct from a rejection on purpose: a rejection is a
repair, and this is a question for a person."""

app = App(
    name="task",
    help="Define processing tasks, validate a model's candidates, and commit them.",
    result_action="return_value",
    exit_on_error=False,
)


def _read(path: Path, what: str) -> dict[str, Any]:
    """
    Read a JSON document, with an error that says which file and what was expected.

    Args:
        path (Path): The file.
        what (str): What it should hold, for the message.

    Returns:
        dict[str, Any]: The parsed document.

    Raises:
        VitruvioError: If it is missing, unreadable, or not a JSON object.
    """
    try:
        parsed = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise VitruvioError(f"{path} does not exist", hint=f"expected {what}") from error
    except json.JSONDecodeError as error:
        raise VitruvioError(f"{path} is not valid JSON: {error}", hint=f"expected {what}") from error
    if not isinstance(parsed, dict):
        raise VitruvioError(f"{path} holds {type(parsed).__name__}, not an object", hint=f"expected {what}")
    # An envelope from `--json` rather than the document itself is the single most common mistake here, and it is
    # trivially detectable, so it is detected rather than reported as a schema failure three layers down.
    if "vitruvio" in parsed and "data" in parsed:
        return dict(parsed["data"])
    return parsed


@app.command(name="define")
def define(
    source: str,
    *,
    allowed: Annotated[list[str] | None, Parameter(name=["--allowed", "-a"], negative=())] = None,
    require: Annotated[list[str] | None, Parameter(name=["--require"], negative=())] = None,
    instructions: str | None = None,
    task_id: Annotated[str | None, Parameter(name=["--task-id"])] = None,
    replacing: str | None = None,
) -> ExitCode:
    """Define what a model is being asked to do with one canonical block.

    Write the result to a file — every other command in this group takes it with `--task`. It is not decoration: the
    task pins which memory types may be proposed and which evidence must be cited, and the gate checks the
    candidates against *it*, not against whatever the model remembered.

    Parameters
    ----------
    source
        The canonical block to interpret. Must be installed: a task over evidence the brain does not hold would ask
        a model to interpret something nobody can audit.
    allowed
        Which memory types may be proposed: episodic, semantic, procedural. Repeatable. Canonical and provenance
        are never proposable — evidence is registered, and provenance is written by the protocol.
    require
        A constraint the proposal must respect. Repeatable. Defaults to "cite source ranges" and "do not invent".
    instructions
        Free-form guidance for the model, carried in the task.
    task_id
        The identifier the resulting provenance records cite. Worth setting: it is what makes "drop everything that
        batch derived" possible afterwards.
    replacing
        A derived block this re-derives. Records the supersession instead of leaving two competing interpretations
        installed, which is what you want when a better model revisits an old document.
    """
    console = current().console
    result = (
        current()
        .service()
        .define_task(
            source,
            allowed=allowed,
            requirements=require,
            instructions=instructions,
            task_id=task_id,
            replacing=replacing,
        )
    )
    view = render.stack(
        render.fields(
            [
                ("operation", result["operation"]),
                ("source", render.digest(result["source"])),
                ("allowed", ", ".join(result["allowed_memory_types"])),
                ("schema", result["output_schema"]),
            ]
        ),
        "",
        render.lines((f"- {item}" for item in result["requirements"]), style="muted"),
    )
    return console.emit("task.define", result, view=view)


@app.command(name="rederive")
def rederive(
    replacing: str,
    *,
    source: Annotated[str | None, Parameter(name=["--source", "-s"])] = None,
    allowed: Annotated[list[str] | None, Parameter(name=["--allowed", "-a"], negative=())] = None,
) -> ExitCode:
    """Define a task that re-derives one existing block from its evidence.

    The same thing as `task define --replacing`, named the way you would look for it. Re-derivation is the operation
    for "a better model should revisit this": it records the supersession rather than leaving two competing
    interpretations of the same evidence installed, and the old block stays auditable.

    Parameters
    ----------
    replacing
        The derived block to re-derive.
    source
        The canonical block to interpret. Defaults to the evidence the block being replaced cites.
    allowed
        Which memory types may be proposed. Repeatable.
    """
    console = current().console
    context = current()

    origin = source
    if origin is None:
        # The block's own evidence, so the ordinary case takes one argument. Re-deriving from *different* evidence is
        # not a re-derivation -- it is a new interpretation -- so a block citing several sources is asked about
        # rather than guessed at.
        block = context.service().resolve(replacing)
        evidence = block["payload"].get("evidence") or []
        if len(evidence) != 1:
            raise VitruvioError(
                f"{replacing} cites {len(evidence)} pieces of evidence, so which one to re-derive from is ambiguous",
                hint="pass --source explicitly",
            )
        origin = str(evidence[0])

    result = context.service().define_task(origin, allowed=allowed, replacing=replacing)
    view = render.stack(
        render.fields(
            [
                ("operation", result["operation"]),
                ("source", render.digest(result["source"])),
                ("replacing", render.digest(replacing)),
                ("allowed", ", ".join(result["allowed_memory_types"])),
            ]
        ),
        "",
        render.lines((f"- {item}" for item in result["requirements"]), style="muted"),
    )
    return console.emit("task.rederive", result, view=view)


@app.command(name="schema")
def schema(
    *,
    task: Annotated[Path, Parameter(name=["--task", "-t"])],
) -> ExitCode:
    """Print the JSON Schema a proposal for this task must satisfy.

    Generated from the same block classes the validation gate checks against, and narrowed to the memory types this
    task allows — so the schema and the gate cannot disagree. Hand it to a model as structured output.

    Parameters
    ----------
    task
        The task document from `task define`.
    """
    console = current().console
    document = _read(task, "a processing task from `vitruvio task define`")
    result = current().service().task_schema(document)
    # The schema *is* the result, so in human mode it goes to stdout as JSON too: there is no useful prose rendering
    # of a JSON Schema, and anything driving this wants to pipe it.
    return console.emit("task.schema", result, lines=[json.dumps(result, indent=2)])


@app.command(name="validate")
def validate(
    candidates: Path,
    *,
    task: Annotated[Path, Parameter(name=["--task", "-t"])],
) -> ExitCode:
    """Run the validation gate over a candidate set, committing nothing.

    Read the code on each rejection: a `REVIEW_*` code means stop and ask a human, and everything else is a repair
    to make and re-validate. Exit 0 here means "the gate would accept this", not "this was stored".

    Parameters
    ----------
    candidates
        The `boltzmann.candidates/v1` document the model produced.
    task
        The task it answers.
    """
    console = current().console
    result = (
        current()
        .service()
        .validate_candidates(
            _read(candidates, "a candidate set matching boltzmann.candidates/v1"),
            _read(task, "a processing task from `vitruvio task define`"),
        )
    )
    head = render.fields(
        [
            ("candidates", str(len(result["results"]))),
            ("committable", render.count(result["committable"])),
            ("clean", render.verdict(bool(result["is_clean"]))),
        ]
    )
    table = render.table("", "status", "memory", "candidate")
    for entry in result["results"]:
        # `validated` is the verdict that earns a commit -- not "accepted", which is nothing the protocol says.
        passed = entry["status"] == VALIDATED
        label = Text(str(entry["candidate"]["payload"].get("label") or entry["candidate"]["memory_type"]))
        for issue in entry.get("issues", []):
            # The issues sit under the candidate they belong to rather than in a column of their own: a rejection
            # is only actionable with its code, and a code beside a truncated detail is neither.
            label.append_text(Text(f"\n{issue['code']}: {issue['detail']}", style="warn"))
        table.add_row(
            render.verdict(passed, yes="ok", no="FAIL"),
            Text(entry["status"], style="ok" if passed else "warn"),
            render.kind(entry["candidate"]["memory_type"]),
            label,
        )
    if review := [entry for entry in result["results"] if entry["status"] == PENDING_REVIEW]:
        # Not a repair. The protocol is saying it cannot decide, which is a question for a person.
        console.warn(
            f"{len(review)} candidates are pending review: the protocol cannot decide these alone, so ask a human "
            f"rather than repairing and retrying"
        )
    if not result["is_clean"]:
        console.warn("nothing was committed; repair the payloads and re-validate before `task commit`")
    return console.emit("task.validate", result, view=render.stack(head, "", table))


@app.command(name="commit")
def commit(
    candidates: Path,
    *,
    task: Annotated[Path, Parameter(name=["--task", "-t"])],
) -> ExitCode:
    """Validate and commit a candidate set.

    All or nothing on a rejection, which is stricter than the SDK's commit: a partial commit leaves the brain
    holding half an interpretation with no record that the other half was refused. Exit 7 means the proposal was
    wrong — repair it, do not retry it unchanged.

    Parameters
    ----------
    candidates
        The candidate set.
    task
        The task it answers.
    """
    console = current().console
    result = (
        current()
        .service()
        .commit_candidates(
            _read(candidates, "a candidate set matching boltzmann.candidates/v1"),
            _read(task, "a processing task from `vitruvio task define`"),
        )
    )
    if result["already_held"]:
        # Not a failure. Re-submitting a set after repairing one member of it is how this is meant to be used.
        console.note(f"{result['already_held']} candidates were already held and were skipped")
    head = render.fields(
        [
            ("committed", Text.assemble((str(len(result["committed"])), "count"), " blocks")),
            ("provenance", f"{len(result['provenance'])} records"),
            ("snapshot", render.digest(result["snapshot"])),
        ]
    )
    roots = render.table("module", "root")
    for memory_type, root in sorted(result["roots"].items()):
        roots.add_row(render.kind(memory_type), render.digest(root))
    committed = render.lines((short(item) for item in result["committed"]), style="digest")
    return console.emit("task.commit", result, view=render.stack(head, "", roots, committed))
