"""The error model every vitruvio interface reports through.

An error here carries three things beyond its message, because three different consumers ask three
different questions of the same failure:

* ``code`` -- a stable string an agent branches on. Messages are prose and will be reworded; a code is a
  contract.
* ``exit_code`` -- what the CLI returns. The distinction that matters to a caller is not which subsystem
  failed but whether retrying could ever help: :data:`ExitCode.USAGE` means "you asked wrong, rephrase",
  :data:`ExitCode.PROTOCOL` and :data:`ExitCode.POLICY` mean "the protocol says no, do not retry".
* ``hint`` -- the next action, when there is one. An error that names the fix is the difference between a
  tool someone can drive and a tool someone has to read the source of.

The mapping from the SDK's own exceptions into this model lives in ``vitruvio.runtime.mapping``, not here:
the kernel must stay installable and importable without the rest of the runtime.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Process exit statuses, named so that no command invents its own.

    The numbers are part of the CLI's contract with whatever is driving it, so they are append-only:
    a value's meaning may be clarified but never reassigned.
    """

    OK = 0
    INTERNAL = 1
    """An unexpected failure. Always a bug in vitruvio."""
    USAGE = 2
    """The invocation was wrong: an unknown flag, a malformed value."""
    CONFIG = 3
    """No brain selected, or the configuration is invalid."""
    NOT_FOUND = 4
    """A block, module, tag or index that was named does not exist."""
    PROTOCOL = 5
    """The protocol refused: failed verification, broken membership, corrupted bytes."""
    POLICY = 6
    """The brain's retention policy forbids the operation."""
    VALIDATION = 7
    """Candidate blocks were rejected by the validation gate."""
    DIVERGED = 8
    """A push would not be a fast-forward: the histories diverged."""
    REGISTRY = 9
    """The registry could not be reached, or refused the credentials."""
    REVIEW = 10
    """A cascade is large enough that the policy requires human review."""
    SOURCE = 11
    """A declared source could not be reached, or refused.

    Its own code rather than ``REGISTRY``, whose docstring scopes it to publishing, and rather than ``CONFIG``,
    which means the declaration itself is wrong. What a caller does about the three differs: wait and retry, fix
    a credential, edit a file. Collapsing them would make an agent guess."""


class VitruvioError(Exception):
    """
    The base of every error vitruvio raises deliberately.

    Attributes:
        code (str): A stable, machine-readable identifier for this failure.
        exit_code (ExitCode): What the CLI should return.
        hint (str | None): The next action a caller could take, when one exists.
    """

    code = "INTERNAL"
    exit_code = ExitCode.INTERNAL

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        """
        Build an error.

        Args:
            message (str): What went wrong, in one sentence.
            hint (str | None): What to do about it.
        """
        super().__init__(message)
        self.message = message
        self.hint = hint


class ConfigError(VitruvioError):
    """The configuration is missing, malformed, or contradicts itself."""

    code = "CONFIG_INVALID"
    exit_code = ExitCode.CONFIG


class BrainNotSelectedError(ConfigError):
    """No brain could be determined from flags, environment, file, or state."""

    code = "NO_BRAIN"


class BrainNotFoundError(ConfigError):
    """A brain was named but the path holds no OCI layout."""

    code = "BRAIN_NOT_FOUND"
    exit_code = ExitCode.NOT_FOUND


class ActorUnknownError(ConfigError):
    """No actor identity could be resolved, so a write would record unattributed provenance."""

    code = "ACTOR_UNKNOWN"


class UsageError(VitruvioError):
    """The invocation contradicts itself, or names something that does not exist.

    Exists because the alternative was worse. cyclopts produces :attr:`ExitCode.USAGE` for a malformed command
    line, but a *semantic* usage error -- two mutually exclusive flags, a name the project does not know -- is
    raised by our own code, and a bare ``VitruvioError`` reports it as exit 1, which this enum documents as "always
    a bug in vitruvio". Telling a user their typo is our bug costs them a real investigation.
    """

    code = "USAGE"
    exit_code = ExitCode.USAGE


class CandidatesRejectedError(VitruvioError):
    """The validation gate rejected at least one candidate, so nothing was committed.

    Its own type, and exit 7, because of what it tells an automated caller: the brain is fine, the request was fine,
    and the *proposal* was wrong. That means repair the payloads and come back -- which is a different response from
    every other failure, and the only one where retrying with the same input is guaranteed to fail identically.
    """

    code = "CANDIDATES_REJECTED"
    exit_code = ExitCode.VALIDATION


class SourceError(VitruvioError):
    """A declared source could not be reached, listed, or fetched from.

    The world was uncooperative: a command exited non-zero, a host was down, a directory vanished. Distinct from
    :class:`SourceUnavailableError`, which means the *declaration* cannot be satisfied by this installation --
    the difference between "try again later" and "nothing will change until you edit something".
    """

    code = "SOURCE_FAILED"
    exit_code = ExitCode.SOURCE


class SourceUnavailableError(ConfigError):
    """A source names a kind this installation cannot construct: unknown, or a plugin that will not import.

    A ``ConfigError`` deliberately, and this is the one place worth contrasting with an existing choice:
    ``EmbedderUnavailableError`` is a bare ``Exception``, so a missing extra currently falls through the mapping
    table to ``INTERNAL`` and reports an uninstalled dependency as "a bug in vitruvio". A declaration naming
    something absent is a configuration problem, and it should say so.
    """

    code = "SOURCE_UNKNOWN"


class CredentialError(VitruvioError):
    """A registry credential is missing, unreadable, or was refused."""

    code = "CREDENTIAL_MISSING"
    exit_code = ExitCode.REGISTRY
