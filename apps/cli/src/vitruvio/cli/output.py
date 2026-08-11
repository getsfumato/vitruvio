"""The output contract: one JSON object, or human text, and the discipline that separates them.

Two rules make this CLI drivable by an agent as well as by a person.

**stdout is the result; stderr is everything else.** Progress, warnings and log lines never touch stdout, so
``vitruvio search q --json | jq`` works unconditionally, without a quiet flag and without luck.

**``--json`` emits exactly one object, with a stable top level.** ``{vitruvio, command, ok, data, warnings,
error}`` is the same shape for every command, so a caller branches on ``ok`` and then on ``error.code``
without knowing which command it ran. The alternative -- per-command JSON shapes -- forces whatever is
driving the CLI to special-case forty outputs, which is how a machine-readable interface stops being one.

Note that ``data`` is passed through unchanged. Rendering an ``EvidenceBundle`` is the runtime's job
(``vitruvio.runtime.wire``), not this module's: the CLI and the future MCP server must serialize identical
dictionaries or they will drift.

**Human output goes through Rich, and only human output.** A command hands ``emit`` a renderable -- a table, a
label-and-value block -- and this module decides whether anything is drawn at all. Rich is constructed lazily
and never in ``--json`` mode, which is what keeps the envelope byte-identical to what it was before there was a
renderer, and keeps ``config show`` from importing a rendering library to print one line.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from vitruvio.kernel import ExitCode, VitruvioError, __version__

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rich.console import Console as RichConsole
    from rich.console import RenderableType


@dataclass
class Envelope:
    """
    The one object ``--json`` prints.

    Attributes:
        command (str): Dotted operation name, e.g. ``query.search``. Stable across releases.
        ok (bool): Whether the operation succeeded.
        data (Any): The payload, already JSON-able.
        warnings (list[str]): Non-fatal notes. Present even on success, because a degraded answer that looks
            identical to a clean one is the failure mode this whole design is trying to avoid.
        error (dict[str, Any] | None): The failure, when there is one.
    """

    command: str
    ok: bool = True
    data: Any = None
    warnings: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None

    @classmethod
    def failure(cls, command: str, error: VitruvioError) -> Envelope:
        """
        Build an envelope from an error.

        Args:
            command (str): The operation that failed.
            error (VitruvioError): What went wrong.

        Returns:
            Envelope: The failure envelope.
        """
        return cls(
            command=command,
            ok=False,
            error={
                "code": error.code,
                "kind": type(error).__name__,
                "message": error.message,
                "hint": error.hint,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        """The wire form, with ``vitruvio`` first so a truncated read still identifies the producer."""
        return {
            "vitruvio": __version__,
            "command": self.command,
            "ok": self.ok,
            "data": self.data,
            "warnings": self.warnings,
            "error": self.error,
        }


class Console:
    """
    Where a command writes.

    Holds the ``--json`` and ``--quiet`` decisions so that individual commands do not each re-implement
    them, and so that a command cannot accidentally print human text into a JSON stream.

    Attributes:
        json_mode (bool): Whether to emit an envelope instead of prose. Implies quiet.
        quiet (bool): Whether to suppress notes on stderr.
        color (bool): Whether human output may use colour.
    """

    def __init__(self, *, json_mode: bool = False, quiet: bool = False, color: bool = True) -> None:
        """
        Build a console.

        Args:
            json_mode (bool): Emit a JSON envelope.
            quiet (bool): Suppress informational output on stderr.
            color (bool): Permit colour in human output.
        """
        self.json_mode = json_mode
        self.quiet = quiet or json_mode
        self.color = color and not json_mode
        self._warnings: list[str] = []
        self._rich: RichConsole | None = None

    def rich(self) -> RichConsole:
        """
        The Rich console human output is drawn with, built on first use.

        Lazy for two reasons. Importing Rich costs a few milliseconds and the kernel-is-the-floor rule exists
        because ``config show`` and ``brain use`` are measured in tens of them; and in ``--json`` mode nothing
        here is ever reached, so a console constructed in ``__init__`` would be one built for every agent-driven
        call that will never draw anything.

        Returns:
            RichConsole: The console, carrying ``--no-color``.
        """
        if self._rich is None:
            from vitruvio.cli.render.theme import console

            self._rich = console(color=self.color)
        return self._rich

    def _aside(self, message: str, style: str) -> None:
        """
        Write one styled line to stderr, without wrapping it.

        ``soft_wrap`` is not a detail: a hint is usually a command to run, and a hint Rich broke across two
        lines at column eighty is a command that no longer copies. The terminal may soft-wrap it, which puts the
        decision where it belongs.

        Args:
            message (str): The line.
            style (str): A style from the theme.
        """
        from vitruvio.cli.render.theme import console

        console(color=self.color, stderr=True).print(message, style=style, soft_wrap=True)

    def warn(self, message: str) -> None:
        """
        Record a non-fatal note.

        In JSON mode it lands in ``warnings`` rather than on stderr, so that a machine reading the envelope
        sees it. In human mode it goes to stderr immediately, so that a person sees it in order.

        Args:
            message (str): The note.
        """
        self._warnings.append(message)
        if not self.json_mode:
            self._aside(f"warning: {message}", "warn")

    def note(self, message: str) -> None:
        """
        Write an informational line to stderr, unless quiet.

        Args:
            message (str): The line.
        """
        if not self.quiet:
            self._aside(message, "muted")

    def emit(
        self,
        command: str,
        data: Any = None,
        *,
        view: RenderableType | Sequence[RenderableType] | None = None,
        lines: Sequence[str] | None = None,
    ) -> ExitCode:
        """
        Write a successful result.

        Args:
            command (str): The dotted operation name.
            data (Any): The JSON-able payload.
            view (RenderableType | Sequence[RenderableType] | None): Human rendering, as one Rich renderable or
                a sequence of them printed in order. This is what a command should pass.
            lines (Sequence[str] | None): Human rendering as plain strings. Kept for output that is genuinely
                lines -- a generated completion script, a rendered plan tree -- where wrapping it in a
                renderable would only be ceremony.
            The payload's own JSON is the fallback when a command has written neither.

        Returns:
            ExitCode: Always :data:`ExitCode.OK`, returned so a command body can ``return console.emit(...)``.
        """
        if self.json_mode:
            envelope = Envelope(command=command, data=data, warnings=self._warnings)
            print(json.dumps(envelope.to_dict(), indent=2, sort_keys=False, default=str))
        elif view is not None:
            # A str is a Sequence of str, so it would iterate into characters. Checked rather than assumed
            # because the failure is silent and absurd: one character per line.
            parts = [view] if isinstance(view, str) or not isinstance(view, (list, tuple)) else list(view)
            for part in parts:
                self.rich().print(part)
        elif lines is not None:
            for line in lines:
                print(line)
        elif data is not None:
            print(json.dumps(data, indent=2, default=str))
        return ExitCode.OK

    def fail(self, command: str, error: VitruvioError) -> ExitCode:
        """
        Write a failure, in whichever mode is active.

        Args:
            command (str): The dotted operation name.
            error (VitruvioError): What went wrong.

        Returns:
            ExitCode: The error's own exit code, for the caller to return or exit with.
        """
        if self.json_mode:
            envelope = Envelope.failure(command, error)
            envelope.warnings = self._warnings
            print(json.dumps(envelope.to_dict(), indent=2, default=str))
        else:
            self._aside(f"error: {error.message}", "bad")
            if error.hint:
                self._aside(f"hint: {error.hint}", "warn")
        return error.exit_code
