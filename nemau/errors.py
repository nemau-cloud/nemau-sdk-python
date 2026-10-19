"""Nemau Cloud SDK errors."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from nemau.models import (
    ERROR_CODE_CREDENTIAL_LIMIT,
    ERROR_CODE_ORGANIZATION_READ_ONLY,
    ERROR_CODE_RESOURCE_IN_USE,
)

if TYPE_CHECKING:  # pragma: no cover - circular import at runtime only
    from nemau.models import Task

#: How much of an error body is kept. A misrouted request can return a whole
#: HTML page, and its beginning is enough to identify it.
MAX_ERROR_BODY = 4096


class NemauError(Exception):
    """Base of every SDK error."""


class NemauApiError(NemauError):
    """A non-2xx response from the API.

    ``message`` is the error text returned by the API. Client errors usually
    carry a specific message ("project quota exceeded for instances: 10/10 in
    use"); other errors carry a generic one. The message is safe to display.
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        method: str,
        path: str,
        reason: str = "",
        code: str = "",
        limit: int = 0,
        scope: str = "",
        body: str = "",
    ) -> None:
        text = f"{method} {path}: {status_code} {message}"
        if reason:
            text += f" ({reason})"
        super().__init__(text)
        self.status_code = status_code
        self.message = message
        #: Machine-readable code some refusals carry: "permission_denied" on
        #: authorisation failures, :data:`REASON_SUBNET_OVERLAP` on a subnet
        #: whose range is taken. Empty otherwise.
        self.reason = reason
        #: Stable, machine-readable cause some refusals carry
        #: ("credential_limit", "organization_read_only", "invalid_request"),
        #: empty otherwise. Branch on it, never on the message.
        self.code = code
        #: Qualify a "credential_limit" refusal: the cap that was reached (0 when
        #: the server did not say) and what it counts ("bucket" or "project",
        #: empty when absent).
        self.limit = limit
        self.scope = scope
        #: Truncated raw body, useful when a proxy answered with HTML.
        self.body = body
        self.method = method
        self.path = path
        #: Seconds the server asked to wait (``Retry-After`` on a 429 or 503),
        #: None when it did not say.
        self.retry_after: float | None = None

    @property
    def retryable(self) -> bool:
        """A further attempt could reasonably succeed."""
        return self.status_code == 429 or self.status_code >= 500

    @property
    def not_found(self) -> bool:
        return self.status_code == 404

    @property
    def unauthorized(self) -> bool:
        return self.status_code == 401

    @property
    def forbidden(self) -> bool:
        """Valid credential, not authorised.

        On this API that means an IAM policy, a token scope, or a missing
        organization feature.
        """
        return self.status_code == 403


class NemauTaskFailedError(NemauError):
    """A task reached a terminal, unsuccessful state.

    The submitted work did not happen. The API replaces internal error details
    with a generic message, so the exception message includes the task id:
    support needs it to investigate the failure.
    """

    def __init__(self, task: Task) -> None:
        detail = f": {task.error}" if task.error else ""
        super().__init__(f"task {task.id} ({task.task_type}) {task.status}{detail}")
        self.task = task


#: The ``reason`` of the 409 refusing a subnet whose CIDR overlaps another
#: subnet of the same project. All the subnets of a project are routed
#: together, whichever network they belong to, so the conflict may sit on
#: another network; the message names the subnet, its range and its network.
REASON_SUBNET_OVERLAP = "subnet_overlap"


def is_subnet_overlap(error: BaseException) -> bool:
    """The API refused a subnet because its range overlaps another subnet of
    the project.

    It matches the refusal of the creation request itself, which is where an
    overlap is caught. A conflicting subnet created by someone else between
    that request and the task running fails the task instead: that surfaces as
    a :class:`NemauTaskFailedError` carrying the same message, without the
    reason.
    """
    return isinstance(error, NemauApiError) and error.reason == REASON_SUBNET_OVERLAP


#: The ``reason`` of the 409 refusing to delete a certificate a listener still
#: serves. Other certificate refusals carry a ``certificate_*`` reason too.
REASON_CERTIFICATE_IN_USE = "certificate_in_use"


def is_certificate_in_use(error: BaseException) -> bool:
    """The API refused to delete a certificate because a listener serves it."""
    return isinstance(error, NemauApiError) and error.reason == REASON_CERTIFICATE_IN_USE


def is_resource_in_use(error: BaseException) -> bool:
    """Whether the platform refused the operation because a dependant still uses
    the resource. The error message names what to remove first."""
    return (
        isinstance(error, NemauTaskFailedError)
        and error.task.error_code == ERROR_CODE_RESOURCE_IN_USE
    )


def is_credential_limit(error: BaseException) -> bool:
    """The container already holds the maximum number of access keys (429).

    Revoke an unused key, then retry; repeating the same call cannot succeed.
    """
    return isinstance(error, NemauApiError) and error.code == ERROR_CODE_CREDENTIAL_LIMIT


#: The ``error`` of the 422 refusing an ``Idempotency-Key`` already used with
#: other parameters.
IDEMPOTENCY_KEY_MISMATCH = "idempotency_key_mismatch"

#: The ``reason`` of the 409 refusing an IAM access key beyond the per-user cap.
REASON_ACCESS_KEY_LIMIT = "access_key_limit"


def is_idempotency_key_mismatch(error: BaseException) -> bool:
    """The ``Idempotency-Key`` was already used with different parameters (422).

    Not retryable: pick a new key for the new request, or resend the original
    parameters to get the original task back.
    """
    return (
        isinstance(error, NemauApiError)
        and error.status_code == 422
        and error.message == IDEMPOTENCY_KEY_MISMATCH
    )


def is_rate_limited(error: BaseException) -> bool:
    """The write budget is spent (429). ``error.retry_after`` says when to try
    again."""
    return isinstance(error, NemauApiError) and error.status_code == 429


def is_access_key_limit(error: BaseException) -> bool:
    """The user already holds the maximum number of active IAM access keys (409).

    Revoke an unused key first; repeating the call cannot succeed.
    """
    return (
        isinstance(error, NemauApiError)
        and error.status_code == 409
        and error.reason == REASON_ACCESS_KEY_LIMIT
    )


def is_organization_read_only(error: BaseException) -> bool:
    """The organization is in its read-only phase (403)."""
    return isinstance(error, NemauApiError) and error.code == ERROR_CODE_ORGANIZATION_READ_ONLY


def is_not_found(error: BaseException) -> bool:
    return isinstance(error, NemauApiError) and error.not_found


def is_unauthorized(error: BaseException) -> bool:
    return isinstance(error, NemauApiError) and error.unauthorized


def is_forbidden(error: BaseException) -> bool:
    return isinstance(error, NemauApiError) and error.forbidden


def has_reason(error: BaseException, reason: str) -> bool:
    """Whether ``error`` is an API refusal carrying the given reason code.

    Match on the machine-readable code (``"dns_zone_reserved"``) rather than on
    the message, whose wording can change.
    """
    return bool(reason) and isinstance(error, NemauApiError) and error.reason == reason


def api_error_from_body(status_code: int, raw: str, *, method: str, path: str) -> NemauApiError:
    """Builds the error for a non-2xx response from its body."""
    raw = raw[:MAX_ERROR_BODY]
    message = ""
    reason = ""
    code = ""
    limit = 0
    scope = ""
    try:
        payload: Any = json.loads(raw)
        if isinstance(payload, dict):
            message = str(payload.get("error") or payload.get("message") or "")
            reason = str(payload.get("reason") or "")
            code = payload["code"] if isinstance(payload.get("code"), str) else ""
            raw_limit = payload.get("limit")
            if isinstance(raw_limit, int) and not isinstance(raw_limit, bool) and raw_limit > 0:
                limit = raw_limit
            scope = payload["scope"] if isinstance(payload.get("scope"), str) else ""
    except ValueError:
        message = raw.strip()
    if not message:
        message = raw.strip() or str(status_code)
    return NemauApiError(
        status_code,
        message,
        method=method,
        path=path,
        reason=reason,
        code=code,
        limit=limit,
        scope=scope,
        body=raw,
    )


def not_found(kind: str, identifier: str, path: str) -> NemauApiError:
    """The 404 synthesised when a list-and-filter read finds nothing.

    It is a real :class:`NemauApiError`, so :func:`is_not_found` treats it
    exactly like a 404 returned by the server.
    """
    return NemauApiError(
        404,
        f'{kind} "{identifier}" not found in this project',
        method="GET",
        path=path,
    )
