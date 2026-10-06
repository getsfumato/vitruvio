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
    from boltzmann.brain import REFS_POINTER, RefTable
    from boltzmann.branches import DEFAULT_BRANCH, tag_for
    from boltzmann.exceptions import BoltzmannError
    from boltzmann.store.oci_layout import OciLayoutStore
    from pydantic import ValidationError

    default = BranchTag(DEFAULT_BRANCH, config.project.registry.tag)
    try:
        raw = OciLayoutStore(config.brain, create=False).read_pointer(REFS_POINTER)
    except (BoltzmannError, OSError):
        return default  # No layout yet: `init` or a first `pull` is what comes next, on the default branch.
    if not raw:
        return default
    try:
        table = RefTable.model_validate_json(raw)
    except ValidationError:
        # Not ours to judge here. Opening the brain reports an unreadable pointer properly; a default-tag lookup
        # that raised instead would turn every distribution command into that report.
        return default
    if table.current == DEFAULT_BRANCH:
        return default
    ref = table.branches.get(table.current)
    return BranchTag(table.current, ref.tag if ref is not None else tag_for(table.current))


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


__all__ = ["BranchTag", "current_branch_tag", "tag_for_branch"]
