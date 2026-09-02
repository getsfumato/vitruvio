"""The browser's retrieval workspace and its modal for choosing what to look at.

The query workspace is the planner's answer, kept visibly separate from the filter box. The selection screen is
how the browser stops being about one brain: projects on the left, that project's brains on the right, and
what it returns is a pair -- a configuration file and a brain name -- because those two together are what
identifies a brain in vitruvio. Neither screen resolves anything itself; both hand a choice back and let the
browser act on it, so there is one place where a brain is opened and one place where a block is drawn.

The query workspace: the planner's answer, kept visibly separate from the filter box.

The filter in the browser's middle pane narrows rows that were already read. This screen runs
:meth:`~vitruvio.runtime.BrainService.search`, which is the cost-based planner choosing indices and returning a
verified Evidence Bundle. Results stay beside the physical plan and query-scoped views of the indices that actually
ran. They look similar and are not, so the difference is stated on the screen rather than left for a user to infer:
the score column here is agreement between retrieval strategies, and the footer says so, because a number in a
results table gets read as confidence unless something says otherwise.

Choosing a result dismisses the screen with a block identity, and the browser reveals it in its module. That is
the whole contract -- this screen never renders a block, so there is one place where a block is drawn.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Header, Input, Label, SelectionList, Static, TabbedContent, TabPane
from textual.widgets.selection_list import Selection

from vitruvio.cli import render
from vitruvio.cli.tui.query_views import btree_view, graph_view, plan_view, vector_view
from vitruvio.runtime import BrainService

LIMIT = 25
"""How many matches to ask the planner for."""


class ClassificationScreen(ModalScreen[list[str] | None]):
    """Choose additional existing catalog classes for one canonical source."""

    CSS = """
    ClassificationScreen { align: center middle; }
    #classify-panel { width: 72%; height: 76%; border: round $primary; background: $surface; padding: 1 2; }
    #classify-title { height: 2; text-style: bold; color: $primary; }
    #classify-note { height: 3; color: $text-muted; }
    #classes { height: 1fr; background: $background; }
    #classify-help { height: 2; color: $text-muted; }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "apply classes"),
        Binding("escape", "cancel", "cancel"),
    ]

    def __init__(self, source: dict[str, Any], classes: list[dict[str, Any]]) -> None:
        super().__init__()
        self.source = source
        self.class_options = classes

    def compose(self) -> ComposeResult:
        options = [
            Selection(
                f"{item['scheme']} / {item['label']}",
                item["reference"],
                bool(item.get("selected")),
                disabled=bool(item.get("disabled")),
            )
            for item in self.class_options
        ]
        with Vertical(id="classify-panel"):
            yield Label(str(self.source.get("title") or self.source.get("block_id")), id="classify-title")
            yield Static(
                "Space toggles a class. Existing placements are selected and locked because catalog history is "
                "append-only.",
                id="classify-note",
            )
            yield SelectionList(*options, id="classes")
            yield Static("ctrl+s validates and applies · escape leaves the brain unchanged", id="classify-help")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#classes", SelectionList).focus()

    def action_save(self) -> None:
        selected = list(self.query_one("#classes", SelectionList).selected)
        by_scheme: dict[str, list[str]] = {}
        for reference in selected:
            scheme = str(reference).partition("/")[0]
            by_scheme.setdefault(scheme, []).append(str(reference))
        exclusive = {item["scheme"] for item in self.class_options if item.get("exclusive")}
        conflicts = [scheme for scheme in exclusive if len(by_scheme.get(scheme, [])) > 1]
        if conflicts:
            self.notify(
                f"exclusive schemes accept one class: {', '.join(sorted(conflicts))}", severity="warning", timeout=8
            )
            return
        self.dismiss(sorted(str(value) for value in selected))

    def action_cancel(self) -> None:
        self.dismiss(None)


class SigningKeyScreen(ModalScreen[str | None]):
    """Choose which eligible ssh-agent key will sign a catalog commit."""

    CSS = """
    SigningKeyScreen { align: center middle; }
    #key-panel { width: 74%; height: 54%; border: round $primary; background: $surface; padding: 1 2; }
    #key-title { height: 2; text-style: bold; color: $primary; }
    #eligible-keys { height: 1fr; background: $background; }
    #key-help { height: 2; color: $text-muted; }
    """

    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, keys: list[dict[str, Any]]) -> None:
        super().__init__()
        self.keys = keys

    def compose(self) -> ComposeResult:
        with Vertical(id="key-panel"):
            yield Label("sign governed catalog change", id="key-title")
            yield DataTable(id="eligible-keys", cursor_type="row")
            yield Static("enter chooses a key · escape leaves the brain unchanged", id="key-help")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#eligible-keys", DataTable)
        table.add_columns("subject", "fingerprint", "scopes")
        for key in self.keys:
            table.add_row(
                str(key.get("subject") or "(no subject)"),
                render.digest(key.get("fingerprint")),
                ", ".join(key.get("scopes") or ()),
                key=str(key["fingerprint"]),
            )
        table.focus()
        if self.keys:
            table.move_cursor(row=0)

    @on(DataTable.RowSelected, "#eligible-keys")
    def _selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(str(event.row_key.value))

    def action_cancel(self) -> None:
        self.dismiss(None)


class SearchScreen(Screen[str | None]):
    """
    Ask the brain a question, and pick a block from what comes back.

    Attributes:
        service (BrainService): The service layer.
    """

    CSS = """
    SearchScreen { background: $background; }

    #query-workspace { height: 1fr; }
    #query-head { height: auto; padding: 1 2 0 2; }
    #query-title { width: 20; padding-top: 1; text-style: bold; }
    #query { width: 1fr; border: none; background: $boost; }
    #query-status { width: auto; min-width: 22; padding: 1 0 0 2; color: $text-muted; }
    #query-options { height: 3; padding: 0 2; }
    #query-since, #query-until { width: 1fr; border: none; background: $surface; }
    #query-depth { width: 24; border: none; background: $surface; }

    #query-main { height: 1fr; margin-top: 1; }
    #result-pane { width: 42%; min-width: 42; border-right: solid $panel; }
    #result-heading { height: 2; padding: 0 1; text-style: bold; }
    #results { height: 1fr; }
    #analysis { width: 1fr; min-width: 52; }
    .query-view { padding: 1 2; }
    #note { height: 2; padding: 0 2; color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "dismiss_screen", "close"),
    ]

    def __init__(self, service: BrainService) -> None:
        """
        Build the screen.

        Args:
            service (BrainService): The service layer.
        """
        super().__init__()
        self.service = service
        self.matches: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        """Lay out the persistent query workspace: results beside the plan and its visual evidence."""
        yield Header(show_clock=False)
        with Vertical(id="query-workspace"):
            with Horizontal(id="query-head"):
                yield Label("query the brain", id="query-title")
                yield Input(placeholder="natural language, terms, label, or sha256: identity", id="query")
                yield Static("ready", id="query-status")
            with Horizontal(id="query-options"):
                yield Input(placeholder="since · RFC3339 (optional)", id="query-since")
                yield Input(placeholder="until · RFC3339 (optional)", id="query-until")
                yield Input(placeholder="graph depth · 1 default", id="query-depth", type="integer")
            with Horizontal(id="query-main"):
                with Vertical(id="result-pane"):
                    yield Label("results", id="result-heading")
                    yield DataTable(id="results", cursor_type="row")
                with TabbedContent(id="analysis"):
                    with TabPane("plan", id="plan-tab"), VerticalScroll(classes="query-view"):
                        yield Static(plan_view(None), id="query-plan")
                    with TabPane("graph", id="graph-tab"), VerticalScroll(classes="query-view"):
                        yield Static(graph_view(None), id="query-graph")
                    with TabPane("vectors", id="vector-tab"), VerticalScroll(classes="query-view"):
                        yield Static(vector_view(None), id="query-vector")
                    with TabPane("B-tree", id="btree-tab"), VerticalScroll(classes="query-view"):
                        yield Static(btree_view(None), id="query-btree")
            yield Static(
                "score is retrieval agreement, not probability · enter opens a result · escape returns to browsing",
                id="note",
            )
        yield Footer()

    def on_mount(self) -> None:
        """Focus the query box and label the results table."""
        self.query_one("#results", DataTable).add_columns("score", "memory", "block", "identity")
        self.query_one("#query", Input).focus()

    @on(Input.Submitted, "#query")
    def _submitted(self, event: Input.Submitted) -> None:
        """Run the query the planner was given."""
        depth_value = self.query_one("#query-depth", Input).value.strip()
        self.search(
            event.value,
            since=self.query_one("#query-since", Input).value.strip() or None,
            until=self.query_one("#query-until", Input).value.strip() or None,
            expand_depth=max(0, int(depth_value or "1")),
        )

    @work(thread=True, exclusive=True, group="search")
    def search(
        self,
        text: str,
        *,
        since: str | None = None,
        until: str | None = None,
        expand_depth: int = 1,
    ) -> None:
        """
        Retrieve, off the event loop.

        Args:
            text (str): What to look for.
            since (str | None): Optional RFC3339 lower time bound.
            until (str | None): Optional RFC3339 upper time bound.
            expand_depth (int): Graph expansion depth.
        """
        query = text.strip()
        if not query and not (since or until):
            self.app.call_from_thread(self._failed, "write a query or provide a time window before running it")
            return
        self.app.call_from_thread(self._loading, query or "time window")
        try:
            result = self.service.search(
                query,
                since=since,
                until=until,
                expand_depth=expand_depth,
                limit=LIMIT,
                diagnostics=True,
            )
        except Exception as error:
            self.app.call_from_thread(self._failed, str(error))
            return
        self.app.call_from_thread(self._fill, result)

    def _loading(self, text: str) -> None:
        """Make the in-flight state explicit without hiding the previous result."""
        query = self.query_one("#query", Input)
        query.disabled = True
        self.query_one("#query-status", Static).update(f"planning {text[:28]}…")

    def _failed(self, message: str) -> None:
        """Return control to the query and name a recoverable failure."""
        query = self.query_one("#query", Input)
        query.disabled = False
        query.focus()
        self.query_one("#query-status", Static).update("query failed")
        self.app.notify(message, severity="error", timeout=10)

    def _fill(self, result: dict[str, Any]) -> None:
        """
        Show the bundle.

        Args:
            result (dict[str, Any]): What ``service.search`` produced.
        """
        self.matches = list(result.get("matches", []))
        plan = result.get("plan") or {}
        diagnostics = result.get("diagnostics") or {}
        self.query_one("#query-plan", Static).update(plan_view(plan))
        self.query_one("#query-graph", Static).update(graph_view(diagnostics.get("graph")))
        self.query_one("#query-vector", Static).update(vector_view(diagnostics.get("vector")))
        self.query_one("#query-btree", Static).update(btree_view(diagnostics.get("btree")))
        table = self.query_one("#results", DataTable)
        table.clear()
        for match in self.matches:
            payload = match.get("content") or {}
            identity = str(
                payload.get("label")
                or payload.get("summary")
                or payload.get("statement")
                or payload.get("media_type")
                or "(no identifying field)"
            )
            table.add_row(
                Text(str(match.get("score", "-")), style="score"),
                render.kind(match.get("memory_type")),
                render.digest(match.get("block_id")),
                Text(identity),
                key=match["block_id"],
            )
        query = self.query_one("#query", Input)
        query.disabled = False
        indices = sorted({kind for kinds in (plan.get("indices_consulted") or {}).values() for kind in kinds})
        selected = " + ".join(indices) if indices else "exhaustive scan"
        more = " · more may exist" if result.get("truncated") else ""
        self.query_one("#query-status", Static).update(f"{len(self.matches)} results{more} · {selected}")
        if not self.matches:
            query.focus()
            self.app.notify("the brain holds nothing matching -- that is an answer, not an error")
            return
        table.focus()
        table.move_cursor(row=0)

    @on(DataTable.RowSelected, "#results")
    def _chosen(self, event: DataTable.RowSelected) -> None:
        """Hand the chosen block back to the browser."""
        self.dismiss(str(event.row_key.value))

    def action_dismiss_screen(self) -> None:
        """Close without choosing."""
        self.dismiss(None)


class SelectionScreen(ModalScreen["tuple[Path, str | None] | None"]):
    """
    Choose which project, and which brain in it, the browser is about.

    The two panes are one decision rather than two: a brain name only means something inside a project, so
    moving the cursor in the left pane refills the right one, and only the right one can be chosen from. That
    is the same rule the CLI enforces -- ``--brain algebra`` is resolved *within* whatever ``--project``
    selected -- expressed as a layout instead of as an error message.

    Dismisses with the pair the browser needs, or ``None`` when nothing was chosen.

    Attributes:
        entries (list[dict[str, Any]]): The catalogue, as :func:`~vitruvio.cli.tui.selection.catalogue` built it.
    """

    CSS = """
    SelectionScreen { align: center middle; }

    #panel {
        width: 84%;
        height: 70%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #panes { height: 1fr; }
    #projects { width: 34%; border-right: solid $panel; }
    #brains { width: 1fr; }
    #hint { color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "dismiss_screen", "close"),
    ]

    def __init__(
        self,
        entries: list[dict[str, Any]],
        *,
        config_file: str | None = None,
        open_brain: str | None = None,
    ) -> None:
        """
        Build the screen over an already-read catalogue.

        Args:
            entries (list[dict[str, Any]]): The projects to offer.
            config_file (str | None): The project the session is currently in, so the cursor can start there
                rather than at whatever sorts first -- landing somewhere else would make the highlight
                disagree with the header for one keypress, and the highlight is what fills the brain pane.
            open_brain (str | None): The layout path of the brain already open, marked in both panes. "Which one
                am I on?" is half of what a reader opens this screen to find out, and a highlight cannot say it:
                the cursor moves as soon as they start looking around.
        """
        super().__init__()
        self.entries = entries
        self.config_file = config_file
        # Matched by path rather than by name, because a name is only unique inside a project and this screen
        # deliberately shows several projects at once.
        self.open_brain = str(Path(open_brain).resolve()) if open_brain else None

    def compose(self) -> ComposeResult:
        """Lay out the projects, their brains, and the sentence about what enter does."""
        with Vertical(id="panel"):
            yield Label("open a brain -- a project on the left, its brains on the right")
            with Horizontal(id="panes"):
                yield DataTable(id="projects", cursor_type="row")
                yield DataTable(id="brains", cursor_type="row")
            yield Static(
                "* is the brain you have open. tab moves between the panes, enter opens the highlighted brain, "
                "escape keeps the one you have",
                id="hint",
            )

    def on_mount(self) -> None:
        """Label both tables, fill the projects, and start on the project this session is in."""
        self.query_one("#projects", DataTable).add_columns("", "project", "brains", "")
        self.query_one("#brains", DataTable).add_columns("", "brain", "state", "description")

        table = self.query_one("#projects", DataTable)
        for index, entry in enumerate(self.entries):
            holds_open = any(self._is_open(brain) for brain in entry["brains"])
            table.add_row(
                Text("*", style="ok") if holds_open else "",
                Text(str(entry["label"]), style="value"),
                Text(str(len(entry["brains"])), style="count"),
                # A project found through a remembered brain rather than through the registry can be opened here
                # but *not* named on a command line, and that difference is worth seeing before you go looking
                # for it in a CLI refusal.
                Text("" if entry["registered"] else "unregistered", style="warn"),
                key=str(index),
            )
        if not self.entries:
            self.app.notify(
                "nothing to open: no project is registered, and this machine remembers no brain in one. "
                "`vitruvio project register --path <dir>` makes one openable",
                severity="warning",
                timeout=12,
            )
            return

        # Resolved on both sides. The catalogue reports resolved paths and this arrives however the command line
        # spelled it, so on a machine whose home or temporary directory is a symlink the two never matched and the
        # picker started on whichever project sorts first -- in a different project from the one on the header.
        here = Path(self.config_file).resolve() if self.config_file else None
        start = next(
            (index for index, entry in enumerate(self.entries) if Path(str(entry["config_file"])) == here),
            0,
        )
        table.move_cursor(row=start)
        table.focus()
        self._fill_brains(start)

    def _fill_brains(self, index: int) -> None:
        """
        Show one project's brains.

        Args:
            index (int): Which entry of the catalogue.
        """
        table = self.query_one("#brains", DataTable)
        table.clear()
        if not (0 <= index < len(self.entries)):
            return
        for position, brain in enumerate(self.entries[index]["brains"]):
            table.add_row(
                Text("*", style="ok") if self._is_open(brain) else "",
                Text(str(brain["name"] or "(this project's brain)"), style="value"),
                # A brain the project declares but nobody has created yet is listed, marked. Hiding it would
                # make `project add --no-create` look like it did nothing.
                render.verdict(bool(brain["present"]), yes="", no="not created"),
                Text(str(brain["description"] or ""), style="muted"),
                key=str(position),
            )

    def _is_open(self, brain: dict[str, Any]) -> bool:
        """
        Whether one catalogue brain is the one already on screen.

        Args:
            brain (dict[str, Any]): A brain entry.

        Returns:
            bool: Whether its layout is the open one.
        """
        if self.open_brain is None or not brain["path"]:
            return False
        return str(Path(str(brain["path"])).resolve()) == self.open_brain

    @on(DataTable.RowHighlighted, "#projects")
    def _project_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Refill the brain pane as the project cursor moves, rather than waiting to be told twice."""
        self._fill_brains(int(str(event.row_key.value or 0)))

    @on(DataTable.RowSelected, "#projects")
    def _project_chosen(self) -> None:
        """On enter in the left pane, move into the brains -- which is where the choice actually is."""
        self.query_one("#brains", DataTable).focus()

    @on(DataTable.RowSelected, "#brains")
    def _brain_chosen(self, event: DataTable.RowSelected) -> None:
        """Hand the project-and-brain pair back to the browser."""
        projects = self.query_one("#projects", DataTable)
        entry = self.entries[projects.cursor_row]
        brain = entry["brains"][int(str(event.row_key.value or 0))]
        if not brain["present"]:
            # Refused here rather than opened and reported as a failure: the layout does not exist, so there is
            # nothing to browse, and dismissing would throw away the pane the reader is standing in.
            self.app.notify(
                f"{brain['name'] or 'that brain'} has no layout yet -- `vitruvio project add` creates one",
                severity="warning",
            )
            return
        self.dismiss((Path(str(entry["config_file"])), brain["name"]))

    def action_dismiss_screen(self) -> None:
        """Close without choosing."""
        self.dismiss(None)


__all__ = ["LIMIT", "ClassificationScreen", "SearchScreen", "SelectionScreen", "SigningKeyScreen"]
