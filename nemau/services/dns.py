"""/dns: authoritative zones and their record sets."""

from __future__ import annotations

import urllib.parse
from collections.abc import Sequence
from typing import Any

from nemau.client import _unwrap
from nemau.errors import not_found
from nemau.models import DNSRecordSet, DNSZone, TaskRef
from nemau.services import Service
from nemau.services.update import patch

#: Zone statuses. A zone is created pending until its ownership is proven.
DNS_ZONE_STATUS_PENDING_VERIFICATION = "PENDING_VERIFICATION"
DNS_ZONE_STATUS_ACTIVE = "ACTIVE"

#: 400: the name is under a domain the platform keeps (nemau-cloud.fr,
#: nemau-cloud.com).
REASON_DNS_ZONE_RESERVED = "dns_zone_reserved"
#: 409: the zone is already active on the platform, or already claimed in
#: this project.
REASON_DNS_ZONE_EXISTS = "dns_zone_exists"
#: 409: :meth:`DNSService.verify_zone` on a zone that is already active.
REASON_DNS_ZONE_ALREADY_VERIFIED = "dns_zone_already_verified"
#: 409: a record-set write on a zone whose ownership is not proven yet.
REASON_DNS_ZONE_PENDING_VERIFICATION = "dns_zone_pending_verification"


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class DNSService(Service):
    """Zones and records."""

    def list_zones(self) -> list[DNSZone]:
        payload = self._client.request("GET", "/dns/zones")
        return [DNSZone.from_dict(item) for item in _unwrap(payload, "zones")]

    def get_zone(self, zone_id: str) -> DNSZone:
        """The API has no per-zone read: this lists and filters."""
        for item in self.list_zones():
            if item.id == zone_id:
                return item
        raise not_found("dns zone", zone_id, "/dns/zones")

    def create_zone(self, *, name: str, email: str, description: str = "", ttl: int = 0) -> TaskRef:
        """``email`` is the SOA contact. A zero ``ttl`` becomes 300 server-side.

        The zone comes back pending, with the TXT record that proves ownership
        of the domain in ``verification``; see :meth:`verify_zone`. A reserved
        name is refused with :data:`REASON_DNS_ZONE_RESERVED`, an existing one
        with :data:`REASON_DNS_ZONE_EXISTS`.
        """
        body: dict[str, Any] = {"name": name, "email": email}
        if description:
            body["description"] = description
        if ttl:
            body["ttl"] = ttl
        return self._client.submit("POST", "/dns/zones", json=body)

    def create_zone_and_wait(self, **kwargs: Any) -> DNSZone:
        """Returns the created zone, still pending: publish its record, then
        :meth:`verify_zone_and_wait`."""
        ref = self.create_zone(**kwargs)
        zone_id, _ = self._client.wait_for_resource_id(ref, "zone_id")
        return self.get_zone(zone_id)

    def verify_zone(self, zone_id: str) -> TaskRef:
        """Queues the ownership check of a pending zone.

        The platform looks up the TXT record named in ``verification`` and
        activates the zone if it holds the expected value. The task fails, and
        the zone stays pending, while the record is absent or wrong. A record
        published a moment ago may not have reached the domain's nameservers
        yet, so the check may need another try. A zone already active is
        refused with :data:`REASON_DNS_ZONE_ALREADY_VERIFIED`.
        """
        return self._client.submit("POST", f"/dns/zones/{_quote(zone_id)}/verify")

    def verify_zone_and_wait(self, zone_id: str, *, deadline: float | None = None) -> DNSZone:
        """Runs :meth:`verify_zone` to completion and returns the zone as it
        stands afterwards. A record not found yet raises
        :class:`~nemau.errors.NemauTaskFailedError`.
        """
        ref = self.verify_zone(zone_id)
        self._client.wait_for(ref, deadline=deadline)
        return self.get_zone(zone_id)

    def delete_zone(self, zone_id: str) -> TaskRef:
        return self._client.submit("DELETE", f"/dns/zones/{_quote(zone_id)}")

    def list_record_sets(self, zone_id: str) -> list[DNSRecordSet]:
        """A zone pending verification has no record sets yet: the list is empty."""
        payload = self._client.request("GET", f"/dns/zones/{_quote(zone_id)}/recordsets")
        return [DNSRecordSet.from_dict(item) for item in _unwrap(payload, "recordsets")]

    def get_record_set(self, zone_id: str, record_set_id: str) -> DNSRecordSet:
        for item in self.list_record_sets(zone_id):
            if item.id == record_set_id:
                return item
        raise not_found("dns record set", record_set_id, f"/dns/zones/{zone_id}/recordsets")

    def create_record_set(
        self,
        zone_id: str,
        *,
        name: str,
        record_type: str,
        records: Sequence[str],
        ttl: int = 0,
        description: str = "",
    ) -> TaskRef:
        """The zone must be active: on a zone still pending verification the
        API refuses with :data:`REASON_DNS_ZONE_PENDING_VERIFICATION`."""
        body: dict[str, Any] = {"name": name, "type": record_type, "records": list(records)}
        if ttl:
            body["ttl"] = ttl
        if description:
            body["description"] = description
        return self._client.submit("POST", f"/dns/zones/{_quote(zone_id)}/recordsets", json=body)

    def update_record_set(
        self,
        zone_id: str,
        record_set_id: str,
        *,
        records: Sequence[str] | None = None,
        ttl: int = 0,
        description: str = "",
    ) -> TaskRef:
        """A record-set id is "canonical.name./TYPE" and travels as ``?id=``:
        its slash does not fit in one decoded path segment."""
        body: dict[str, Any] = {}
        if records is not None:
            body["records"] = list(records)
        if ttl:
            body["ttl"] = ttl
        if description:
            body["description"] = description
        return self._client.submit(
            "PUT",
            f"/dns/zones/{_quote(zone_id)}/recordsets",
            query={"id": record_set_id},
            json=body,
        )

    def delete_record_set(self, zone_id: str, record_set_id: str) -> TaskRef:
        return self._client.submit(
            "DELETE", f"/dns/zones/{_quote(zone_id)}/recordsets", query={"id": record_set_id}
        )

    def update_zone(
        self,
        zone_id: str,
        *,
        description: str | None = None,
        email: str | None = None,
        ttl: int | None = None,
    ) -> TaskRef:
        """Changes a zone's description, SOA contact or default TTL.

        Its name is the zone's identity and cannot change.
        """
        return patch(
            self._client,
            "update_zone",
            f"/dns/zones/{_quote(zone_id)}",
            {"description": description, "email": email, "ttl": ttl},
        )
