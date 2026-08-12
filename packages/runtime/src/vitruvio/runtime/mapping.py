"""One table from an SDK exception to how each interface should report it.

The CLI needs an exit code, an MCP server needs a stable code and whether retrying could help, an HTTP API
needs a status. Deriving those three separately from the same exception is three chances to disagree about
whether a failure was the caller's fault.

The interesting column is ``retryable``, because it is the one a caller acts on and the one that is easiest to
get wrong. A registry timeout is retryable. A retention policy refusing a canonical drop is *not*: the answer
will be the same every time, and an agent that retries it is an agent in a loop. Neither is "an error", flatly:
the distinction is the whole point.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from boltzmann.exceptions import (
    BlockIntegrityError,
    BlockNotFoundError,
    BlockTombstonedError,
    BoltzmannError,
    CommitError,
    DistributionError,
    InclusionProofError,
    MembershipError,
    MemoryTypeError,
    ProtocolError,
    QueryError,
    ReferenceNotFoundError,
    RetentionPolicyError,
    SnapshotError,
    ValidationError,
)

from vitruvio.kernel import ExitCode, VitruvioError


@dataclass(frozen=True)
class Report:
    """
    How one failure should be reported, in every interface at once.

    Attributes:
        code (str): Stable, machine-readable identifier. A message is prose and will be reworded; this is the
            contract.
        exit_code (ExitCode): What the CLI returns.
        http_status (int): What an HTTP API would answer.
        retryable (bool): Whether retrying the same request could ever succeed.
        hint (str | None): The next action, when there is one.
    """

    code: str
    exit_code: ExitCode
    http_status: int
    retryable: bool
    hint: str | None = None


# Ordered most specific first: the lookup walks it and takes the first match, so a subclass must precede its
# base. `BoltzmannError` is last, as the catch-all that guarantees no SDK exception escapes unmapped.
_TABLE: tuple[tuple[type[BaseException], Report], ...] = (
    (
        RetentionPolicyError,
        Report(
            "POLICY_REFUSED",
            ExitCode.POLICY,
            403,
            retryable=False,
            hint=(
                "the brain's retention policy forbids this; change [policy] in vitruvio.toml deliberately, "
                "or use supersede/demote, which change accessibility rather than membership"
            ),
        ),
    ),
    (
        ValidationError,
        Report(
            "VALIDATION_REJECTED",
            ExitCode.VALIDATION,
            422,
            retryable=True,
            hint="read the per-candidate code in the report, repair the candidates, and validate again",
        ),
    ),
    (CommitError, Report("COMMIT_FAILED", ExitCode.VALIDATION, 409, retryable=False)),
    (
        BlockTombstonedError,
        Report(
            "BLOCK_TOMBSTONED",
            ExitCode.NOT_FOUND,
            410,
            retryable=False,
            hint="the block is still a verifiable member of the version; its bytes were destroyed under an erasure policy",
        ),
    ),
    (BlockNotFoundError, Report("BLOCK_NOT_FOUND", ExitCode.NOT_FOUND, 404, retryable=False)),
    (
        BlockIntegrityError,
        Report(
            "INTEGRITY_FAILED",
            ExitCode.PROTOCOL,
            500,
            retryable=False,
            hint="the stored bytes do not hash to the identity they are filed under; this brain is corrupt",
        ),
    ),
    (
        MembershipError,
        Report(
            "MEMBERSHIP_FAILED",
            ExitCode.PROTOCOL,
            500,
            retryable=False,
            hint="a block did not prove into the installed snapshot; run `vitruvio brain verify`",
        ),
    ),
    (InclusionProofError, Report("PROOF_FAILED", ExitCode.PROTOCOL, 500, retryable=False)),
    (SnapshotError, Report("SNAPSHOT_INVALID", ExitCode.PROTOCOL, 500, retryable=False)),
    (MemoryTypeError, Report("MEMORY_TYPE_INVALID", ExitCode.USAGE, 400, retryable=False)),
    (
        ReferenceNotFoundError,
        Report(
            "REFERENCE_NOT_FOUND",
            ExitCode.NOT_FOUND,
            404,
            retryable=False,
            hint="the registry holds no artifact under that reference and tag; a first push is expected to see this",
        ),
    ),
    (
        DistributionError,
        Report(
            "REGISTRY_FAILED",
            ExitCode.REGISTRY,
            502,
            retryable=True,
            hint="run `vitruvio registry check <reference>` to test reachability, credentials and media-type support",
        ),
    ),
    (QueryError, Report("QUERY_FAILED", ExitCode.USAGE, 400, retryable=False)),
    (ProtocolError, Report("PROTOCOL_ERROR", ExitCode.PROTOCOL, 500, retryable=False)),
    (BoltzmannError, Report("PROTOCOL_ERROR", ExitCode.PROTOCOL, 500, retryable=False)),
)

FALLBACK = Report("INTERNAL", ExitCode.INTERNAL, 500, retryable=False)
"""For anything not in the table. Reaching this means a bug in vitruvio, and it says so."""


def report_for(error: BaseException) -> Report:
    """
    How to report one failure.

    Args:
        error (BaseException): What was raised.

    Returns:
        Report: The code, exit status, HTTP status and retryability.
    """
    if isinstance(error, VitruvioError):
        return Report(error.code, error.exit_code, _http_for(error.exit_code), retryable=False, hint=error.hint)
    for kind, report in _TABLE:
        if isinstance(error, kind):
            return report
    return FALLBACK


def _http_for(exit_code: ExitCode) -> int:
    """The HTTP status a vitruvio-native error corresponds to."""
    return {
        ExitCode.OK: 200,
        ExitCode.USAGE: 400,
        ExitCode.CONFIG: 400,
        ExitCode.NOT_FOUND: 404,
        ExitCode.PROTOCOL: 500,
        ExitCode.POLICY: 403,
        ExitCode.VALIDATION: 422,
        ExitCode.DIVERGED: 409,
        ExitCode.REGISTRY: 502,
        ExitCode.REVIEW: 409,
        ExitCode.SOURCE: 502,
        # A source is upstream of vitruvio exactly as a registry is, so its unreachability is a bad gateway and
        # not the 500 the .get() fallback would report. An HTTP client retries a 502 and pages a human for a 500.
    }.get(exit_code, 500)


def translate(error: BaseException) -> VitruvioError:
    """
    Wrap any exception as the error type every vitruvio interface reports.

    Preserves the original message rather than replacing it: the SDK's messages are specific and carry the
    identities involved, and a wrapper that says "protocol error" instead would be throwing away the only part
    a user can act on.

    Args:
        error (BaseException): What was raised.

    Returns:
        VitruvioError: The same failure, carrying a code, an exit status and a hint.
    """
    if isinstance(error, VitruvioError):
        return error

    report = report_for(error)
    translated = VitruvioError(str(error) or type(error).__name__, hint=report.hint)
    # Set on the instance rather than by subclassing: the table is the single declaration of these values, and
    # a parallel hierarchy of thirteen exception classes mirroring it would be a second place to keep in sync.
    translated.code = report.code
    translated.exit_code = report.exit_code
    return translated


def known_codes() -> Iterator[str]:
    """Every code this table can produce, for documentation and for the skills' error table."""
    seen: set[str] = set()
    for _, report in (*_TABLE, (BaseException, FALLBACK)):
        if report.code not in seen:
            seen.add(report.code)
            yield report.code
