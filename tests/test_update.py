"""Mutable-field updates: what a PATCH sends, and what it refuses to send."""

from __future__ import annotations

import unittest

from nemau.services.update import update_body
from tests.helpers import Stub, client_for, routes

ACCEPTED = (202, {"task_id": "t-1", "status": "pending"})


class UpdateBodyTest(unittest.TestCase):
    def test_an_omitted_argument_stays_out_of_the_body(self) -> None:
        # Sending the whole object would let a caller holding a stale copy blank
        # a name another caller had just written.
        self.assertEqual(
            update_body("update_network", name=None, description="prod segment"),
            {"description": "prod segment"},
        )

    def test_an_empty_string_is_an_explicit_clear(self) -> None:
        self.assertEqual(update_body("update_network", description=""), {"description": ""})

    def test_an_update_with_no_field_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            update_body("update_volume", name=None, description=None)
        self.assertIn("update_volume", str(caught.exception))


class UpdateRequestTest(unittest.TestCase):
    def test_the_patch_carries_only_the_field_given(self) -> None:
        with Stub(routes({"PATCH /api/v1/network/networks/net-1": ACCEPTED})) as stub:
            client_for(stub).network.update_network("net-1", description="prod segment")
            request = stub.requests[0]
            self.assertEqual(request.method, "PATCH")
            self.assertEqual(request.json(), {"description": "prod segment"})

    def test_a_volume_update_carries_its_id_in_the_query(self) -> None:
        # A volume id can contain a slash, which one path segment cannot hold.
        volid = "pool-a:104/disk-0.qcow2"
        with Stub(routes({"PATCH /api/v1/storage/volumes": ACCEPTED})) as stub:
            client_for(stub).storage.update_volume(volid, name="data")
            self.assertIn("id=", stub.requests[0].query)

    def test_each_resource_has_its_own_path(self) -> None:
        cases = [
            (
                "/api/v1/compute/instances/i-1",
                lambda c: c.compute.update_instance("i-1", name="web-2"),
            ),
            (
                "/api/v1/network/pools/p-1/members/m-1",
                lambda c: c.load_balancer.update_pool_member("p-1", "m-1", weight=3),
            ),
            (
                "/api/v1/storage/containers/backups",
                lambda c: c.storage.update_container("backups", versioning=True),
            ),
            (
                "/api/v1/dns/zones/example.com.",
                lambda c: c.dns.update_zone("example.com.", ttl=600),
            ),
        ]
        for path, call in cases:
            with self.subTest(path=path), Stub(routes({"PATCH " + path: ACCEPTED})) as stub:
                call(client_for(stub))
                self.assertEqual(stub.requests[0].path, path)


if __name__ == "__main__":
    unittest.main()


class ContainerRetentionCapTest(unittest.TestCase):
    def test_retention_above_the_cap_is_refused_before_any_request(self) -> None:
        with Stub(lambda _r: (202, {"task_id": "t"})) as stub:
            storage = client_for(stub).storage
            with self.assertRaisesRegex(ValueError, "retention_days must not exceed 2555"):
                storage.create_container(
                    "backups",
                    object_lock=True,
                    retention_mode="COMPLIANCE",
                    retention_days=2556,
                )
            self.assertEqual(stub.requests, [])
