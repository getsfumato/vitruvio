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
from contextlib import contextmanager
from dataclasses import dataclass

from boltzmann.exceptions import (
    ActorIdError,
    AuthenticityError,
    BlockIntegrityError,
    BlockNotFoundError,
    BlockTombstonedError,
    BoltzmannError,
    CatalogError,
    CommitError,
    DistributionError,
    DivergenceError,
    InclusionProofError,
    MembershipError,
    MemoryTypeError,
    NoCommonAncestorError,
    ProtocolError,
    QueryError,
    ReconciliationBlockedError,
    ReconciliationError,
    ReconciliationHaltedError,
    ReferenceNotFoundError,
    ResolutionRefusedError,
    RetentionPolicyError,
    RollbackError,
    SnapshotError,
    ValidationError,
)
from pydantic import ValidationError as PydanticValidationError

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
        PydanticValidationError,
        Report(
            "USAGE",
            ExitCode.USAGE,
            400,
            retryable=False,
            hint="repair the malformed fields in the supplied document and try again",
        ),
    ),
    (
        ActorIdError,
        Report(
            "ACTOR_ID_INVALID",
            ExitCode.CONFIG,
            400,
            retryable=False,
            hint="use a lowercase address such as alex@example.org or a namespaced name such as openai/codex",
        ),
    ),
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
        RollbackError,
        Report(
            "ROLLBACK_REFUSED",
            ExitCode.DIVERGED,
            409,
            retryable=False,
            hint=(
                "the served head is an ancestor of the held head; keep the newer history, or pass "
                "`vitruvio dist pull --allow-rollback` only when discarding it is deliberate"
            ),
        ),
    ),
    (
        # Before `DistributionError`, which it subclasses -- and the reason it needs its own row at all. Falling
        # through to the base reported a diverged push as `REGISTRY_FAILED`, exit 9, *retryable*: an agent told to
        # retry a transport hiccup, against a refusal that will be identical every time. It is the one distribution
        # failure with a defined remedy, and exit 8 exists to say so.
        DivergenceError,
        Report(
            "DIVERGED",
            ExitCode.DIVERGED,
            409,
            retryable=False,
            hint=(
                "someone published since this brain last pulled; `vitruvio dist fetch` brings their history "
                "and reconciles it. Never --force, which discards their version"
            ),
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
    (CatalogError, Report("CATALOG_INVALID", ExitCode.USAGE, 400, retryable=False)),
    (QueryError, Report("QUERY_FAILED", ExitCode.USAGE, 400, retryable=False)),
    (
        AuthenticityError,
        Report(
            "AUTHENTICITY_FAILED",
            ExitCode.PROTOCOL,
            422,
            retryable=False,
            hint="inspect `vitruvio auth status`; integrity and signature authority are independent verdicts",
        ),
    ),
    (
        # The three reconciliation rows precede `ProtocolError`, which they subclass.
        #
        # Halted is not a failure: it is the operation asking a question, and it arrives from two directions --
        # a reconciliation that just stopped on something that did not apply, and any ordinary write refused
        # because one is still open. One code covers both because the answer to both is the same command.
        #
        # The hint is rewritten rather than passed through. The SDK's message ends in `reconcile_abort()`, which is
        # a Python method on a class the user never sees; a person reading it has been handed the wrong vocabulary.
        ReconciliationHaltedError,
        Report(
            "RECONCILE_OPEN",
            ExitCode.RECONCILE,
            409,
            retryable=False,
            hint=(
                "`vitruvio reconcile status` lists what is open, `vitruvio reconcile resolve` decides it, "
                "and `vitruvio reconcile abort` abandons it -- nothing was written either way"
            ),
        ),
    ),
    (
        ReconciliationBlockedError,
        Report(
            "RECONCILE_BLOCKED",
            ExitCode.RECONCILE,
            409,
            retryable=False,
            hint=(
                "a candidate is still undecided, and committing would decide it on your behalf; "
                "`vitruvio reconcile status` names which"
            ),
        ),
    ),
    (
        NoCommonAncestorError,
        Report(
            "NO_COMMON_ANCESTOR",
            ExitCode.PROTOCOL,
            422,
            retryable=False,
            hint=(
                "the two histories share no ancestor, so a block missing from one side is ambiguous between "
                "'they added it' and 'I dropped it' -- reconciling on a guess is refused rather than attempted"
            ),
        ),
    ),
    (
        ResolutionRefusedError,
        Report(
            "RESOLUTION_REFUSED",
            ExitCode.RECONCILE,
            409,
            retryable=False,
            hint=(
                "that decision is not available for this verdict: `admit` is offered for a contradiction and "
                "never for a rejection, and `prefer` only where two histories replaced the same block. "
                "`vitruvio reconcile status` reports the verdict"
            ),
        ),
    ),
    (
        # The base, last of the reconciliation rows and still above `ProtocolError`. Without it every refusal
        # the SDK adds -- and the head-mismatch it already raises -- reported as `PROTOCOL_ERROR`, exit 5, HTTP
        # 500, which the exit-code reference documents as an integrity failure. A caller reading that goes
        # looking for corruption; the actual answer is usually `abort`.
        ReconciliationError,
        Report(
            "RECONCILE_FAILED",
            ExitCode.RECONCILE,
            409,
            retryable=False,
            hint="`vitruvio reconcile status` reports where it stands, and `abort` abandons it without writing",
        ),
    ),
    (ProtocolError, Report("PROTOCOL_ERROR", ExitCode.PROTOCOL, 500, retryable=False)),
    (BoltzmannError, Report("PROTOCOL_ERROR", ExitCode.PROTOCOL, 500, retryable=False)),
)

FALLBACK = Report("INTERNAL", ExitCode.INTERNAL, 500, retryable=False)
"""For anything not in the table. Reaching this means a bug in vitruvio, and it says so."""


_VOCABULARY = (
    ("reconcile_status()", "`vitruvio reconcile status`"),
    ("reconcile_resolve()", "`vitruvio reconcile resolve`"),
    ("reconcile_accept_removals()", "`vitruvio reconcile accept-removals`"),
    ("reconcile_continue()", "`vitruvio reconcile continue`"),
    ("reconcile_abort()", "`vitruvio reconcile abort`"),
    ("force=True", "--force"),
)
"""SDK API names that appear inside messages, and what they are called here.

:func:`translate` preserves the SDK's messages deliberately, and this is the one narrow exception. These
messages do not merely *mention* an API -- they end in an instruction built from it ("abandon it with
``reconcile_abort()``", "or pass ``force=True``"), which is sound advice written in a vocabulary the reader
does not have. Somebody driving the CLI has no `Brain` object to call a method on, and an agent will try.

A substitution rather than a rewritten message, because the rest of those sentences is the specific part: they
name the history being reconciled and what is unresolved about it, which is exactly what preserving the SDK's
wording is for. Only the noun for "how you do that" is wrong.

Found by reading the SDK's `raise` sites rather than by guessing: seven API tokens appear inside error
messages, and these are the ones a caller can actually reach. `rebuildable=False` is a field on a model nobody
here constructs by hand, so it would only ever be read by somebody looking at the SDK anyway.
"""


def _in_our_words(message: str) -> str:
    """
    Replace any SDK API name in a message with the command that does the same thing.

    Args:
        message (str): The SDK's message.

    Returns:
        str: The same message, in vocabulary the reader can act on.
    """
    for api, command in _VOCABULARY:
        message = message.replace(api, command)
    return message


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
        ExitCode.RECONCILE: 409,
    }.get(exit_code, 500)


def translate(error: BaseException) -> VitruvioError:
    """
    Wrap any exception as the error type every vitruvio interface reports.

    Preserves the original message rather than replacing it: the SDK's messages are specific and carry the
    identities involved, and a wrapper that says "protocol error" instead would be throwing away the only part
    a user can act on.

    The one thing it does change is vocabulary. A message that tells the reader to call a Python method is
    telling them to do something they cannot do from here -- see :data:`_VOCABULARY`.

    Args:
        error (BaseException): What was raised.

    Returns:
        VitruvioError: The same failure, carrying a code, an exit status and a hint.
    """
    if isinstance(error, VitruvioError):
        return error

    report = report_for(error)
    wrapped = VitruvioError(_in_our_words(str(error)) or type(error).__name__, hint=report.hint)
    # Set on the instance rather than by subclassing: the table is the single declaration of these values, and
    # a parallel hierarchy of thirteen exception classes mirroring it would be a second place to keep in sync.
    wrapped.code = report.code
    wrapped.exit_code = report.exit_code
    return wrapped


@contextmanager
def translated() -> Iterator[None]:
    """
    Re-raise anything the SDK throws as the error type every interface reports.

    The boundary itself, wrapped around every call into the SDK. It lives beside :func:`translate` because it is
    the only thing that calls it: a caller that needs the conversion needs it as a scope, not as a function.

    A ``VitruvioError`` passes through untouched. Something already carrying a code and an exit status has been
    through the table once, and translating it again would replace a specific failure with the generic reading of
    whatever type it happens to be.
    """
    try:
        yield
    except VitruvioError:
        raise
    except Exception as error:
        raise translate(error) from error


def known_codes() -> Iterator[str]:
    """Every code this table can produce, for documentation and for the skills' error table."""
    seen: set[str] = set()
    for _, report in (*_TABLE, (BaseException, FALLBACK)):
        if report.code not in seen:
            seen.add(report.code)
            yield report.code
