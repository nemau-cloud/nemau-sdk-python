"""API services, grouped the way the paths they call are grouped."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from nemau.client import NemauClient


class Service:
    """Shared base: holds the client reference."""

    __slots__ = ("_client",)

    def __init__(self, client: NemauClient) -> None:
        self._client = client
