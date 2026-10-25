"""Zone ownership: the pending state, the verify action, the refusals."""

from __future__ import annotations

import unittest
from typing import Any

from nemau import (
    DNS_ZONE_STATUS_ACTIVE,
    DNS_ZONE_STATUS_PENDING_VERIFICATION,
    REASON_DNS_ZONE_ALREADY_VERIFIED,
    REASON_DNS_ZONE_EXISTS,
    REASON_DNS_ZONE_PENDING_VERIFICATION,
    REASON_DNS_ZONE_RESERVED,
    DNSZoneVerification,
    NemauApiError,
    NemauTaskFailedError,
    has_reason,
)
from tests.helpers import RecordedRequest, Stub, client_for

ACCEPTED = (202, {"task_id": "t", "status": "pending"})

#: A zone's id is a UUID, not its name.
ZONE_ID = "3f1c9a2e-7b4d-4e8a-9c61-2d5f0b8e4a17"
OTHER_ZONE_ID = "8a2b6c4d-1e3f-4a5b-8c7d-9e0f1a2b3c4d"

ZONES = {
    "zones": [
        {
            "id": ZONE_ID,
            "name": "example.com.",
            "status": "PENDING_VERIFICATION",
            "verification": {
                "name": "_nemau-challenge.example.com.",
                "type": "TXT",
                "value": "nemau-verification=abc123",
            },
        },
        {"id": OTHER_ZONE_ID, "name": "example.org.", "status": "ACTIVE"},
    ],
    "count": 2,
}


def verify_stub(terminal: str):  # type: ignore[no-untyped-def]
    """Serves the verify write, its task, then the zone list."""

    def handler(request: RecordedRequest) -> tuple[int, Any]:
        if request.method == "POST" and request.path.endswith("/verify"):
            return 202, {"task_id": "task-1", "status": "pending"}
        if request.path.startswith("/api/v1/tasks/"):
            payload = {
                "id": "task-1",
                "taskType": "dns:verify_zone",
                "status": terminal,
                "progress": 100,
            }
            if terminal == "failed":
                payload["error"] = "TXT record _nemau-challenge.example.com. not found"
            else:
                payload["result"] = {"zone_id": ZONE_ID, "status": "ACTIVE"}
            return 200, payload
        return 200, {
            "zones": [{"id": ZONE_ID, "name": "example.com.", "status": "ACTIVE"}],
            "count": 1,
        }

    return handler


class ZoneOwnershipTest(unittest.TestCase):
    def test_pending_zone_carries_its_verification_record(self) -> None:
        with Stub(lambda _r: (200, ZONES)) as stub:
            dns = client_for(stub).dns
            pending = dns.get_zone(ZONE_ID)
            self.assertEqual(pending.status, DNS_ZONE_STATUS_PENDING_VERIFICATION)
            self.assertEqual(
                pending.verification,
                DNSZoneVerification(
                    name="_nemau-challenge.example.com.",
                    type="TXT",
                    value="nemau-verification=abc123",
                ),
            )
            active = dns.get_zone(OTHER_ZONE_ID)
            self.assertEqual(active.status, DNS_ZONE_STATUS_ACTIVE)
            self.assertIsNone(active.verification)

    def test_verify_zone_posts_to_the_zone(self) -> None:
        with Stub(lambda _r: ACCEPTED) as stub:
            ref = client_for(stub).dns.verify_zone(ZONE_ID)
            self.assertEqual(ref.task_id, "t")
            seen = stub.requests[0]
            self.assertEqual(seen.method, "POST")
            self.assertEqual(seen.path, f"/api/v1/dns/zones/{ZONE_ID}/verify")
            self.assertEqual(seen.body, b"")

    def test_verify_zone_and_wait_returns_the_active_zone(self) -> None:
        with Stub(verify_stub("completed")) as stub:
            zone = client_for(stub).dns.verify_zone_and_wait(ZONE_ID)
            self.assertEqual(zone.status, DNS_ZONE_STATUS_ACTIVE)
            self.assertIsNone(zone.verification)

    def test_missing_record_fails_the_wait(self) -> None:
        with Stub(verify_stub("failed")) as stub:
            with self.assertRaises(NemauTaskFailedError) as caught:
                client_for(stub).dns.verify_zone_and_wait(ZONE_ID)
            self.assertIn("not found", str(caught.exception))
            # No zone read after a failed check.
            self.assertFalse(any(r.path == "/api/v1/dns/zones" for r in stub.requests))

    def test_refusals_are_recognisable_by_reason(self) -> None:
        cases = [
            (
                400,
                REASON_DNS_ZONE_RESERVED,
                lambda c: c.dns.create_zone(name="x.nemau-cloud.fr.", email="a@b.c"),
            ),
            (
                409,
                REASON_DNS_ZONE_EXISTS,
                lambda c: c.dns.create_zone(name="example.com.", email="a@b.c"),
            ),
            (409, REASON_DNS_ZONE_ALREADY_VERIFIED, lambda c: c.dns.verify_zone(ZONE_ID)),
            (
                409,
                REASON_DNS_ZONE_PENDING_VERIFICATION,
                lambda c: c.dns.create_record_set(
                    ZONE_ID, name="www", record_type="A", records=["203.0.113.7"]
                ),
            ),
        ]
        for status, reason, call in cases:
            refusal = (status, {"error": "refused", "reason": reason})
            with self.subTest(reason=reason), Stub(lambda _r, answer=refusal: answer) as stub:
                with self.assertRaises(NemauApiError) as caught:
                    call(client_for(stub))
                self.assertTrue(has_reason(caught.exception, reason))
                self.assertFalse(has_reason(caught.exception, "some_other_reason"))

        self.assertFalse(has_reason(ValueError("dns_zone_exists"), REASON_DNS_ZONE_EXISTS))
        self.assertFalse(has_reason(NemauApiError(409, "x", method="POST", path="/"), ""))

    def test_created_zone_is_read_back_by_its_uuid(self) -> None:
        def handler(request: RecordedRequest) -> tuple[int, Any]:
            if request.method == "POST":
                return 202, {"task_id": "task-1", "status": "pending"}
            if request.path.startswith("/api/v1/tasks/"):
                return 200, {
                    "id": "task-1",
                    "taskType": "dns:create_zone",
                    "status": "completed",
                    "progress": 100,
                    "result": {"zone_id": ZONE_ID, "status": "PENDING_VERIFICATION"},
                }
            return 200, ZONES

        with Stub(handler) as stub:
            zone = client_for(stub).dns.create_zone_and_wait(
                name="example.com.", email="dns@example.com"
            )
            self.assertEqual(zone.id, ZONE_ID)
            self.assertEqual(zone.status, DNS_ZONE_STATUS_PENDING_VERIFICATION)
            self.assertIsNotNone(zone.verification)


if __name__ == "__main__":
    unittest.main()
