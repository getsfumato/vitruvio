"""``vitruvio retain`` -- the removal paths, and the discipline each one needs.

Removal is where a knowledge protocol earns or loses trust, so the paper gives it five distinct mechanisms rather
than one delete, and this group keeps them distinct instead of collapsing them into something friendlier.

| what you want | the mechanism | what it costs |
|---|---|---|
| this is wrong, take it out | `drop` | everything derived from it goes too |
| this is superseded by that | `supersede` | nothing; membership is unchanged, only accessibility |
| this is stale, rank it lower | `demote` | nothing; recorded in the ledger, not on the block |
| reclaim bytes nothing needs | `prune` | irreversible, and harmless: no retained root names them |
| this must not exist anywhere | `redact` | one block becomes unreconstructable, forever |

Two rules run through all of it.

**Plan before you drop.** `drop` runs `plan-drop` for you and refuses without `--yes`, because the cost of a drop is
not local: excluding one block excludes everything that cited it. `--yes` skips the *prompt*, never the plan.

**Episodic memory is append-only by protocol.** What happened cannot stop having happened, so `drop` will refuse it
and `supersede` is the path. That is not a limitation to work around; it is the property that makes an episodic
record worth reading.

`redact` is the one command here that should feel heavy. It destroys bytes a retained root still names, and it is for
personal data, credentials, or licensed material -- not for cleanup. Wrong knowledge gets dropped.
"""

from __future__ import annotations

import sys
from typing import Annotated

from cyclopts import App, Parameter
from rich.console import RenderableType
from rich.text import Text

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.cli.render import short
from vitruvio.kernel import ExitCode, UsageError, VitruvioError

app = App(
    name="retain",
    help="Drop, supersede, demote, prune and redact -- the five removal mechanisms.",
    result_action="return_value",
    exit_on_error=False,
)


def _cascade_lines(cascade: dict[str, object]) -> list[str]:
    """
    Render a cascade plan as lines.

    Lines rather than a table, and deliberately: `drop` prints this plan to **stderr**, one line at a time
    through ``console.note``, before it asks for confirmation -- so what the reader is about to consent to is
    beside the prompt rather than mixed into the result on stdout. A renderable cannot be noted.

    Args:
        cascade (dict[str, object]): The plan.

    Returns:
        list[str]: Lines, dependents grouped by module.
    """
    lines = [f"cascade     {cascade['size']} blocks in total"]
    dependents = cascade.get("dependents") or {}
    if isinstance(dependents, dict):
        for memory_type, blocks in sorted(dependents.items()):
            lines.append(f"  {memory_type:<12} {len(blocks)}")
            lines.extend(f"      {short(str(item))}" for item in blocks)
    if rederivable := cascade.get("rederivable"):
        count = len(rederivable) if isinstance(rederivable, list) else rederivable
        lines += [
            "",
            (
                f"rederivable {count} of them could be re-derived from newer evidence "
                f"instead of dropped (--rederive-against)"
            ),
        ]
    return lines


def _cascade(cascade: dict[str, object]) -> list[RenderableType]:
    """
    Render a cascade plan for stdout.

    Args:
        cascade (dict[str, object]): The plan.

    Returns:
        list[RenderableType]: The renderables, dependents grouped by module.
    """
    # The cascade size carries the decision: dropping one block that takes forty with it is not the operation the
    # reader thought they were asking for, and that number is the only place it says so.
    head = render.fields([("cascade", Text.assemble((str(cascade["size"]), "count"), " blocks in total"))])
    dependents = cascade.get("dependents") or {}
    table = None
    if isinstance(dependents, dict) and dependents:
        table = render.table("module", ("blocks", "right"), "identities")
        for memory_type, blocks in sorted(dependents.items()):
            table.add_row(
                render.kind(memory_type),
                str(len(blocks)),
                Text(", ".join(short(str(item)) for item in blocks), style="digest"),
            )
    advice = None
    if rederivable := cascade.get("rederivable"):
        count = len(rederivable) if isinstance(rederivable, list) else rederivable
        advice = Text(
            f"{count} of them could be re-derived from newer evidence instead of dropped (--rederive-against)",
            style="warn",
        )
    return render.stack(head, table, advice)


def _confirm(prompt: str, yes: bool) -> None:
    """
    Require a typed confirmation unless it was waived.

    Args:
        prompt (str): What to ask.
        yes (bool): Whether ``--yes`` was passed.

    Raises:
        VitruvioError: If the answer is not ``yes``.
    """
    if yes:
        return
    console = current().console

    # There is nobody to prompt. Refusing beats prompting into a pipe that will never answer, and beats assuming
    # consent from the absence of a terminal.
    #
    # Both arms are `UsageError` rather than a bare `VitruvioError`: a missing `--yes` is something the caller can
    # fix, and exit 1 is documented as "always a bug in vitruvio". Telling someone their invocation is our bug costs
    # them a real investigation, which is the reason UsageError exists.
    if console.json_mode:
        raise UsageError(
            "this operation needs confirmation and --json has no one to ask",
            hint="pass --yes once you have read the plan",
        )
    if not sys.stdin.isatty():
        # The check the comment above always described and the code did not do. Without it a scheduled `retain drop`
        # reached `input()`, took an EOFError, and main's last-resort handler reported a missing `--yes` as
        # "internal error: EOFError" followed by "this is a bug in vitruvio -- please report it".
        raise UsageError(
            "this operation needs confirmation and stdin is not a terminal",
            hint="pass --yes once you have read the plan",
        )

    try:
        answer = input(f"{prompt} [type yes to proceed] ")
    except EOFError:
        # Ctrl-D at the prompt is a person declining, which is the same outcome as typing anything but yes.
        raise VitruvioError("cancelled", hint="nothing was changed") from None
    if answer.strip().lower() != "yes":
        raise VitruvioError("cancelled", hint="nothing was changed")


@app.command(name="plan-drop")
def plan_drop(
    *blocks: str,
    memory_type: Annotated[str, Parameter(name=["--memory-type", "-t"])],
    rederive_against: Annotated[str | None, Parameter(name=["--rederive-against"])] = None,
) -> ExitCode:
    """Show what dropping these blocks would take with it.

    Always worth running first, and `drop` runs it anyway. The number that matters is the cascade size: a drop of one
    block that takes forty with it is a different decision from one that takes none.

    Parameters
    ----------
    blocks
        The blocks to exclude.
    memory_type
        Which module they belong to.
    rederive_against
        Newer evidence the dependents could be re-derived from instead of dropped. Reported, not applied.
    """
    console = current().console
    result = current().service().plan_drop(blocks, memory_type=memory_type, rederive_against=rederive_against)
    if result.get("requires_review"):
        console.warn(
            "this cascade exceeds the policy's review threshold, so `drop` will refuse it: a removal this wide is a "
            "decision for a person, not a command"
        )
    return console.emit("retain.plan-drop", result, view=_cascade(result))


@app.command(name="drop")
def drop(
    *blocks: str,
    memory_type: Annotated[str, Parameter(name=["--memory-type", "-t"])],
    reason: Annotated[str, Parameter(name=["--reason", "-r"])] = "requested",
    rederive_against: Annotated[str | None, Parameter(name=["--rederive-against"])] = None,
    yes: bool = False,
) -> ExitCode:
    """Exclude blocks from a module, cascading through provenance.

    The plan is printed first and confirmation is required: `--yes` skips the prompt, never the plan. Blocks are not
    mutated — what changes is the composition, so the module gets a new Merkle root and consumers of the old root are
    unaffected until they pull.

    Exit 6 means the retention policy refused (episodic memory is append-only; canonical drops need
    `canonical_drop_allowed`). Exit 10 means the cascade is wide enough that the policy wants a human.

    Parameters
    ----------
    blocks
        The blocks to exclude.
    memory_type
        Which module.
    reason
        Why. Recorded in provenance — an unexplained removal is one nobody can audit.
    rederive_against
        Newer evidence to re-derive dependents from rather than dropping them.
    yes
        Skip the prompt. The plan is still computed and still printed.
    """
    console = current().console
    service = current().service()

    plan = service.plan_drop(blocks, memory_type=memory_type, reason=reason, rederive_against=rederive_against)
    for line in _cascade_lines(plan):
        console.note(line)
    _confirm(f"drop {len(blocks)} blocks and {plan['size']} dependents from {memory_type}?", yes)

    result = service.drop(blocks, memory_type=memory_type, reason=reason, rederive_against=rederive_against)
    dropped = sum(len(items) for items in result["dropped"].values())
    head = render.fields(
        [
            ("dropped", Text.assemble((str(dropped), "count"), " blocks")),
            ("snapshot", render.digest(result["snapshot"])),
        ]
    )
    roots = render.table("module", "root")
    for memory, root in sorted(result["roots"].items()):
        roots.add_row(render.kind(memory), render.digest(root))
    console.note("the bytes are still on disk; `vitruvio retain prune` reclaims what no retained root needs")
    return console.emit("retain.drop", result, view=render.stack(head, "", roots))


@app.command(name="drop-producer")
def drop_producer(
    producer: str,
    *,
    kind: str = "model",
    # Not `--version`: cyclopts owns that at the app level, so a producer version passed there prints vitruvio's own
    # version and exits -- the drop silently never runs, and the output looks like a successful command.
    producer_version: Annotated[str | None, Parameter(name=["--producer-version"])] = None,
    memory_type: Annotated[list[str] | None, Parameter(name=["--memory-type", "-t"], negative=())] = None,
    reason: Annotated[str, Parameter(name=["--reason", "-r"])] = "producer invalidated",
    yes: bool = False,
) -> ExitCode:
    """Drop everything one producer derived.

    The operation a bad model version needs, and the reason a producer is recorded at commit time rather than
    inferred later. Naming a `--version` drops one release without touching the others.

    Parameters
    ----------
    producer
        The model name, pipeline name or batch id.
    kind
        model, pipeline, batch or actor.
    producer_version
        A specific version of that producer. Not spelled `--version`, which belongs to vitruvio itself.
    memory_type
        Which modules to sweep. Repeatable. Defaults to the derived ones.
    reason
        Why.
    yes
        Skip the prompt.
    """
    console = current().console
    label = f"{kind}:{producer}" + (f"@{producer_version}" if producer_version else " (every version)")
    _confirm(f"drop everything derived by {label}?", yes)

    result = (
        current()
        .service()
        .drop_by_producer(producer, kind=kind, version=producer_version, memory_types=memory_type, reason=reason)
    )
    dropped = sum(len(items) for items in result["dropped"].values())
    view = render.fields(
        [
            ("producer", label),
            ("dropped", Text.assemble((str(dropped), "count"), " blocks")),
            ("snapshot", render.digest(result["snapshot"])),
        ]
    )
    if dropped == 0:
        console.warn(
            f"nothing was derived by {label}. Check `kind` and the exact id: a producer is matched exactly, and a "
            f"typo looks identical to a clean brain"
        )
    return console.emit("retain.drop-producer", result, view=view)


@app.command(name="supersede")
def supersede(
    block: str,
    *,
    supersedes: str,
    memory_type: Annotated[str, Parameter(name=["--memory-type", "-t"])],
    reason: Annotated[str | None, Parameter(name=["--reason", "-r"])] = None,
) -> ExitCode:
    """Record that one block takes precedence over another.

    Membership does not change: the superseded block stays in the composition and keeps proving into the root, and
    only accessibility moves. This is the only removal path episodic memory has, because it is append-only by
    protocol — and it is usually the right path for the others too, since it keeps the record of what was believed.

    Parameters
    ----------
    block
        The block that takes precedence.
    supersedes
        The block it replaces.
    memory_type
        Which module both belong to.
    reason
        Why the earlier block was superseded.
    """
    console = current().console
    result = current().service().supersede(block, superseded=supersedes, memory_type=memory_type, reason=reason)
    view = render.stack(
        render.fields(
            [
                (
                    "supersedes",
                    Text.assemble(
                        (short(block), "digest"),
                        " now supersedes ",
                        (short(supersedes), "digest"),
                        f" in {memory_type}",
                    ),
                ),
                ("snapshot", render.digest(result["snapshot"])),
            ]
        ),
        "",
        render.empty("the superseded block is still a verifiable member; a search holds it back unless asked for it"),
    )
    return console.emit("retain.supersede", result, view=view)


@app.command(name="demote")
def demote(
    block: str,
    *,
    memory_type: Annotated[str, Parameter(name=["--memory-type", "-t"])],
    reason: Annotated[str | None, Parameter(name=["--reason", "-r"])] = None,
) -> ExitCode:
    """Lower a block's retrieval priority without removing it.

    Recorded in the ledger, not on the block: a block is immutable, so accessibility as a field would change its
    block id and make the demoted block a different block.

    Parameters
    ----------
    block
        The block to demote.
    memory_type
        Which module.
    reason
        Why.
    """
    console = current().console
    result = current().service().demote(block, memory_type=memory_type, reason=reason)
    view = render.fields(
        [
            ("demoted", Text.assemble((short(block), "digest"), f" in {memory_type}")),
            ("snapshot", render.digest(result["snapshot"])),
        ]
    )
    return console.emit("retain.demote", result, view=view)


@app.command(name="prune")
def prune(*, apply: bool = False) -> ExitCode:
    """Reclaim blobs unreachable from every retained root.

    Dry run by default, matching the SDK: the safe direction is the one you can repeat. Pruning decides nothing about
    what to forget — a drop already did — so it is irreversible and yet harmless: nothing a retained root names is
    touched.

    Parameters
    ----------
    apply
        Actually delete.
    """
    console = current().console
    result = current().service().prune(apply=apply)
    view = render.fields(
        [
            ("reclaimable", Text.assemble((str(len(result.get("reclaimed") or [])), "count"), " blobs")),
            ("bytes", str(result.get("bytes", 0))),
            ("applied", render.verdict(bool(result["applied"]), no="no (dry run)")),
        ]
    )
    if not result["applied"] and result.get("reclaimed"):
        console.note("re-run with --apply to delete them")
    return console.emit("retain.prune", result, view=view)


@app.command(name="redact")
def redact(
    block: str,
    *,
    memory_type: Annotated[str, Parameter(name=["--memory-type", "-t"])],
    reason: Annotated[str, Parameter(name=["--reason", "-r"])],
    yes: bool = False,
) -> ExitCode:
    """Destroy a block's bytes while a retained root still names it.

    **This is not the cleanup path.** Wrong or obsolete knowledge is dropped. Redaction is for personal data,
    credentials, or licensed material that must disappear even from retained history, and it is irreversible: that
    one block can never be reconstructed. `inspect resolvability` will report it as tombstoned rather than missing,
    so a lawful erasure is never mistaken for a corrupt store.

    Two limits worth stating out loud. A hash of low-entropy content is not anonymous, so confirming a guess may
    still be possible while the block id is retained. And erasure does not propagate to copies already pulled — a
    revocation can be published, but a distributed brain can only signal.

    Content another block still names survives, because bytes are addressed by their hash and destroying them would
    take the other block's evidence with it. The output says what was held back for that reason.

    Parameters
    ----------
    block
        The block to redact.
    memory_type
        Which module.
    reason
        Why. Required: an unexplained destruction of evidence is indistinguishable from an attack on the record.
    yes
        Skip the prompt. Consider not passing this.
    """
    console = current().console
    _confirm(
        f"permanently destroy the bytes of {short(block)}? This cannot be undone, and a human should have approved it",
        yes,
    )
    result = current().service().redact(block, memory_type=memory_type, reason=reason)
    destroyed = result.get("redacted") or []
    view = render.fields(
        [
            ("redacted", Text.assemble((short(block), "digest"), f" in {memory_type}")),
            # Red, because this is the one operation in the protocol that destroys something no root can recover.
            ("destroyed", Text(f"{len(destroyed)} blobs", style="bad")),
            ("snapshot", render.digest(result["snapshot"])),
        ]
    )
    if held := result.get("retained"):
        console.warn(
            f"{len(held)} blobs were kept because another block still names them; destroying them would take that "
            f"block's evidence with it, and nothing would report the loss"
        )
    return console.emit("retain.redact", result, view=view)


@app.command(name="policy")
def policy() -> ExitCode:
    """Show the retention policy in force, and what it permits.

    Worth reading before anything else in this group: the policy is what decides whether a drop is even expressible,
    and the profile in `vitruvio.toml` is committed with the project so every clone removes under the same rules.
    """
    console = current().console
    result = current().service().policy()
    document = result["policy"]
    view = render.stack(
        render.fields([("profile", result["profile"]), ("from", result["config_file"] or "(defaults)")]),
        "",
        render.fields(
            [
                ("droppable", ", ".join(document.get("droppable_modules") or []) or "(none)"),
                (
                    "canonical",
                    Text("droppable", style="warn")
                    if document.get("canonical_drop_allowed")
                    else Text("never dropped", style="ok"),
                ),
                ("review at", f"{document.get('cascade_review_threshold') or '(no threshold)'} cascaded blocks"),
                ("roots kept", str(document.get("retained_roots"))),
                ("mechanisms", ", ".join(document.get("allowed_mechanisms") or []) or "(all)"),
            ]
        ),
    )
    return console.emit("retain.policy", result, view=view)
