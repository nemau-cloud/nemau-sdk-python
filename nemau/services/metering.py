"""/metering: consumption and accrued cost.

The responses do not follow a stable public schema, so these methods return
the decoded JSON as is. The organization always comes from the credential, and
a ``tenant_id`` argument is ignored.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Union

from nemau.services import Service

DateLike = Union[str, dt.datetime, None]


def _iso(value: DateLike) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        # A naive datetime is read as UTC, not as local time: the API expects
        # an absolute instant.
        moment = value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
        return moment.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return value


class MeteringService(Service):
    """Consumption and cost."""

    def usage(self, **query: Any) -> dict[str, Any]:
        return self._get("/metering/usage", query)

    def usage_summary(self, **query: Any) -> dict[str, Any]:
        return self._get("/metering/usage/summary", query)

    def costs(self, **query: Any) -> dict[str, Any]:
        return self._get("/metering/costs", query)

    def events(self, **query: Any) -> dict[str, Any]:
        return self._get("/metering/events", query)

    def _get(self, path: str, query: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if query.get("resource_type"):
            params["resource_type"] = query["resource_type"]
        start = _iso(query.get("period_start"))
        if start:
            params["period_start"] = start
        end = _iso(query.get("period_end"))
        if end:
            params["period_end"] = end
        if query.get("page"):
            params["page"] = query["page"]
        if query.get("page_size"):
            params["page_size"] = query["page_size"]
        payload = self._client.request("GET", path, query=params or None)
        return payload if isinstance(payload, dict) else {}
