"""``vitruvio browse`` -- the brain as a place you can walk around in.

The shape is the one a reader already knows from a note-taking application: modules on the left, what is in the
selected module in the middle, and the selected thing itself on the right. What is different is what is being
shown. Every pane is evidence with an identity: a row is a block, the preview is the bytes that block names,
and the tabs beside it are the block's payload, its Merkle inclusion proof, and the provenance records that
name it. Nothing is summarised and nothing is generated -- the brain returns evidence, and so does this.

Three decisions about *this* interface rather than about the CLI.

**Reading is not querying.** The middle pane lists a module in its own order, and the filter box filters the
rows that were read. There is no relevance ranking in it and no index behind it, because reading a module and
retrieving from one are different operations -- ``s`` opens the search screen, which is the planner's answer and
is labelled as such. Blurring the two would put a second, worse retrieval path inside the interface people
spend the most time in.

**Every read happens on a worker thread.** Resolving a page of blocks reads and hashes each one, and a brain
with a canonical module of scanned PDFs will make that take a visible moment. Doing it on the event loop froze
the interface for exactly as long as the store took, which reads as a hang; a thread worker with an exclusive
group also means the fourth arrow-key press cancels the three previews nobody is waiting for any more.

**A block that cannot be read is still shown.** Tombstoned and missing rows appear, marked. A viewer that hid
them would make a redacted brain look like a smaller one, and the protocol is explicit that those two are not
the same thing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import RenderableType
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Input, Static, TabbedContent, TabPane, Tree

from vitruvio.cli import render
from vitruvio.cli.tui.screens import SearchScreen, SelectionScreen
from vitruvio.runtime import BrainService

MODULES = ("canonical", "episodic", "semantic", "procedural", "provenance")
"""The five modules, in the order the protocol introduces them: what was observed, what happened, what is
known, how to do it, and where all of it came from."""

PAGE = 200
"""Rows read per page.

Bounded because a page is read, not indexed: every row is a block resolved and verified out of the store, so an
unbounded module would be an unbounded wait. Two hundred fills any terminal several times over, and ``n``
brings the next page.
"""

READING_FIELDS = {
    "episodic": ("summary", "context", "outcome"),
    "semantic": ("label", "statement"),
    "procedural": ("label", "goal"),
}
"""Which payload fields make up the reading view of a derived block, in the order they read.

Canonical is absent because a canonical block *is* its bytes -- the preview draws those. Provenance is absent
because a record is a set of fields rather than a text, and the payload tab shows it whole.
"""


class ModuleTree(Tree[str]):
    """
    The module sidebar, with one binding the default tree does not have.

    Every module is a leaf, so ``right`` -- which a tree binds to "expand this node" -- had nothing to do and did
    nothing. That left the sidebar as a place a reader could arrive at and not get out of: the modules are on the
    left and their blocks are in the *next pane*, and no key in the tree went there.

    So ``right`` descends into the blocks when the cursor is on a module, and still expands when the cursor is on
    something that has children.
    """

    BINDINGS = [
        Binding("right,l", "descend", "open module", show=False),
    ]

    def focus_current_module(self) -> None:
        """
        Take focus, with the cursor on the module that is open rather than on the root.

        Coming back to the sidebar from the blocks and landing on "brain" would mean one wasted keypress every
        time, and worse, it would make the highlight disagree with what the middle pane is showing -- and moving
        the highlight is what switches modules, so the disagreement is one arrow key away from being real.
        """
        self.focus()
        app = self.app
        current = getattr(app, "kind", None)
        for node in self.root.children:
            if node.data == current:
                # Assigning the line rather than calling a move helper: the cursor is addressed by row, and the
                # node's line is where the tree has laid it out.
                self.cursor_line = node.line
                return

    def action_descend(self) -> None:
        """Move into the selected module's blocks, or expand a node that is still collapsed.

        The collapsed check matters rather than a plain "has children": the root is expanded from the start, so
        testing for children alone made ``right`` on arrival expand something already open and go nowhere.
        """
        node = self.cursor_node
        if node is not None and node.children and not node.is_expanded:
            node.expand()
            return
        self.screen.query_one("#blocks", DataTable).focus()


class BlockTable(DataTable[Any]):
    """
    The block list, with the way back out.

    ``left`` and ``escape`` return to the module sidebar. With a row cursor there is nothing horizontal to move
    along, so the key is free -- and a pane you can enter and not leave is the same trap the tree had.
    """

    BINDINGS = [
        Binding("left,h,escape", "ascend", "back to modules", show=False),
    ]

    def action_ascend(self) -> None:
        """Hand the cursor back to the module tree, landing on the module currently open."""
        tree = self.screen.query_one("#modules", ModuleTree)
        tree.focus_current_module()


class BrainBrowser(App[None]):
    """
    The browser.

    Attributes:
        service (BrainService | None): The one way in. Every pane goes through it, exactly as a command body
            does. ``None`` until a brain is chosen -- ``p`` opens the picker, and the browser can be pointed at
            another project's brain without being restarted.
        brain (str): The brain's path, for the title bar.
        project (str | None): The project it belongs to, so two projects' identically named brains are told apart.
        config_file (str | None): That project's configuration file, which is half of a brain's identity here.
    """

    CSS = """
    Screen { layers: base; }

    #modules { width: 22; border-right: solid $panel; }
    #middle { width: 1fr; min-width: 40; }
    #detail { width: 1fr; min-width: 40; border-left: solid $panel; }

    #filter { border: none; height: 3; background: $boost; }
    #blocks { height: 1fr; }

    #preview, #payload, #links, #proof { padding: 0 1; }

    .empty { color: $text-muted; padding: 1; }
    """
    """Inline rather than a ``.tcss`` file, and for a packaging reason: hatchling drops non-Python files from a
    package directory unless they are named as artifacts, so a stylesheet in a wheel is one more thing that can
    ship missing. A stylesheet that failed to install would be a completely unstyled interface."""

    BINDINGS = [
        Binding("q", "quit", "quit"),
        # Advertised even though Textual binds it by default, because "how do I get to the other pane" was the
        # first question this interface failed to answer, and an undiscoverable key is one that does not exist.
        Binding("tab", "focus_next", "pane"),
        Binding("m", "focus_modules", "modules"),
        Binding("p", "select_brain", "project/brain"),
        Binding("i", "identify", "which brain"),
        Binding("slash", "focus_filter", "filter"),
        Binding("s", "search", "search"),
        Binding("r", "reload", "reload"),
        Binding("t", "toggle_view", "original/text"),
        Binding("o", "open_external", "open"),
        Binding("e", "export", "export"),
        Binding("y", "copy_id", "copy id"),
        Binding("n", "next_page", "next page", show=False),
        Binding("b", "previous_page", "previous page", show=False),
        Binding("right_square_bracket", "next_pdf_page", "pdf +1", show=False),
        Binding("left_square_bracket", "previous_pdf_page", "pdf -1", show=False),
        Binding("question_mark", "help_panel", "keys"),
    ]

    def __init__(
        self,
        service: BrainService | None,
        *,
        brain: str,
        origin: str | None = None,
        name: str | None = None,
        project: str | None = None,
        config_file: str | None = None,
    ) -> None:
        """
        Build the browser over an already-resolved service, or over nothing yet.

        Args:
            service (BrainService | None): The service layer. ``None`` opens the selection screen instead of a
                brain, which is what ``vitruvio browse`` does when no layer named one: a picker is a better
                answer to "which brain did you mean" than an error naming five flags.
            brain (str): The brain's path, shown in the title.
            origin (str | None): Which layer of precedence selected that path -- flag, environment, file or state.
            name (str | None): Which of the project's named brains this is, when it is one.
            project (str | None): Which project it belongs to, when it belongs to a named one.
            config_file (str | None): That project's configuration file, so the picker can start where the
                session is rather than at whatever sorts first.

        Several layers can select a brain and only one of them is visible in the command that was typed, so a
        bare ``vitruvio browse`` opens *something* and the interface used to show only the path it landed on.
        That is the question the last four arguments exist to answer: a reader has to be able to tell one
        project's ``metrica-a`` from another's, because those are different brains and the path alone does not
        say which one you got.
        """
        super().__init__()
        self.service = service
        self.brain = brain
        self.origin = origin
        # `brain_name` rather than `name`: Textual's App already has a read-only `name` property, and assigning to
        # it raises. A shadowed attribute on a framework base class is a collision that only shows up at runtime.
        self.brain_name = name
        self.project = project
        self.config_file = config_file
        self.kind: str = MODULES[0]
        self.rows: list[dict[str, Any]] = []
        self.selected: dict[str, Any] | None = None
        self.offset = 0
        self.filter: str | None = None
        self.pdf_page = 0
        self.normalized = False
        self.counts: dict[str, int] = {}

    @property
    def opened(self) -> BrainService:
        """
        The service for the brain currently open.

        A property rather than the attribute itself because the attribute is now optional -- ``browse`` with no
        brain selected opens the picker instead of failing -- and every reader below runs only once something is
        open. Raising here rather than returning ``None`` keeps that assumption checkable: each caller is inside a
        worker that already reports its own failures, so the worst case is a notification rather than a crash.

        Returns:
            BrainService: The service.

        Raises:
            RuntimeError: If no brain is open. A bug in this module, not something a user can cause.
        """
        if self.service is None:
            raise RuntimeError("no brain is open yet")
        return self.service

    # --- Layout ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Lay out the three panes."""
        yield Header(show_clock=False)
        with Horizontal():
            yield ModuleTree("brain", id="modules")
            with Vertical(id="middle"):
                yield Input(placeholder="filter these rows (this is not a query)", id="filter")
                yield BlockTable(id="blocks", cursor_type="row", zebra_stripes=True)
            with Vertical(id="detail"), TabbedContent(id="tabs"):
                with TabPane("preview", id="tab-preview"), VerticalScroll():
                    yield Static(id="preview")
                with TabPane("payload", id="tab-payload"), VerticalScroll():
                    yield Static(id="payload")
                with TabPane("links", id="tab-links"), VerticalScroll():
                    yield Static(id="links")
                with TabPane("proof", id="tab-proof"), VerticalScroll():
                    yield Static(id="proof")
        yield Footer()

    def on_mount(self) -> None:
        """Install the house theme, then populate the module tree and load the first module."""
        # The renderables this interface shows are the *same* ones the CLI prints, and they name styles from
        # vitruvio's theme -- `digest`, `canonical`, `warn`. Textual renders them through its own Rich console,
        # which has never heard of those names and raises `MissingStyle` on the first table it measures. Pushing
        # the theme here is what makes one renderer serve both interfaces instead of two that drift.
        self.console.push_theme(render.THEME)
        self.title = "vitruvio"
        self.sub_title = self._where()
        table = self.query_one("#blocks", DataTable)
        table.add_columns("block", "title", "detail", "size", "type")
        # The blocks, not the sidebar. Browsing starts by reading what is in the module you opened on, so the
        # arrow keys should walk the evidence from the first keystroke. Focusing the tree instead -- which is what
        # composing it first made Textual do -- put the cursor in a five-item list of leaves where the arrows
        # appeared to do nothing at all.
        table.focus()
        if self.service is None:
            # Nothing was selected, so the first thing to do is ask. `call_after_refresh` rather than a direct
            # push: a screen pushed from `on_mount` is pushed before the app has a screen stack to put it on.
            self.call_after_refresh(self.action_select_brain)
            return
        self.load_modules()

    def _where(self) -> str:
        """
        Which brain this is, and which layer of precedence chose it, short enough to survive the header.

        The full path used to go here and the header truncated it -- which cut off the *end*, where the reason
        was. So the header carries the identifying short form and ``i`` carries the whole thing: a project brain
        by ``project/brain``, and an unnamed one by its parent directory and basename, because "brain" on its own
        is the name of every second brain on a machine and identifies nothing.

        The project is part of the short form rather than part of the detail, because two projects holding a
        ``metrica-a`` each is the normal case this interface now has to survive: ``eticompass/metrica-a`` and
        ``eticompass-v2/metrica-a`` are told apart by exactly the half that used to be dropped.

        Returns:
            str: e.g. ``facultad/analisis-numerico by flag``, or ``(no brain open)``.
        """
        from pathlib import Path

        if self.service is None:
            return "(no brain open -- p to choose one)"

        path = Path(self.brain)
        if self.project:
            # Whenever the project is known, not only when the brain has a name inside it. A single-brain project
            # declares its brain in `[brain].path` and never names it, so requiring both dropped the project from
            # the header of exactly the brain whose own label -- `brain` -- identifies nothing on its own.
            label = f"{self.project}/{self.brain_name or path.name}"
        else:
            label = self.brain_name or (f"{path.parent.name}/{path.name}" if path.parent.name else path.name)
        return f"{label} by {self.origin}" if self.origin else label

    # --- Loading --------------------------------------------------------------

    @work(thread=True, exclusive=True, group="modules")
    def load_modules(self) -> None:
        """Read the brain's anatomy and fill the tree."""
        try:
            info = self.opened.info()
        except Exception as error:  # a brain that cannot be opened is a message, not a traceback
            self.call_from_thread(self.notify, str(error), severity="error", timeout=10)
            return
        counts = {entry["memory_type"]: entry["block_count"] for entry in info["modules"]}
        self.call_from_thread(self._fill_tree, counts)

    def _fill_tree(self, counts: dict[str, int]) -> None:
        """
        Rebuild the module tree.

        Every module is listed, installed or not: a module absent from a selectively pulled brain is a fact
        about this brain, and a tree that showed only what is present would make it invisible.

        Args:
            counts (dict[str, int]): Block count per installed module.
        """
        self.counts = counts
        tree = self.query_one("#modules", Tree)
        tree.clear()
        tree.root.label = Text(f"{sum(counts.values())} blocks")
        tree.root.expand()
        for name in MODULES:
            held = counts.get(name)
            # A module that is not installed is listed with a dash rather than a sentence: the sidebar is 22
            # cells wide, and "(not installed)" was truncated to "(not instal" in every terminal narrower than
            # a laptop's. The absence still has to be visible, so it is a mark rather than prose.
            label = Text.assemble(
                (name, name if held else "muted"),
                (f"  {held}", "count") if held else ("  -", "muted"),
            )
            tree.root.add_leaf(label, data=name)
        self.load_rows()

    @work(thread=True, exclusive=True, group="rows")
    def load_rows(self) -> None:
        """Read a page of the selected module."""
        try:
            result = self.opened.blocks(self.kind, limit=PAGE, offset=self.offset, contains=self.filter)
        except Exception as error:
            self.call_from_thread(self.notify, str(error), severity="error", timeout=10)
            return
        self.call_from_thread(self._fill_rows, result)

    def _fill_rows(self, result: dict[str, Any]) -> None:
        """
        Fill the middle pane.

        Args:
            result (dict[str, Any]): What ``service.blocks`` produced.
        """
        self.rows = list(result["rows"])
        table = self.query_one("#blocks", DataTable)
        table.clear()
        for row in self.rows:
            size = row.get("size")
            table.add_row(
                render.digest(row.get("block_id")),
                Text(str(row.get("title", "")), style="value" if row.get("resolvable", True) else "bad"),
                Text(str(row.get("detail", "")), style="muted"),
                Text(_bytes(size) if isinstance(size, int) else "", style="muted"),
                Text(str(row.get("media_type") or row.get("kind") or ""), style="muted"),
                key=row["block_id"],
            )
        shown = f"{len(self.rows)} of {result['matched']}"
        where = f"  offset {self.offset}" if self.offset else ""
        self.sub_title = f"{self._where()}    {self.kind}  {shown}{where}"
        if not self.rows:
            self._set("preview", render.empty("Nothing in this module matches."))
            self.selected = None
            return
        table.move_cursor(row=0)
        self.select(self.rows[0])

    def select(self, row: dict[str, Any]) -> None:
        """
        Make a row the selected block and load its detail.

        Args:
            row (dict[str, Any]): The row.
        """
        self.selected = row
        self.pdf_page = 0
        self.normalized = False
        self._set("preview", render.empty("reading..."))
        self.load_detail(row)

    @work(thread=True, exclusive=True, group="detail")
    def load_detail(self, row: dict[str, Any]) -> None:
        """
        Read everything the right-hand pane shows about one block.

        The four tabs are loaded together rather than on demand. Each is one service call against a block
        already in the store, and loading them lazily would mean a visible pause every time somebody pressed
        a tab -- which is most of what browsing *is*.

        Args:
            row (dict[str, Any]): The selected row.
        """
        identity = row["block_id"]
        width = max(20, self.query_one("#preview", Static).size.width or 80)

        try:
            preview = self._preview(row, width=width)
        except Exception as error:
            # A worker that raises takes the whole application down -- Textual re-raises `WorkerFailed` and the
            # interface is gone. That happened over one canonical block whose bytes were not the image its media
            # type claimed: a decoder exception four calls deep ended the session. Every pane below already
            # reports its own failure, and the preview draws arbitrary registered bytes, so it is the one most
            # able to meet something unexpected. It reports too.
            preview = Text(f"{type(error).__name__}: {error}", style="bad")
        self.call_from_thread(self._set, "preview", preview)

        try:
            payload = self.opened.resolve(identity)["payload"]
            self.call_from_thread(self._set, "payload", render.payload(payload))
        except Exception as error:
            self.call_from_thread(self._set, "payload", Text(str(error), style="bad"))

        try:
            self.call_from_thread(self._set, "links", render.records(self.opened.related(identity)))
        except Exception as error:
            self.call_from_thread(self._set, "links", Text(str(error), style="bad"))

        try:
            proof = self.opened.prove(identity, row["memory_type"])
            view = render.fields(
                [
                    ("root", render.digest(proof["root"], full=True)),
                    ("leaf index", f"{proof['leaf_index']} of {proof['tree_size']}"),
                    ("audit path", f"{len(proof['audit_path'])} hashes"),
                    ("verified", render.verdict(proof["verified"], no="NO")),
                ]
            )
            self.call_from_thread(self._set, "proof", view)
        except Exception as error:
            self.call_from_thread(self._set, "proof", Text(str(error), style="bad"))

    def _preview(self, row: dict[str, Any], *, width: int) -> RenderableType:
        """
        What to show in the preview tab.

        Args:
            row (dict[str, Any]): The selected row.
            width (int): Cells available, for the drawings.

        Returns:
            RenderableType: The rendering.
        """
        from rich.console import Group

        head = render.media.describe(row)
        if not row.get("resolvable", True):
            return Group(head, "", Text(str(row.get("detail", "not resolvable")), style="bad"))

        view = row.get("normalized_view") or {}
        if self.normalized and view.get("blob"):
            try:
                data = self.opened.content(str(view["blob"]))
            except Exception as error:
                return Group(head, "", Text(str(error), style="bad"))
            return Group(head, "", render.media.preview(data, str(view.get("media_type", "text/plain")), width=width))

        if blob := row.get("blob"):
            media_type = str(row.get("media_type", "application/octet-stream"))
            try:
                data = self.opened.content(str(blob))
            except Exception as error:
                return Group(head, "", Text(str(error), style="bad"))
            body = render.media.preview(data, media_type, width=width, page=self.pdf_page)
            hint = None
            if render.media.is_pdf(media_type):
                pages = render.media.pdf_pages(data)
                hint = Text(f"page {self.pdf_page + 1} of {pages}   [ ] to turn", style="muted")
            elif view.get("blob"):
                hint = Text("t shows the normalized text view of these bytes", style="muted")
            return Group(head, "", body, *(("", hint) if hint is not None else ()))

        return Group(head, "", self._reading(row))

    def _reading(self, row: dict[str, Any]) -> RenderableType:
        """
        The reading view of a block that names no bytes.

        A derived block's text is the block: an episode's summary, a definition's statement, a procedure's
        goal. Shown as prose here and as the document in the payload tab, because both questions get asked.

        Args:
            row (dict[str, Any]): The selected row.

        Returns:
            RenderableType: The rendering.
        """
        from rich.console import Group
        from rich.markdown import Markdown

        try:
            payload = self.opened.resolve(row["block_id"])["payload"]
        except Exception as error:
            return Text(str(error), style="bad")

        fields = READING_FIELDS.get(str(row.get("memory_type")), ())
        parts: list[RenderableType] = []
        for name in fields:
            value = payload.get(name)
            if isinstance(value, str) and value.strip():
                parts += [Markdown(value.strip()), ""]
        if steps := payload.get("steps"):
            parts.append(
                render.lines(
                    (f"{position}. {step.get('action', '')}" for position, step in enumerate(steps, start=1)),
                )
            )
        if not parts:
            parts = [render.payload(payload)]
        return Group(*parts)

    def _set(self, target: str, renderable: RenderableType | list[RenderableType]) -> None:
        """
        Put a renderable into one of the detail panes.

        A list is grouped first. The shared renderers return a *sequence* of renderables -- a heading, a blank
        line, a table -- because ``Console.print`` takes them one at a time, and a widget takes exactly one:
        handing the list straight to ``Static.update`` renders the words "unable to display 'list' type", which
        is what this used to do in the links tab.

        Args:
            target (str): The widget id, without the ``#``.
            renderable (RenderableType | list[RenderableType]): What to show.
        """
        from rich.console import Group

        content = Group(*renderable) if isinstance(renderable, list) else renderable
        self.query_one(f"#{target}", Static).update(content)

    # --- Events ---------------------------------------------------------------

    @on(Tree.NodeHighlighted, "#modules")
    def _module_highlighted(self, event: Tree.NodeHighlighted[str]) -> None:
        """Switch modules as the sidebar cursor moves, rather than waiting to be told twice.

        Moving onto a module is unambiguous about what the reader wants to see, and requiring enter as well meant
        the middle pane sat showing the previous module while the highlight said otherwise.
        """
        self._switch(event.node.data)

    @on(Tree.NodeSelected, "#modules")
    def _module_chosen(self, event: Tree.NodeSelected[str]) -> None:
        """On enter, switch if the highlight has not already, then move into the blocks.

        Enter on a module means "show me this one", and the answer to that is in the next pane -- so the cursor
        goes there. Leaving it in the tree is what made a selected module look like a module that did nothing.
        """
        self._switch(event.node.data)
        self.query_one("#blocks", DataTable).focus()

    def _switch(self, name: object) -> None:
        """
        Open a module, if it is one and not the one already open.

        Args:
            name (object): A tree node's data, which is a memory type for a module and ``None`` for the root.
        """
        if not isinstance(name, str) or name == self.kind:
            return
        self.kind = name
        self.offset = 0
        self.load_rows()

    @on(DataTable.RowHighlighted, "#blocks")
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Follow the cursor: the highlighted row is the selected block."""
        key = event.row_key.value
        for row in self.rows:
            if row["block_id"] == key:
                if self.selected is None or self.selected["block_id"] != key:
                    self.select(row)
                return

    @on(Input.Changed, "#filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        """Refilter as it is typed. Each keystroke cancels the previous read rather than queueing behind it."""
        self.filter = event.value or None
        self.offset = 0
        self.load_rows()

    @on(Input.Submitted, "#filter")
    def _filter_submitted(self) -> None:
        """Hand the cursor back to the rows, which is where the reading happens."""
        self.query_one("#blocks", DataTable).focus()

    # --- Actions --------------------------------------------------------------

    def action_focus_filter(self) -> None:
        """Focus the filter box."""
        self.query_one("#filter", Input).focus()

    def action_focus_modules(self) -> None:
        """Focus the module sidebar, from wherever the cursor is."""
        self.query_one("#modules", ModuleTree).focus_current_module()

    def action_select_brain(self) -> None:
        """Choose which project, and which brain in it, to read.

        The interface used to be about whichever brain the command line resolved, and changing that meant quitting
        and running `vitruvio browse` again with different flags. That is untenable once a person keeps a project
        per subject or per client: reading across two of them is the normal case, not an exotic one.
        """
        self.browse_catalogue()

    @work(thread=True, exclusive=True, group="catalogue")
    def browse_catalogue(self) -> None:
        """Read every openable project off disk, then show the picker.

        On a worker because it reads a `vitruvio.toml` per project and checks a layout per brain. Small work, but
        it is filesystem work, and the event loop is not where filesystem work goes.
        """
        from pathlib import Path

        from vitruvio.cli.tui.selection import catalogue

        try:
            entries = catalogue(Path(self.config_file) if self.config_file else None)
        except Exception as error:
            self.call_from_thread(self.notify, str(error), severity="error", timeout=10)
            return
        self.call_from_thread(self._ask, entries)

    def _ask(self, entries: list[dict[str, Any]]) -> None:
        """
        Push the picker.

        Args:
            entries (list[dict[str, Any]]): The catalogue.
        """
        self.push_screen(
            SelectionScreen(entries, config_file=self.config_file, open_brain=self.brain or None),
            self._selected,
        )

    def _selected(self, choice: tuple[Path, str | None] | None) -> None:
        """
        Act on what the picker returned.

        Args:
            choice (tuple[Path, str | None] | None): The project's configuration file and the brain's name, or
                ``None`` when the screen was dismissed without choosing.
        """
        if choice is None:
            if self.service is None:
                # Dismissing the picker that opened *instead of* a brain leaves nothing to look at, so the only
                # honest thing to do is leave -- an empty interface would read as a brain that failed to load.
                self.exit()
            return
        config_file, brain = choice
        self.retarget(config_file, brain)

    @work(thread=True, exclusive=True, group="retarget")
    def retarget(self, config_file: Path, brain: str | None) -> None:
        """
        Point the whole interface at another brain.

        Resolution goes through the kernel rather than through anything this module knows, so the brain a picked
        row opens is the same brain ``--project x --brain y`` would open. Opening it is real work -- a brain's
        indices are rebuilt on open -- which is why this is a worker and why the header says so first.

        Args:
            config_file (Path): The project's configuration file.
            brain (str | None): The brain's name in that project, or ``None`` for a single-brain project's own.
        """
        from vitruvio.cli.tui.selection import open_selection

        self.call_from_thread(self.notify, f"opening {brain or config_file.parent.name}...")
        try:
            opened = open_selection(config_file, brain)
        except Exception as error:
            self.call_from_thread(self.notify, str(error), severity="error", timeout=10)
            return
        self.call_from_thread(self._adopt, opened, str(config_file))

    def _adopt(self, opened: dict[str, Any], config_file: str) -> None:
        """
        Make an opened brain the one on screen.

        Everything the reader was looking *at* is reset -- the page, the filter, the selected block -- because
        none of it refers to anything in the new brain. The module stays: "I was reading semantic memory" is a
        statement about what you are doing rather than about which brain you were in.

        Args:
            opened (dict[str, Any]): What :func:`~vitruvio.cli.tui.selection.open_selection` returned.
            config_file (str): The project's configuration file.
        """
        self.service = opened["service"]
        self.brain = str(opened["brain"])
        self.origin = opened["origin"]
        self.brain_name = opened["name"]
        self.project = opened["project"]
        self.config_file = config_file

        self.offset = 0
        self.filter = None
        self.selected = None
        self.pdf_page = 0
        self.normalized = False
        self.query_one("#filter", Input).value = ""
        self.sub_title = self._where()
        self.load_modules()

    def action_identify(self) -> None:
        """Say exactly which brain this is, in full, and which layer of precedence chose it.

        The question this answers is the first one a person actually has -- "wait, which brain am I looking at" --
        and it is a fair question, because a bare `vitruvio browse` consults four layers and only one of them is
        visible in what was typed. The header has room for the short form; this has room for the path.
        """
        self.identify()

    @work(thread=True, exclusive=True, group="identify")
    def identify(self) -> None:
        """Read the brain's own account of itself, then show it."""
        try:
            state = self.opened.state()
        except Exception as error:
            self.call_from_thread(self.notify, str(error), severity="error", timeout=10)
            return
        actor = state.get("actor") or {}
        where = f"selected by {self.origin}"
        if self.brain_name:
            where += f", as {self.project or 'the project'}'s {self.brain_name!r}"
        lines = [
            self.brain,
            where,
            f"snapshot {render.short(state['snapshot']['digest'])}, {state['block_count']} blocks",
            f"installed: {', '.join(state['installed']) or 'nothing'}",
            f"writes attributed to {actor.get('id') or '(no actor set)'}",
        ]
        if origin := state.get("origin"):
            lines.append(f"pulled from {origin['reference']}:{origin['tag']}")
        self.call_from_thread(self.notify, "\n".join(lines), title="which brain", timeout=20)

    def action_reload(self) -> None:
        """Re-read the brain. For a brain something else is writing to."""
        self.load_modules()

    def action_next_page(self) -> None:
        """Read the next page of this module."""
        if len(self.rows) == PAGE:
            self.offset += PAGE
            self.load_rows()

    def action_previous_page(self) -> None:
        """Read the previous page of this module."""
        if self.offset:
            self.offset = max(0, self.offset - PAGE)
            self.load_rows()

    def action_next_pdf_page(self) -> None:
        """Turn to the next page of a PDF."""
        if self.selected and render.media.is_pdf(str(self.selected.get("media_type", ""))):
            self.pdf_page += 1
            self.load_detail(self.selected)

    def action_previous_pdf_page(self) -> None:
        """Turn back one page of a PDF."""
        if self.selected and self.pdf_page:
            self.pdf_page -= 1
            self.load_detail(self.selected)

    def action_toggle_view(self) -> None:
        """Swap between the original bytes and the normalized view of them."""
        if not self.selected or not (self.selected.get("normalized_view") or {}).get("blob"):
            self.notify("this block names no normalized view", severity="warning")
            return
        self.normalized = not self.normalized
        self.load_detail(self.selected)

    def action_copy_id(self) -> None:
        """Copy the selected block's identity to the clipboard."""
        if self.selected:
            self.copy_to_clipboard(self.selected["block_id"])
            self.notify("block id copied")

    def action_export(self) -> None:
        """Write the selected block's bytes into the working directory."""
        if not self.selected or not self.selected.get("blob"):
            self.notify("this block names no content to export", severity="warning")
            return
        self.export(
            str(self.selected["blob"]),
            self.selected.get("origin"),
            str(self.selected.get("media_type") or ""),
        )

    @work(thread=True, group="export")
    def export(self, blob: str, name: str | None, media_type: str | None = None) -> None:
        """
        Copy content out of the brain.

        Args:
            blob (str): The content address.
            name (str | None): The origin's file name, when one was recorded.
            media_type (str | None): The block's media type, which names the file when the origin does not.
        """
        from pathlib import Path

        from vitruvio.cli.render import desktop

        target = Path.cwd() / desktop.filename(name, blob, media_type)
        try:
            result = self.opened.export_content(blob, target)
        except Exception as error:
            self.call_from_thread(self.notify, str(error), severity="error", timeout=10)
            return
        self.call_from_thread(self.notify, f"wrote {result['path']} ({result['size']} bytes)")

    def action_open_external(self) -> None:
        """Hand the selected block's bytes to whatever the desktop opens them with.

        For everything a terminal cannot show: a video, a spreadsheet, a PDF someone wants to read properly.
        The bytes are written to a temporary file first, because the brain is a content-addressed store and
        there is no path inside it to hand over.
        """
        if not self.selected or not self.selected.get("blob"):
            self.notify("this block names no content to open", severity="warning")
            return
        self.open_externally(
            str(self.selected["blob"]),
            self.selected.get("origin"),
            str(self.selected.get("media_type") or ""),
        )

    @work(thread=True, group="open")
    def open_externally(self, blob: str, name: str | None, media_type: str | None = None) -> None:
        """
        Write the bytes out and hand them to the desktop's own handler.

        This is how a PDF gets read rather than glanced at: the preview is a thumbnail bounded by how many
        character cells the pane has, and the operating system already owns a program that opens a page at full
        resolution.

        Args:
            blob (str): The content address.
            name (str | None): The origin recorded at registration, which becomes the file's name.
            media_type (str | None): The block's media type. Load-bearing rather than decorative: a handler is
                chosen by suffix, and a brain whose blocks record no origin -- a pulled one -- would otherwise
                hand a PDF to a text editor under a name that is a bare content address.
        """
        from pathlib import Path

        from vitruvio.cli.render import desktop

        target = desktop.scratch(name, blob, media_type)
        try:
            result = self.opened.export_content(blob, target)
        except Exception as error:
            self.call_from_thread(self.notify, str(error), severity="error", timeout=10)
            return
        try:
            desktop.open_path(Path(result["path"]))
        except desktop.NoOpenerError as error:
            # The bytes are already written, so the useful message is where they are -- not that opening failed.
            # Over SSH with no display this is the whole outcome rather than a degraded one.
            self.call_from_thread(
                self.notify,
                f"{error}\nthe bytes are at {result['path']}",
                severity="warning",
                timeout=20,
            )
            return
        self.call_from_thread(self.notify, f"opened {result['path']}")

    def action_search(self) -> None:
        """Open the search screen -- the planner's answer, as distinct from the filter box."""
        self.push_screen(SearchScreen(self.opened), self._searched)

    def _searched(self, block_id: str | None) -> None:
        """
        Jump to a block the search screen returned.

        Args:
            block_id (str | None): The chosen block, or ``None`` if the screen was dismissed.
        """
        if block_id:
            self.reveal(block_id)

    @work(thread=True, exclusive=True, group="reveal")
    def reveal(self, block_id: str) -> None:
        """
        Show a block that may not be in the current module or on the current page.

        Args:
            block_id (str): The block to show.
        """
        try:
            resolved = self.opened.resolve(block_id)
        except Exception as error:
            self.call_from_thread(self.notify, str(error), severity="error", timeout=10)
            return
        kind = str(resolved["memory_type"])
        found = None
        offset = 0
        while found is None:
            page = self.opened.blocks(kind, limit=PAGE, offset=offset)
            for row in page["rows"]:
                if row["block_id"] == block_id:
                    found = row
                    break
            if found is not None or not page["truncated"]:
                break
            offset += PAGE
        if found is None:
            self.call_from_thread(self.notify, "that block is not in its module's composition", severity="warning")
            return
        self.kind, self.offset, self.filter = kind, offset, None
        self.call_from_thread(self._jump, block_id)

    def _jump(self, block_id: str) -> None:
        """
        Reload the middle pane and put the cursor on one row.

        Args:
            block_id (str): The row to land on.
        """
        self.query_one("#filter", Input).value = ""
        self.load_rows()
        self.set_timer(0.4, lambda: self._land(block_id))

    def _land(self, block_id: str) -> None:
        """
        Move the cursor onto a row once its page has arrived.

        Args:
            block_id (str): The row to land on.
        """
        table = self.query_one("#blocks", DataTable)
        for index, row in enumerate(self.rows):
            if row["block_id"] == block_id:
                table.move_cursor(row=index)
                self.select(row)
                return

    def action_help_panel(self) -> None:
        """Show every key binding, including the ones the footer has no room for."""
        self.action_show_help_panel()


def _bytes(size: int) -> str:
    """
    A byte count a person can read.

    Args:
        size (int): The count.

    Returns:
        str: e.g. ``1.4 MiB``.
    """
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"  # pragma: no cover -- the loop above always returns


def run(service: BrainService, brain: Path | str) -> None:
    """
    Open the browser on a brain.

    Args:
        service (BrainService): The service layer, already resolved.
        brain (Path | str): The brain's path, for the title bar.
    """
    BrainBrowser(service, brain=str(brain)).run()


__all__ = ["MODULES", "PAGE", "BrainBrowser", "run"]
