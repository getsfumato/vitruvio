"""Which branch a brain is on, and the tag that makes the default for a push or a pull.

Read from the ``refs`` pointer directly rather than through an opened brain, because every distribution operation
needs the answer before it opens anything: the default tag decides what the registry is asked for. Opening a brain
to learn it would cost a pointer read *and* an index rebuild on a command that has not decided to do anything yet.

The default branch is the one exception to "the branch's own tag". ``main`` publishes to ``[registry].tag``, which
is how a project says which tag it treats as current; the SDK's own default for it is ``latest``, and a project that
configured something else must not have its pushes quietly moved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vitruvio.kernel import ResolvedConfig


@dataclass(frozen=True, slots=True)
class BranchTag:
    """The current branch, and the tag a push or pull defaults to on it."""

    branch: str
    tag: str


def current_branch_tag(config: ResolvedConfig) -> BranchTag:
    """
    The branch the brain is on and the tag it publishes to.

    A brain with no ref table -- every brain until somebody creates a branch -- is on ``main`` and publishes to
    ``[registry].tag``, exactly as before branches existed.

    Args:
        config (ResolvedConfig): Which brain, and the project's registry tag.

    Returns:
        BranchTag: The branch and its tag.
    """
    from boltzmann.brain import HEAD_POINTER, REFS_POINTER, BrainState, RefTable
    from boltzmann.branches import DEFAULT_BRANCH, tag_for
    from boltzmann.exceptions import BoltzmannError
    from boltzmann.store.oci_layout import OciLayoutStore
    from pydantic import ValidationError

    default = BranchTag(DEFAULT_BRANCH, config.project.registry.tag)
    try:
        store = OciLayoutStore(config.brain, create=False)
        raw = store.read_pointer(REFS_POINTER)
        head = store.read_pointer(HEAD_POINTER)
    except (BoltzmannError, OSError):
        return default  # No layout yet: `init` or a first `pull` is what comes next, on the default branch.
    if not raw:
        return default
    try:
        table = RefTable.model_validate_json(raw)
        state = BrainState.model_validate_json(head) if head else None
    except ValidationError:
        # Not ours to judge here. Opening the brain reports an unreadable pointer properly; a default-tag lookup
        # that raised instead would turn every distribution command into that report.
        return default
    current = table.current
    if table.switching is not None:
        # A checkout interrupted between moving the head and recording that it had. The SDK finishes it on its next
        # read when the head already names the target, and abandons it otherwise; this reads it the same way, because
        # choosing the tag from the stale `current` would publish one branch's head over the other's tag.
        target = table.branches.get(table.switching)
        if target is not None and state is not None and state.snapshot == target.snapshot:
            current = table.switching
    if current == DEFAULT_BRANCH:
        return default
    ref = table.branches.get(current)
    return BranchTag(current, ref.tag if ref is not None else tag_for(current))


def align_default_branch(brain: Any, config: ResolvedConfig) -> None:
    """
    Record ``[registry].tag`` as main's tag in the brain's refs, when they say something else.

    The SDK names main's tag ``latest``, or the tag the brain was last pulled from, the moment the first branch is
    created. A project that publishes main to ``stable`` then holds a ref table claiming ``latest`` -- and the SDK
    reads tags back through that table, so a later ``dist pull --tag stable`` is not recognised as main's and installs
    into whichever branch is current. Aligned here, under the session's writer, before anything reads it.

    Args:
        brain (Any): The opened WRITE brain, inside ``session.write``.
        config (ResolvedConfig): The project, for ``[registry].tag``.
    """
    from boltzmann.brain import REFS_POINTER, RefTable
    from boltzmann.branches import DEFAULT_BRANCH
    from boltzmann.identity.serialization import canonicalize

    raw = brain.store.read_pointer(REFS_POINTER)
    if not raw:
        return  # No ref table: main's tag is whatever the command passes, which is already `[registry].tag`.
    table = RefTable.model_validate_json(raw)
    main = table.branches.get(DEFAULT_BRANCH)
    wanted = config.project.registry.tag
    if main is None or main.tag == wanted:
        return
    aligned = table.model_copy(
        update={"branches": {**table.branches, DEFAULT_BRANCH: main.model_copy(update={"tag": wanted})}}
    )
    brain.store.write_pointer(REFS_POINTER, canonicalize(aligned.model_dump(mode="json", exclude_none=True)))


def tag_for_branch(config: ResolvedConfig, name: str) -> str:
    """
    The tag a named branch publishes to, under this project's registry tag.

    Args:
        config (ResolvedConfig): The project, for ``[registry].tag``.
        name (str): The branch.

    Returns:
        str: ``[registry].tag`` for ``main``, otherwise ``br.`` and the encoded name.
    """
    from boltzmann.branches import DEFAULT_BRANCH, tag_for

    return config.project.registry.tag if name == DEFAULT_BRANCH else tag_for(name)


__all__ = ["BranchTag", "align_default_branch", "current_branch_tag", "tag_for_branch"]
