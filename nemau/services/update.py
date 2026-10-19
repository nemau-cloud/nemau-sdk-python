"""The mutable-field updates, shared by every service that has one.

A PATCH carries only the fields that change: passing ``description="prod"``
leaves the name unchanged, so a caller working from a stale copy cannot
overwrite a field it did not pass.

The update methods take keyword arguments, and an omitted argument is left out
of the body. To clear a field, pass an empty value: ``description=""``.
"""

from __future__ import annotations

from typing import Any

from nemau.models import TaskRef

#: The fields the API accepts on a PATCH, with the JSON name each maps to.
#: One list for every service; :func:`update_body` refuses any other field.
UPDATE_FIELDS = (
    "name",
    "description",
    # Load balancer family.
    "lb_algorithm",
    "connection_limit",
    "weight",
    "delay",
    "timeout",
    "max_retries",
    "expected_codes",
    "tls_certificate_ref",
    # Object containers.
    "versioning",
    "retention_days",
    # DNS zones.
    "email",
    "ttl",
)


def update_body(method: str, **fields: Any) -> dict[str, Any]:
    """Builds a PATCH body from the arguments that were actually passed.

    ``None`` means "not given" and is dropped; every other value is sent,
    including the empty string, which is how a field gets cleared.
    """
    body = {name: value for name, value in fields.items() if value is not None}
    if not body:
        raise ValueError(f"nemau: {method} was given no field to change")
    unknown = set(body) - set(UPDATE_FIELDS)
    if unknown:
        raise ValueError(f"nemau: {method} cannot change {', '.join(sorted(unknown))}")
    return body


def patch(
    client: Any,
    method: str,
    path: str,
    fields: dict[str, Any],
    query: dict[str, Any] | None = None,
) -> TaskRef:
    """Issues the PATCH behind every update method."""
    ref: TaskRef = client.submit("PATCH", path, query=query, json=update_body(method, **fields))
    return ref
