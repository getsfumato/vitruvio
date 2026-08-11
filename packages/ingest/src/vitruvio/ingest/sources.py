"""Sources: the declared places material is acquired from, and the disciplines a source has to observe.

A source is the third kind of thing in this package, and it does not share the other two's rules. A pipeline must be
deterministic because its output is content-addressed evidence; a proposer may be a model because its output is a
proposal the validation gate judges. **A source is neither: it is I/O against a world that changes.** Its rule is
therefore the third one -- *a source may fail, may return different results tomorrow, and may never be trusted with
an unbounded operation.* Everything below exists to make that rule cheap to obey.

Which is most of the argument for :class:`BaseSource`. A plugin author writing against the bare :class:`Source`
protocol would reimplement -- and get wrong -- the five things that turn a fetch into a hang or a leak:

* ``stdin`` closed, because a tool with an interactive prompt otherwise waits forever with nothing on screen;
* a timeout on every subprocess, and a generous one, because a source is allowed to do real work;
* bytes and not text, because decoding a PDF corrupts it;
* an environment with ``VITRUVIO_*`` stripped, because a source that shells back into vitruvio while inheriting
  ``VITRUVIO_BRAIN`` is a loop;
* path containment, because a glob that follows a symlink out of its root turns "ingest this folder" into "ingest
  whatever that link points at".

And one thing the config deliberately cannot do: **there is no way to declare a command line.** ``vitruvio.toml``
names a kind; a kind is a Python class installed under ``$XDG_CONFIG_HOME`` or shipped by a distribution. Importing
a module you wrote, from your own config directory, is code execution at the trust level of a shell profile.
Executing an argv that arrived with a ``git clone`` is not, and no confirmation prompt makes it so.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from vitruvio.ingest.media import media_type_for
from vitruvio.kernel import ConfigError, SourceError, SourceUnavailableError, plugin_dir

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from vitruvio.kernel import SourceSpec

ENTRY_POINT_GROUP = "vitruvio.sources"
"""Where a distributable source kind registers itself.

The same mechanism vitruvio's own bundle would use, so a third-party kind and a built-in one are indistinguishable
to the resolver -- which is the property that makes "write your own" a first-class path rather than a lesser one.
"""

MODULE_PREFIX = "vitruvio_source_"
"""Namespace for a plugin loaded from a file, so it cannot shadow an installed module."""

STDERR_TAIL = 400
"""How much of a failed command's stderr to quote. Enough to carry the real message, short enough to read."""


@dataclass(frozen=True, slots=True)
class Item:
    """
    One thing a source is offering, before anything has been fetched.

    Listing is separate from fetching precisely so that ``--dry-run`` and the duplicate checks can happen without a
    download: every field here must be knowable from a listing.

    Attributes:
        id (str): Stable within this source. What its own system calls the thing.
        origin (str): Where it came from, as a stable identifier -- a URL, ``arxiv:2401.12345``,
            ``aula://77/4821``. **Required**, unlike the free-text ``origin`` a manual registration may omit,
            because this is the dedup key: it is projected into the provenance hash-map index, and a repeated pull
            answers "already have it?" by looking it up. Canonicalising it is the source's job. An origin carrying a
            session token or a rotating query parameter is not stable across fetches, and only the source knows
            which parts of its own addresses are incidental.
        media_type (str | None): What it is, when the source knows. ``None`` means guess from the name.
        digest (str | None): An ``OciDigest`` of the content, if the source can produce one *cheaply and exactly*.
            ``None`` is the normal case and not a deficiency: Moodle's ``contenthash`` is SHA-1, an HTTP ``ETag``
            is not a content hash, and a YouTube transcript has no digest at all until it is fetched. Content
            addressing catches the duplicate anyway -- one wasted download, not a wrong result.
        title (str | None): A human label, for the pull report.
        size (int | None): Bytes, when the listing says. Used to refuse an oversized item before fetching it.
    """

    id: str
    origin: str
    media_type: str | None = None
    digest: str | None = None
    title: str | None = None
    size: int | None = None


@dataclass(frozen=True, slots=True)
class FetchResult:
    """One fetched item's bytes and metadata learned only while acquiring it.

    A listing often knows the MIME type and can put it on :class:`Item`. Some remote systems expose only a human
    label until the download redirects to a real filename. Those sources return this object so the canonical block
    records the specific type instead of ``application/octet-stream``. Returning bare ``bytes`` remains supported
    for sources whose listing already carries everything Vitruvio needs.

    Attributes:
        data (bytes): The acquired content, unchanged.
        media_type (str | None): The authoritative type discovered by the fetch, when known.
        title (str | None): The real filename or a better report label discovered by the fetch.
    """

    data: bytes
    media_type: str | None = None
    title: str | None = None


@runtime_checkable
class Source(Protocol):
    """
    What ``source pull`` needs from a source, and nothing more.

    Two methods, deliberately: listing is cheap and repeatable, fetching is expensive and may fail. Collapsing them
    into one ``iterate()`` would make it impossible to skip a duplicate without downloading it, which is the whole
    economy of a repeated pull.
    """

    KIND: ClassVar[str]

    @property
    def available(self) -> bool:
        """Whether this source can be used right now: its directory exists, its extra is installed, its tool is on
        ``PATH``. False is a *report*, not an error -- ``source status`` shows it without failing."""
        ...

    def list(self) -> Sequence[Item]:
        """Everything the source is offering, fetching none of it."""
        ...

    def fetch(self, item: Item) -> bytes | FetchResult:
        """The bytes of one item, optionally with metadata learned while fetching it."""
        ...


class BaseSource:
    """
    The base every source kind inherits, built-in or written by hand.

    Subclass it, set :attr:`KIND`, implement :meth:`list` and :meth:`fetch`. Reach for :meth:`run`, :meth:`get` and
    :meth:`contain` rather than ``subprocess``, ``httpx`` and ``Path.read_bytes`` -- each one carries a bound that
    the naked call does not, and the module docstring says which and why.

    Attributes:
        KIND (str): The name ``vitruvio.toml`` uses to select this class. Must be set by a subclass.
    """

    KIND: ClassVar[str] = ""

    def __init__(self, *, name: str, spec: SourceSpec, root: Path | None = None, cwd: Path | None = None) -> None:
        """
        Build a source from its declaration.

        A subclass that validates its ``options`` should do it here and raise
        :class:`~vitruvio.kernel.ConfigError`, because the constructor is the only code that knows what this kind's
        options mean. That is why they are untyped in the schema: the set of kinds is open.

        Args:
            name (str): The source's name in the project, used in every message about it.
            spec (SourceSpec): Its declaration.
            root (Path | None): ``spec.path``, already resolved against the configuration file. Resolved by the
                caller on purpose -- a relative path in a committed file resolves against *that file*, and a plugin
                author must not have to remember it.
            cwd (Path | None): Where a subprocess runs. The configuration file's directory, so a tool that writes
                relative to where it was invoked writes inside the project.
        """
        self.name = name
        self.spec = spec
        self.root = root
        self.cwd = cwd
        self.options: dict[str, Any] = dict(spec.options)

    def __repr__(self) -> str:
        """Name and kind, which is what a debugger session actually needs."""
        return f"{type(self).__name__}(name={self.name!r}, kind={self.KIND!r})"

    @property
    def available(self) -> bool:
        """Assume usable. A kind with a dependency, a tool or a directory to check overrides this."""
        return True

    def unavailable_because(self) -> str | None:
        """
        Why this source is unusable, in one sentence, or ``None`` when it is fine.

        Separate from :attr:`available` so that ``source status`` can print the reason. "unavailable" on its own
        costs the reader a turn to investigate; "``~/Downloads/arxiv`` does not exist" does not.

        Returns:
            str | None: The reason, or ``None``.
        """
        return None if self.available else "unavailable"

    def list(self) -> Sequence[Item]:
        """
        Everything on offer, fetching nothing.

        Returns:
            Sequence[Item]: The items.

        Raises:
            NotImplementedError: Always, here. A subclass must implement it.
        """
        raise NotImplementedError

    def fetch(self, item: Item) -> bytes | FetchResult:
        """
        The bytes of one item.

        Args:
            item (Item): One item from :meth:`list`.

        Returns:
            bytes | FetchResult: Its content, optionally with metadata unavailable to :meth:`list`.

        Raises:
            NotImplementedError: Always, here. A subclass must implement it.
        """
        raise NotImplementedError

    # -- the disciplines ------------------------------------------------------------------------------------

    def run(self, argv: Sequence[str], *, timeout: int | None = None) -> bytes:
        """
        Run a command and return its stdout as bytes.

        Never through a shell: ``argv`` is a list and ``shell=True`` appears nowhere in this module. The five bounds
        are the point -- ``stdin`` is closed, the timeout is real and generous, output stays bytes, ``VITRUVIO_*``
        is stripped from the environment, and a failure quotes the tail of stderr instead of an exit code alone.

        Args:
            argv (Sequence[str]): The program and its arguments. A sequence, never a string -- a string would be
                interpreted by a shell, and there is no shell here.
            timeout (int | None): Seconds. Defaults to the source's declared ``timeout``.

        Returns:
            bytes: Standard output, undecoded. Decoding here would corrupt every PDF that came through it.

        Raises:
            SourceError: If the program is missing, timed out, or exited non-zero.
        """
        limit = timeout if timeout is not None else self.spec.timeout
        try:
            # argv is a list and never a string, and shell=True appears nowhere in this module.
            completed = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=limit,
                cwd=str(self.cwd) if self.cwd else None,
                env=self.environment(),
                check=False,
            )
        except FileNotFoundError as error:
            raise SourceError(
                f"source {self.name!r} needs {argv[0]!r}, which is not on PATH",
                hint=f"install {argv[0]!r}, or point the source at a kind that does not need it",
            ) from error
        except subprocess.TimeoutExpired as error:
            raise SourceError(
                f"source {self.name!r} ran {argv[0]!r} for {limit}s without finishing",
                hint=f"raise `timeout` for this source in vitruvio.toml, or check whether {argv[0]!r} is waiting "
                f"for input it will never get",
            ) from error
        except OSError as error:
            raise SourceError(f"source {self.name!r} could not run {argv[0]!r}: {error}") from error

        if completed.returncode != 0:
            tail = completed.stderr.decode("utf-8", "replace").strip()[-STDERR_TAIL:]
            detail = f": {tail}" if tail else ""
            raise SourceError(f"source {self.name!r}: {argv[0]!r} exited {completed.returncode}{detail}")
        return completed.stdout

    def run_text(self, argv: Sequence[str], *, timeout: int | None = None) -> str:
        """
        :meth:`run`, decoded as UTF-8 with replacement.

        Separate rather than a ``text=`` flag so that the byte-returning path is the one a caller reaches for by
        default, and a decode is something the caller asked for.

        Args:
            argv (Sequence[str]): The program and its arguments.
            timeout (int | None): Seconds.

        Returns:
            str: Standard output, decoded.
        """
        return self.run(argv, timeout=timeout).decode("utf-8", "replace")

    def environment(self) -> dict[str, str]:
        """
        The environment a subprocess gets: this one, with every ``VITRUVIO_*`` variable removed.

        A source shelling back into vitruvio while inheriting ``VITRUVIO_BRAIN`` writes into the brain that is
        pulling, which is a loop, and one whose blocks are hard to tell from legitimate ones afterwards.

        Returns:
            dict[str, str]: The environment to pass.
        """
        return {key: value for key, value in os.environ.items() if not key.startswith("VITRUVIO_")}

    def get(self, url: str, *, timeout: int | None = None, headers: dict[str, str] | None = None) -> bytes:
        """
        Fetch one URL, bounded.

        Behind the ``[api]`` extra, like every other outbound HTTP call in vitruvio: a brain that only reads local
        directories should not need an HTTP client installed.

        Args:
            url (str): What to fetch.
            timeout (int | None): Seconds. Defaults to the source's declared ``timeout``.
            headers (dict[str, str] | None): Extra request headers.

        Returns:
            bytes: The response body.

        Raises:
            SourceError: If the request failed, or answered with a status other than 200.
        """
        try:
            import httpx
        except ImportError as error:  # pragma: no cover - depends on the installed extras
            raise SourceUnavailableError(f"source {self.name!r} needs an HTTP client; install vitruvio[api]") from error

        limit = timeout if timeout is not None else self.spec.timeout
        try:
            response = httpx.get(url, timeout=limit, headers=headers, follow_redirects=True)
        except httpx.HTTPError as error:
            raise SourceError(f"source {self.name!r} could not reach {url}: {error}") from error

        if response.status_code != 200:
            raise SourceError(f"source {self.name!r}: {url} answered {response.status_code}")

        body = response.content
        if self.spec.max_bytes is not None and len(body) > self.spec.max_bytes:
            raise SourceError(
                f"source {self.name!r}: {url} returned {len(body)} bytes, over the declared max_bytes "
                f"({self.spec.max_bytes})"
            )
        return body

    def contain(self, path: Path, *, allow_symlinks: bool = False) -> Path:
        """
        Check that a path is a real file inside this source's root, and small enough to read.

        Four refusals, each for a failure that has a name:

        * **outside the root** -- a glob that followed a link out of its directory turns "ingest this folder" into
          "ingest whatever that points at", and a canonical block is content-addressed and Merkle-committed before
          anyone notices;
        * **a symlink**, before resolution, because a link inside the root pointing inside the root still means the
          same bytes get registered twice under two origins;
        * **not a regular file** -- and this one is not theoretical: ``read_bytes()`` on a FIFO blocks forever, and
          a FIFO is something a glob will happily hand you;
        * **too large**, checked against ``stat().st_size`` *before* the read rather than after, which is the
          difference between a refusal and an out-of-memory kill.

        Args:
            path (Path): The candidate.
            allow_symlinks (bool): Permit a symlink whose target is still inside the root.

        Returns:
            Path: The resolved path, safe to read.

        Raises:
            SourceError: If any of the four refusals applies.
        """
        if not allow_symlinks and path.is_symlink():
            raise SourceError(
                f"source {self.name!r} refuses the symlink {path}",
                hint="a link registers the same bytes under a second origin; register the target directly",
            )

        resolved = path.expanduser().resolve()
        if self.root is not None and not resolved.is_relative_to(self.root):
            raise SourceError(
                f"source {self.name!r} refuses {resolved}, which is outside {self.root}",
                hint="a source may only read inside its declared path",
            )
        if not resolved.is_file():
            detail = "does not exist" if not resolved.exists() else "is not a regular file"
            raise SourceError(f"source {self.name!r}: {resolved} {detail}")

        size = resolved.stat().st_size
        if self.spec.max_bytes is not None and size > self.spec.max_bytes:
            raise SourceError(
                f"source {self.name!r}: {resolved} is {size} bytes, over the declared max_bytes ({self.spec.max_bytes})"
            )
        return resolved


class DirectorySource(BaseSource):
    """
    Files in a directory.

    The first built-in kind, and the one that composes with everything else. A tool that *materialises* files --
    ``aulasvirtuales`` downloading a semester, a browser extension dropping arXiv PDFs into a folder -- becomes a
    plugin whose :meth:`list` refreshes a directory and then delegates here. Which is the argument for shipping
    this one first: it is the second half of most other sources.

    Options:
        glob (str): Which names to take. Default ``*``.
        recursive (bool): Whether to descend. Default true.
        hidden (bool): Whether to include dotfiles. Default false -- ``.DS_Store`` and ``.git`` are not evidence.
    """

    KIND: ClassVar[str] = "directory"

    KNOWN_OPTIONS: ClassVar[frozenset[str]] = frozenset({"glob", "recursive", "hidden"})

    def __init__(self, *, name: str, spec: SourceSpec, root: Path | None = None, cwd: Path | None = None) -> None:
        """
        Validate the options this kind understands.

        Args:
            name (str): The source's name.
            spec (SourceSpec): Its declaration.
            root (Path | None): The resolved directory.
            cwd (Path | None): Unused here; a directory source runs no subprocess.

        Raises:
            ConfigError: If ``path`` is missing, or an option is not one this kind knows. An unknown option is an
                error rather than ignored: a typo'd ``glob`` that silently matched everything would register a
                directory's worth of material nobody asked for.
        """
        super().__init__(name=name, spec=spec, root=root, cwd=cwd)
        if root is None:
            raise ConfigError(
                f"source {name!r} is a directory source with no `path`",
                hint=f'set path = "..." under [sources.{name}]',
            )
        unknown = set(self.options) - self.KNOWN_OPTIONS
        if unknown:
            raise ConfigError(
                f"source {name!r} sets options this kind does not know: {', '.join(sorted(unknown))}",
                hint=f"the directory kind takes {', '.join(sorted(self.KNOWN_OPTIONS))}",
            )
        self.glob = str(self.options.get("glob", "*"))
        self.recursive = bool(self.options.get("recursive", True))
        self.hidden = bool(self.options.get("hidden", False))

    @property
    def available(self) -> bool:
        """Whether the directory is there. A folder that has not been created yet is a report, not a crash."""
        return self.root is not None and self.root.is_dir()

    def unavailable_because(self) -> str | None:
        """Which of the two it is, because "create it" and "that is a file" need different responses."""
        if self.root is None:
            return "no path declared"
        if not self.root.exists():
            return f"{self.root} does not exist"
        if not self.root.is_dir():
            return f"{self.root} is not a directory"
        return None

    def list(self) -> Sequence[Item]:
        """
        Every matching file, in a stable order.

        Sorted because a pull's report, and its ``--limit``, should not depend on the order the filesystem
        happened to return -- an unstable ``--limit 10`` takes a different ten files each run.

        Returns:
            Sequence[Item]: One item per file.

        Raises:
            SourceError: If the directory is not there.
        """
        if self.root is None or not self.root.is_dir():
            raise SourceError(
                f"source {self.name!r}: {self.root} is not a directory",
                hint=self.unavailable_because(),
            )
        return [self._item(path) for path in sorted(self._walk())]

    def fetch(self, item: Item) -> bytes:
        """
        Read one file, through :meth:`contain`.

        Args:
            item (Item): An item from :meth:`list`.

        Returns:
            bytes: Its content.
        """
        return self.contain(Path(item.id)).read_bytes()

    def _walk(self) -> Iterator[Path]:
        """Matching regular files, skipping what a glob should not have offered."""
        assert self.root is not None, "list() checked it, and this is private"
        candidates = self.root.rglob(self.glob) if self.recursive else self.root.glob(self.glob)
        for path in candidates:
            if not self.hidden and any(part.startswith(".") for part in path.relative_to(self.root).parts):
                continue
            if path.is_symlink() or not path.is_file():
                # A FIFO reaches here from a glob, and reading one blocks forever. Skipped rather than raised:
                # one odd entry in a directory must not stop the other forty files from being registered.
                continue
            yield path

    def _item(self, path: Path) -> Item:
        """One file as an item, addressed by its own URI."""
        return Item(
            id=str(path),
            origin=path.as_uri(),
            media_type=media_type_for(path, self.spec.media_type),
            title=path.name,
            size=path.stat().st_size,
        )


BUILTIN: tuple[type[BaseSource], ...] = (DirectorySource,)
"""The kinds vitruvio ships.

One, on purpose. ``http``, ``playwright``, ``arxiv`` and ``youtube`` are each one class behind one extra once the
seam exists, and the seam is what this delivers -- a bundle of half-tested kinds would be a worse starting point
than an honest single one plus ``source scaffold``.
"""


@dataclass(frozen=True, slots=True)
class Kind:
    """
    One resolvable source kind and where it came from.

    Attributes:
        kind (str): The name ``vitruvio.toml`` selects it by.
        implementation (type[BaseSource]): The class.
        provenance (str): ``built-in``, ``plugin`` plus the file, or ``entry-point`` plus the distribution. Shown
            by ``source kinds`` because "which of my files is this" is the first question when a plugin misbehaves.
    """

    kind: str
    implementation: type[BaseSource]
    provenance: str


def kinds() -> dict[str, Kind]:
    """
    Every kind this installation can construct: built-in, then plugins, then entry points.

    Loading is lazy and per-command -- only ``source`` commands call this -- so a plugin that raises on import
    cannot break ``vitruvio brain state``. Precedence is deliberate: a plugin **overrides** a built-in of the same
    name, because the machine's owner is the one who put it there, and an entry point does not override a plugin,
    for the same reason.

    Returns:
        dict[str, Kind]: Kind name to its implementation and provenance.
    """
    found: dict[str, Kind] = {
        implementation.KIND: Kind(implementation.KIND, implementation, "built-in") for implementation in BUILTIN
    }
    for kind in _from_entry_points():
        found[kind.kind] = kind
    for kind in _from_plugin_files():
        found[kind.kind] = kind
    return found


def resolve_source(
    name: str,
    spec: SourceSpec,
    *,
    root: Path | None = None,
    cwd: Path | None = None,
) -> BaseSource:
    """
    Construct the source one declaration names.

    Args:
        name (str): The source's name in the project.
        spec (SourceSpec): Its declaration.
        root (Path | None): ``spec.path``, resolved against the configuration file.
        cwd (Path | None): Where a subprocess should run.

    Returns:
        BaseSource: The constructed source.

    Raises:
        SourceUnavailableError: If no kind by that name can be constructed. The message lists what *is* available,
            because the common cause is a typo or a plugin file that is not where it was thought to be.
    """
    available = kinds()
    kind = available.get(spec.kind)
    if kind is None:
        listed = ", ".join(sorted(available)) or "none"
        raise SourceUnavailableError(
            f"source {name!r} names kind {spec.kind!r}, which is not installed (available: {listed})",
            hint=f"run `vitruvio source kinds` to see where each one comes from, or `vitruvio source scaffold "
            f"{spec.kind}` to write one in {plugin_dir()}",
        )
    return kind.implementation(name=name, spec=spec, root=root, cwd=cwd)


def _from_entry_points() -> Iterator[Kind]:
    """Kinds registered by an installed distribution."""
    from importlib.metadata import entry_points

    for entry in entry_points(group=ENTRY_POINT_GROUP):
        try:
            loaded = entry.load()
        # Broad on purpose: a third-party module's import may raise anything at all.
        except Exception as error:
            raise SourceUnavailableError(
                f"the source plugin {entry.name!r} from {entry.value} would not import: {error!r}"
            ) from error
        for implementation in _subclasses(loaded):
            yield Kind(implementation.KIND, implementation, f"entry-point:{entry.name}")


def _from_plugin_files() -> Iterator[Kind]:
    """Kinds defined by a ``*.py`` under :func:`~vitruvio.kernel.plugin_dir`."""
    directory = plugin_dir()
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        for implementation in _subclasses(_import_file(path)):
            yield Kind(implementation.KIND, implementation, f"plugin:{path}")


def _import_file(path: Path) -> object:
    """
    Import one plugin file.

    Raises:
        SourceUnavailableError: If it will not import. Naming the file and the exception rather than letting a
            traceback out: the reader's next action is to edit that file, and a traceback through importlib buries
            which one it was.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"{MODULE_PREFIX}{path.stem}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - only for a path importlib cannot describe
        raise SourceUnavailableError(f"the source plugin {path} could not be loaded")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    # Broad on purpose: a hand-written plugin's import may raise anything at all.
    except Exception as error:
        raise SourceUnavailableError(
            f"the source plugin {path} would not import: {error!r}",
            hint=f"fix {path}, or move it out of {path.parent} to disable it",
        ) from error
    return module


def _subclasses(container: object) -> Iterable[type[BaseSource]]:
    """
    Every usable source class a loaded module or object offers.

    A ``KIND`` is what makes a class a kind rather than an abstract intermediate, so a subclass without one is
    skipped in silence -- that is how a plugin shares a base class between two of its own kinds.
    """
    if isinstance(container, type) and issubclass(container, BaseSource) and container.KIND:
        return [container]
    found = []
    for attribute in vars(container).values():
        if (
            isinstance(attribute, type)
            and issubclass(attribute, BaseSource)
            and attribute is not BaseSource
            and attribute.KIND
        ):
            found.append(attribute)
    return found


SCAFFOLD = '''"""A vitruvio source: {kind}.

Written by `vitruvio source scaffold {kind}`. vitruvio imports this file when a `source` command runs, from your
own configuration directory -- the same trust level as your shell profile. Nothing that arrives with a `git clone`
can add a kind here.

Declare it in vitruvio.toml:

    [sources.{kind}]
    kind = "{kind}"
    brain = "..."          # which brain this feeds; the declaration wins over --brain
    options = {{}}           # whatever your __init__ below reads
"""

from __future__ import annotations

from collections.abc import Sequence

from vitruvio.ingest.sources import BaseSource, FetchResult, Item


class {class_name}(BaseSource):
    """Acquire from {kind}."""

    KIND = "{kind}"

    @property
    def available(self) -> bool:
        """Whether this source can run right now. False is a report, not an error."""
        return True

    def list(self) -> Sequence[Item]:
        """Everything on offer, fetching nothing.

        `origin` is the dedup key: make it stable across runs, and strip anything incidental (a session token, a
        rotating query parameter) before returning it, because only you know which parts of your addresses are.

        Use `self.run([...])` for a subprocess and `self.get(url)` for HTTP -- both are bounded, and the bare calls
        are not. `self.options` holds the effective values: what vitruvio.toml declared, merged with any one-off
        `source pull --option key=value` overrides; `self.root` is `path`, already resolved against the
        configuration file. If an option changes which remote item an id names, include it in `origin` so the
        dedup key remains stable and unambiguous.
        """
        raise NotImplementedError("list the items this source offers")

    def fetch(self, item: Item) -> bytes | FetchResult:
        """The bytes of one item, or a FetchResult when the download reveals its MIME type or filename.

        Read a local file with `self.contain(path).read_bytes()` rather than `Path.read_bytes()`: it refuses a
        symlink, anything outside `self.root`, a FIFO (which would block forever), and an oversized file before the
        read rather than after.
        """
        raise NotImplementedError("fetch one item")
'''
"""The starter a scaffolded plugin is written from.

A worked file rather than a documentation section, because "inherit from BaseSource" leaves a reader to discover
`self.contain` and the stability requirement on `origin` the hard way -- and the hard way here is a directory's
worth of duplicate blocks.
"""


def scaffold(kind: str) -> str:
    """
    The source of a starter plugin for one kind.

    Args:
        kind (str): The kind name, as it will appear in ``vitruvio.toml``.

    Returns:
        str: Python source, ready to write into :func:`~vitruvio.kernel.plugin_dir`.
    """
    parts = [part for part in kind.replace("_", "-").split("-") if part]
    class_name = "".join(part[:1].upper() + part[1:] for part in parts) + "Source"
    return SCAFFOLD.format(kind=kind, class_name=class_name)


def describe() -> list[dict[str, object]]:
    """
    Every installed kind, for ``vitruvio source kinds``.

    Returns:
        list[dict[str, object]]: One record per kind, ordered by name.
    """
    return [
        {"kind": kind.kind, "provenance": kind.provenance, "class": kind.implementation.__qualname__}
        for kind in sorted(kinds().values(), key=lambda kind: kind.kind)
    ]
