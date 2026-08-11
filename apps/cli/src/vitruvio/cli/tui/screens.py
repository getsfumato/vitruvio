"""The search screen: the planner's answer, kept visibly separate from the filter box.

The filter in the browser's middle pane narrows rows that were already read. This screen runs
:meth:`~vitruvio.runtime.BrainService.search`, which is the cost-based planner choosing indices and returning a
verified Evidence Bundle. They look similar and are not, so the difference is stated on the screen rather than
left for a user to infer: the score column here is agreement between retrieval strategies, and the footer says
so, because a number in a results table gets read as confidence unless something says otherwise.

Choosing a result dismisses the screen with a block identity, and the browser reveals it in its module. That is
the whole contract -- this screen never renders a block, so there is one place where a block is drawn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual import on, work
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Label, Static

from vitruvio.cli import render

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from vitruvio.runtime import BrainService

LIMIT = 25
"""How many matches to ask the planner for."""


class SearchScreen(ModalScreen[str | None]):
    """
    Ask the brain a question, and pick a block from what comes back.

    Attributes:
        service (BrainService): The service layer.
    """

    CSS = """
    SearchScreen { align: center middle; }

    #panel {
        width: 80%;
        height: 70%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #query { margin-bottom: 1; }
    #results { height: 1fr; }
    #note { color: $text-muted; }
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
        """Lay out the query box, the results, and the sentence about what a score is."""
        with Vertical(id="panel"):
            yield Label("search the brain -- the planner chooses the indices")
            yield Input(placeholder="natural language, terms, or a sha256: identity", id="query")
            yield DataTable(id="results", cursor_type="row")
            yield Static(
                "a score is agreement between retrieval strategies, not a probability -- enter opens a result",
                id="note",
            )

    def on_mount(self) -> None:
        """Focus the query box and label the results table."""
        self.query_one("#results", DataTable).add_columns("score", "memory", "block", "identity")
        self.query_one("#query", Input).focus()

    @on(Input.Submitted, "#query")
    def _submitted(self, event: Input.Submitted) -> None:
        """Run the query the planner was given."""
        self.search(event.value)

    @work(thread=True, exclusive=True, group="search")
    def search(self, text: str) -> None:
        """
        Retrieve, off the event loop.

        Args:
            text (str): What to look for.
        """
        try:
            result = self.service.search(text, limit=LIMIT)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error", timeout=10)
            return
        self.app.call_from_thread(self._fill, result)

    def _fill(self, result: dict[str, Any]) -> None:
        """
        Show the bundle.

        Args:
            result (dict[str, Any]): What ``service.search`` produced.
        """
        self.matches = list(result.get("matches", []))
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
        if not self.matches:
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


__all__ = ["LIMIT", "SearchScreen"]
