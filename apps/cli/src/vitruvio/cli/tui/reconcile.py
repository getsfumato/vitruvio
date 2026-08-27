"""``vitruvio reconcile resolve`` -- decide what did not apply, with the evidence next to the decision.

The shape is two panes: the open questions on the left, and on the right what the gate found about the selected
one. That is the whole layout, and it is the layout because of what a decision here actually needs. A verdict on
its own is not enough to act on -- ``rejected`` because the evidence was deliberately dropped and ``rejected``
because it never arrived are the same verdict with opposite answers -- so the diagnosis has to be readable
without leaving the row.

Three decisions about this interface rather than about the command group.

**The screen resolves a reconciliation; it does not originate one.** It records decisions, accepts removals and
concludes, and it opens on "nothing in progress" when nothing is. Originating one takes a history to reconcile
against, and nothing persists which history was last fetched -- so that digest is typed once, into
``reconcile merge|rebase|squash``, which is also where the strategy is chosen. What the screen owns is the part
that is genuinely a loop.

Leaving one open matters, which is why ``q`` says what state it is leaving behind rather than just exiting: an
open reconciliation refuses every ordinary write on the brain, and a person who walked away from this screen has
no reason to connect the two.

**A decision that the protocol forbids is not offered.** ``admit`` is absent on a rejection -- not greyed, not
refused on press. A derived block whose evidence is not in the composition breaks R1 and nothing downstream
would catch it, because verification recomputes hashes and compositions rather than citations across modules.
The footer changes per row, and the reason is on screen.

**Every read happens on a worker thread.** Recomputing the plan judges every incoming block, which reads and
hashes each one. Doing that on the event loop froze the interface for as long as the store took, which reads as
a hang.
"""

from __future__ import annotations

from typing import Any

from rich.console import Group, RenderableType
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static

from vitruvio.cli import render
from vitruvio.runtime import BrainService
from vitruvio.runtime.reconcile_result import (
    ReconcileStatusResult,
    ReconcileVerdictResult,
    StatusView,
)

ADMISSIBLE = frozenset({"contradicted", "pending_review"})
"""The verdicts ``admit`` is available for.

A contradiction is information rather than a defect: holding two claims that disagree is a state the protocol
permits, and which one is right is not a question it answers. A pending review is one the protocol declined to
decide. A **rejection** is neither -- see the module docstring.
"""

PREFERABLE = frozenset({"pending_review"})
"""The verdicts ``prefer`` is available for: two histories replaced the same block with different successors."""


class QuestionTable(DataTable[Any]):
    """The open questions. ``escape`` is bound by the app, so there is no pane to get stuck in."""

    BINDINGS = [
        Binding("a", "app.admit", "admit", show=False),
        Binding("r", "app.reject", "reject", show=False),
        Binding("p", "app.prefer", "prefer", show=False),
    ]


class Resolver(App[None]):
    """
    The interactive resolver.

    Attributes:
        service (BrainService): The one way in, exactly as a command body has.
        strategy (str | None): The strategy to start under, from the brain's declaration. ``None`` means nobody
            declared one, and then this cannot start a reconciliation -- only resolve one already open.
    """

    CSS = """
    Screen { layers: base; }

    #questions { width: 3fr; min-width: 44; }
    #detail { width: 2fr; min-width: 36; border-left: solid $panel; }
    #verdict, #summary { padding: 0 1; }
    #summary { height: auto; border-bottom: solid $panel; }

    .empty { color: $text-muted; padding: 1; }
    """
    """Inline for the same packaging reason ``app.py`` gives: a stylesheet dropped from a wheel would ship an
    unstyled interface."""

    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("a", "admit", "admit"),
        Binding("r", "reject", "reject"),
        Binding("p", "prefer", "prefer"),
        Binding("k", "accept_removals", "accept removals"),
        Binding("c", "conclude", "conclude"),
        Binding("x", "abandon", "abandon"),
        Binding("f5", "reload", "reload", show=False),
        Binding("question_mark", "help_panel", "keys"),
    ]

    def __init__(self, service: BrainService, *, strategy: str | None = None) -> None:
        """
        Args:
            service (BrainService): The service layer.
            strategy (str | None): The declared strategy, for starting one that is not open yet.
        """
        super().__init__()
        self.service = service
        self.strategy = strategy
        self.status: ReconcileStatusResult | None = None
        self.questions: list[ReconcileVerdictResult] = []

    # --- Layout ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Lay out the two panes."""
        yield Header(show_clock=False)
        with Horizontal():
            yield QuestionTable(id="questions", cursor_type="row", zebra_stripes=True)
            with Vertical(id="detail"):
                yield Static(id="summary")
                with VerticalScroll():
                    yield Static(id="verdict")
        yield Footer()

    def on_mount(self) -> None:
        """Install the house theme, then load whatever state the brain is in."""
        # The same reason `app.py` does it: these panes render the CLI's own renderables, which name styles from
        # vitruvio's theme. Textual's console has never heard of them and raises on the first table it measures.
        self.console.push_theme(render.THEME)
        self.title = "vitruvio reconcile"
        self.query_one("#questions", DataTable).add_columns("block", "module", "verdict", "decided")
        self.reload()

    # --- Loading --------------------------------------------------------------

    @work(thread=True, exclusive=True, group="status")
    def reload(self) -> None:
        """
        Read where the reconciliation stands, and say so plainly when none is open.

        Reporting rather than starting: originating a reconciliation needs the other history's digest, which this
        screen is never told and nothing persists. So "nothing open" is an answer here, and it names the command
        that opens one.
        """
        ops = self.service.reconcile_ops
        try:
            status = ops.status()
            if not status["open"]:
                if self.strategy is None:
                    self.call_from_thread(self._nothing_to_do, "no reconciliation is open, and no strategy is declared")
                    return
                self.call_from_thread(self._nothing_to_do, "no reconciliation is open")
                return
        except Exception as error:  # a brain that cannot be read is a message, not a traceback
            self.call_from_thread(self.notify, str(error), severity="error", timeout=10)
            return
        self.call_from_thread(self._apply, status)

    def _nothing_to_do(self, why: str) -> None:
        """Say there is nothing open, and how to open one, rather than showing an empty table."""
        self.query_one("#questions", DataTable).clear()
        hint = (
            "`vitruvio reconcile merge|rebase|squash <THEIRS> --reason ...` starts one"
            if self.strategy is None
            else f"`vitruvio dist fetch` brings a history, and this brain reconciles it with {self.strategy}"
        )
        self.query_one("#summary", Static).update(Text(why, style="muted"))
        self.query_one("#verdict", Static).update(Text(hint, style="muted"))
        self.sub_title = "nothing open"

    def _apply(self, status: ReconcileStatusResult) -> None:
        """
        Fill both panes from a recomputed status.

        Args:
            status (ReconcileStatusResult): What ``ReconcileOps.status`` produced.
        """
        self.status = status
        viewed = StatusView(status)
        decisions = viewed.decisions
        self.questions = viewed.questions

        table = self.query_one("#questions", DataTable)
        table.clear()
        for entry in self.questions:
            made = decisions.get(entry["block"])
            table.add_row(
                render.digest(entry["block"]),
                render.kind(entry.get("memory_type")),
                Text(entry["status"], style="warn" if entry["status"] == "rejected" else ""),
                Text(made["kind"], style="ok") if made else Text("open", style="warn"),
            )

        state = viewed.state
        open_count = len(status["unresolved"])
        leaving = viewed.withdrawn_count
        pairs: list[tuple[str, Any]] = [
            ("theirs", render.digest(state["theirs"])),
            ("strategy", state["strategy"]),
            ("open", render.count(open_count)),
        ]
        if leaving:
            pairs.append(
                (
                    "leaving",
                    render.verdict(
                        status["removals_accepted"],
                        yes=f"{leaving} blocks, accepted",
                        no=f"{leaving} of yours -- press k",
                    ),
                )
            )
        self.query_one("#summary", Static).update(render.fields(pairs))
        self.sub_title = "ready to conclude" if status["is_resolved"] else f"{open_count} open"
        self._show_detail()

    # --- The selected question ------------------------------------------------

    @property
    def selected(self) -> ReconcileVerdictResult | None:
        """The question under the cursor, or ``None`` when the table is empty."""
        table = self.query_one("#questions", DataTable)
        if not self.questions or table.cursor_row is None:
            return None
        if not 0 <= table.cursor_row < len(self.questions):
            return None
        return self.questions[table.cursor_row]

    @on(DataTable.RowHighlighted, "#questions")
    def _on_move(self) -> None:
        """Redraw the detail pane, and the footer, for the row now under the cursor."""
        self._show_detail()

    def _show_detail(self) -> None:
        """
        Draw everything known about the selected question.

        The available decisions are part of it. Which ones apply depends on the verdict, so a footer that listed
        all three would advertise one the protocol refuses -- and the refusal would arrive as an error after the
        keypress rather than as an absence before it.
        """
        pane = self.query_one("#verdict", Static)
        entry = self.selected
        if entry is None:
            pane.update(Text("nothing selected", style="muted"))
            self.refresh_bindings()
            return

        body: list[RenderableType] = [
            render.fields(
                [
                    ("block", render.digest(entry["block"], full=True)),
                    ("module", render.kind(entry.get("memory_type"))),
                    ("verdict", entry["status"]),
                ]
            )
        ]

        if issues := entry.get("issues"):
            table = render.table("code", "detail")
            for issue in issues:
                table.add_row(issue.get("code", "-"), Text(str(issue.get("message", "")), style="muted"))
            body += ["", table]

        if missing := entry.get("missing_evidence"):
            # The pair that carries opposite advice, so it is spelled out rather than tabulated as a code. This
            # is the whole reason the SDK distinguishes them, and the advice is what a maintainer relays.
            table = render.table("evidence", "why", "what to tell them")
            for block, why in missing.items():
                advice = (
                    "do NOT resend -- this brain dropped it deliberately"
                    if why == "dropped_deliberately"
                    else "resend the contribution whole -- its source never arrived"
                )
                table.add_row(render.digest(block), why, Text(advice, style="warn"))
            body += ["", table]

        if conflicts := entry.get("conflicts_with"):
            body += ["", render.fields([("conflicts with", ", ".join(render.short(c) for c in conflicts))])]

        available = self._available(entry)
        if "admit" not in available and entry["status"] == "rejected":
            body += [
                "",
                Text(
                    "admit is not offered: this block cites evidence the reconciled composition does not hold, "
                    "which breaks R1, and no later check would catch it -- verification recomputes hashes and "
                    "compositions, not citations across modules. Supply the evidence in an ordinary commit.",
                    style="muted",
                ),
            ]
        body += ["", Text("available here: " + ", ".join(available), style="muted")]
        # `render.stack` returns a list, which is what a command body hands to `emit`. A `Static` takes one
        # renderable, so the list becomes a `Group` -- the same conversion `app.py` does at its own panes.
        pane.update(Group(*body))
        self.refresh_bindings()

    @staticmethod
    def _available(entry: ReconcileVerdictResult) -> list[str]:
        """Which decisions the protocol permits for one verdict. ``reject`` always: declining needs no reason."""
        options = ["reject"]
        if entry["status"] in ADMISSIBLE:
            options.insert(0, "admit")
        if entry["status"] in PREFERABLE and entry.get("conflicts_with"):
            options.append("prefer")
        return options

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """
        Hide a decision the selected verdict does not permit, so the footer never advertises one.

        Args:
            action (str): The action name.
            parameters (tuple[object, ...]): Its parameters, unused.

        Returns:
            bool | None: False to hide and disable, True to allow.
        """
        if action in {"admit", "reject", "prefer"}:
            entry = self.selected
            return entry is not None and action in self._available(entry)
        if action == "accept_removals":
            return (
                self.status is not None
                and bool(StatusView(self.status).withdrawn_count)
                and not self.status["removals_accepted"]
            )
        if action == "conclude":
            return self.status is not None and self.status["is_resolved"]
        return True

    # --- Decisions ------------------------------------------------------------

    def action_admit(self) -> None:
        """Let the selected block in anyway."""
        self._decide("admit")

    def action_reject(self) -> None:
        """Leave the selected block out. It stays in the store and in the history it came from."""
        self._decide("reject")

    def action_prefer(self) -> None:
        """Settle a precedence question by naming the other contender as the winner."""
        entry = self.selected
        if entry is None:
            return
        contenders = entry.get("conflicts_with") or []
        if len(contenders) != 1:
            # More than one and there is a real choice to make, which is a prompt this pane does not have. The
            # command takes the winner explicitly, and that is the honest place for an ambiguous decision.
            self.notify(
                f"{len(contenders)} competing successors -- name the winner with "
                f"`vitruvio reconcile resolve {render.short(entry['block'])} --prefer <BLOCK>`",
                severity="warning",
                timeout=12,
            )
            return
        self._decide("prefer", prefer=contenders[0])

    @work(thread=True, exclusive=True, group="decide")
    def _decide(self, kind: str, prefer: str | None = None) -> None:
        """
        Record one decision, then redraw from what the brain says rather than from what was asked.

        Args:
            kind (str): admit, reject or prefer.
            prefer (str | None): The winning successor, for a precedence decision.
        """
        entry = self.selected
        if entry is None:
            return
        try:
            status = self.service.reconcile_ops.resolve(entry["block"], kind=kind, prefer=prefer)
        except Exception as error:
            self.call_from_thread(self.notify, str(error), severity="error", timeout=10)
            return
        self.call_from_thread(self._apply, status)

    @work(thread=True, exclusive=True, group="decide")
    def action_accept_removals(self) -> None:
        """State that the work this reconciliation removes may go."""
        try:
            status = self.service.reconcile_ops.accept_removals()
        except Exception as error:
            self.call_from_thread(self.notify, str(error), severity="error", timeout=10)
            return
        self.call_from_thread(self._apply, status)

    @work(thread=True, exclusive=True, group="decide")
    def action_conclude(self) -> None:
        """Conclude the reconciliation, and leave -- there is nothing left for this screen to show."""
        try:
            result = self.service.reconcile_ops.continue_()
        except Exception as error:
            self.call_from_thread(self.notify, str(error), severity="error", timeout=10)
            return
        self.call_from_thread(self.exit, None)
        self.call_from_thread(
            print, f"reconciled: {result.get('strategy', '-')} -> {render.short(str(result.get('snapshot')))}"
        )

    @work(thread=True, exclusive=True, group="decide")
    def action_abandon(self) -> None:
        """Abandon the reconciliation. Nothing is undone; the decisions recorded so far are discarded."""
        try:
            self.service.reconcile_ops.abort()
        except Exception as error:
            self.call_from_thread(self.notify, str(error), severity="error", timeout=10)
            return
        self.call_from_thread(self.exit, None)

    def action_reload(self) -> None:
        """Recompute the plan and redraw."""
        self.reload()

    async def action_quit(self) -> None:
        """
        Leave, saying what is being left behind.

        ``async`` because Textual's own ``action_quit`` is, and overriding it with a synchronous method changes
        the signature the framework calls.

        An open reconciliation refuses every ordinary write on this brain, so walking away from one silently
        would strand somebody in a state whose cause is off-screen. Printed rather than a notification, because
        the point is for it to survive the interface closing.
        """
        if self.status is not None and not self.status["is_resolved"]:
            open_count = len(self.status["unresolved"])
            self.exit(None)
            print(
                f"left a reconciliation open with {open_count} question{'' if open_count == 1 else 's'} "
                "unanswered; ordinary writes stay refused until `vitruvio reconcile continue` or "
                "`vitruvio reconcile abort`"
            )
            return
        self.exit(None)


__all__ = ["Resolver"]
