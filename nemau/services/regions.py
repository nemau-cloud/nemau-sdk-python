"""/regions.

The only endpoints that do not require authentication. A client without a
credential can list regions, for example to check that the API is reachable.
"""

from __future__ import annotations

import builtins
import urllib.parse

from nemau.models import Region, RegionStatus
from nemau.services import Service


class RegionsService(Service):
    """Deployment regions."""

    def list(self) -> builtins.list[Region]:
        payload = self._client.request("GET", "/regions")
        return Region.list_from(payload)

    def status(self, slug: str) -> RegionStatus:
        """An unhealthy region answers 503, which surfaces as a
        :class:`~nemau.errors.NemauApiError` rather than a :class:`RegionStatus`."""
        payload = self._client.request(
            "GET", f"/regions/{urllib.parse.quote(slug, safe='')}/status"
        )
        return RegionStatus.from_dict(payload or {})
