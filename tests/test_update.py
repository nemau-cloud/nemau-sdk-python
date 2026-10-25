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


class InstanceBackupUpdateTest(unittest.TestCase):
    PATH = "/api/v1/compute/instances/i-1"

    def test_the_patch_carries_exactly_the_backup_fields_given(self) -> None:
        with Stub(routes({"PATCH " + self.PATH: ACCEPTED})) as stub:
            client_for(stub).compute.update_instance(
                "i-1",
                backup_schedule="daily",
                backup_hour=3,
                backup_retention_days=14,
            )
            self.assertEqual(
                stub.requests[0].json(),
                {"backup_schedule": "daily", "backup_hour": 3, "backup_retention_days": 14},
            )

    def test_weekly_carries_the_weekday(self) -> None:
        with Stub(routes({"PATCH " + self.PATH: ACCEPTED})) as stub:
            client_for(stub).compute.update_instance(
                "i-1", backup_schedule="weekly", backup_weekday=6, backup_retention_days=35
            )
            self.assertEqual(
                stub.requests[0].json(),
                {"backup_schedule": "weekly", "backup_weekday": 6, "backup_retention_days": 35},
            )

    def test_zero_is_a_value_and_is_sent(self) -> None:
        # 0 is a valid hour and weekday: an omitted argument is None, not 0.
        with Stub(routes({"PATCH " + self.PATH: ACCEPTED})) as stub:
            client_for(stub).compute.update_instance("i-1", backup_hour=0, backup_weekday=0)
            self.assertEqual(stub.requests[0].json(), {"backup_hour": 0, "backup_weekday": 0})

    def test_switching_off_sends_none(self) -> None:
        with Stub(routes({"PATCH " + self.PATH: ACCEPTED})) as stub:
            client_for(stub).compute.update_instance("i-1", backup_schedule="none")
            self.assertEqual(stub.requests[0].json(), {"backup_schedule": "none"})

    def test_name_and_backup_fields_travel_together(self) -> None:
        with Stub(routes({"PATCH " + self.PATH: ACCEPTED})) as stub:
            client_for(stub).compute.update_instance("i-1", name="web-2", backup_hour=4)
            self.assertEqual(stub.requests[0].json(), {"name": "web-2", "backup_hour": 4})

    def test_invalid_values_are_refused_before_any_request(self) -> None:
        cases = [
            ({"backup_schedule": "hourly"}, "backup_schedule must be one of"),
            ({"backup_schedule": ""}, "backup_schedule must be one of"),
            ({"backup_hour": 24}, "backup_hour must be between 0 and 23"),
            ({"backup_hour": -1}, "backup_hour must be between 0 and 23"),
            ({"backup_hour": True}, "backup_hour must be an integer"),
            ({"backup_hour": "2"}, "backup_hour must be an integer"),
            ({"backup_hour": 2.0}, "backup_hour must be an integer"),
            ({"backup_weekday": 7}, "backup_weekday must be between 0 and 6"),
            ({"backup_weekday": False}, "backup_weekday must be an integer"),
            ({"backup_retention_days": 0}, "backup_retention_days must be between 1 and 35"),
            ({"backup_retention_days": 36}, "backup_retention_days must be between 1 and 35"),
            ({"backup_retention_days": True}, "backup_retention_days must be an integer"),
        ]
        with Stub(routes({"PATCH " + self.PATH: ACCEPTED})) as stub:
            compute = client_for(stub).compute
            for kwargs, message in cases:
                with self.subTest(**kwargs), self.assertRaisesRegex(ValueError, message):
                    compute.update_instance("i-1", **kwargs)
            self.assertEqual(stub.requests, [])

    def test_a_call_with_only_none_is_refused(self) -> None:
        with Stub(routes({"PATCH " + self.PATH: ACCEPTED})) as stub:
            with self.assertRaisesRegex(ValueError, "update_instance was given no field"):
                client_for(stub).compute.update_instance("i-1", backup_hour=None)
            self.assertEqual(stub.requests, [])


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
