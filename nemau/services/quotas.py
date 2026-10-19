"""The capacity limits a write is checked against.

A write is accepted only when both the project the credential resolves to and
its organization have room, so both scopes are reported.
"""

from __future__ import annotations

from nemau.models import QuotaList
from nemau.services import Service


class QuotasService(Service):
    """Quota reads."""

    def get(self) -> QuotaList:
        """Limits, settled usage and pending reservations for both scopes."""
        payload = self._client.request("GET", "/orchestration/quotas")
        return QuotaList.from_dict(payload or {})
