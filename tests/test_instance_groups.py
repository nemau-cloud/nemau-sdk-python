"""Instance groups: exact bodies, template and user_data rules, and refusals before any request."""

from __future__ import annotations

import dataclasses
import json
import unittest
from pathlib import Path
from typing import Any

from nemau import InstanceGroup, InstanceGroupDetail
from tests.helpers import RecordedRequest, Stub, client_for

#: The read contract API clients rely on: the detail of one group.
GOLDEN_PATH = Path(__file__).parent / "fixtures" / "instance_group.golden.json"
GOLDEN: dict[str, Any] = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

NET = "0b5f3c1e-2d4a-4f6b-8c9d-1e2f3a4b5c6d"
SUBNET = "1c6f4d2f-3e5b-4a7c-9d0e-2f3a4b5c6d7e"
SG = "7a8b9c0d-1e2f-4a3b-9c8d-7e6f5a4b3c2d"
POOL = "2d7a5e3a-4f6c-4b8d-8e1f-3a4b5c6d7e8f"
GROUP = "33333333-4444-4555-8666-777777777777"
INSTANCE = "88888888-9999-4aaa-8bbb-cccccccccccc"
CLOUD_CONFIG = "#cloud-config\npackages:\n  - nginx\n"
ACCEPTED = (202, {"task_id": "t", "status": "pending"})


def _valid_create(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "name": "web-tier",
        "network_id": NET,
        "subnet_id": SUBNET,
        "min_size": 1,
        "max_size": 5,
        "launch_template": {
            "image_id": "ubuntu-24.04",
            "flavor_id": "gp.small",
            "key_name": "ops",
            "service_level": "99.9%",
            "user_data": CLOUD_CONFIG,
        },
        "load_balancer_pools": [{"pool_id": POOL, "protocol_port": 443}],
        "scaling_policies": [{"metric": "cpu_percent", "target": 60}],
    }
    kwargs.update(overrides)
    return kwargs


def _create_with(patch: dict[str, Any]) -> Any:
    return lambda c: c.instance_groups.create(**_valid_create(**patch))


def _refused_locally(test: unittest.TestCase, call: Any, message: str) -> None:
    with Stub(lambda _r: ACCEPTED) as stub:
        with test.assertRaisesRegex(ValueError, message):
            call(client_for(stub))
        test.assertEqual(stub.requests, [], "no request must leave the client")


def _without_extra(value: Any) -> Any:
    """The decoded object as a plain JSON value; every model must have kept no unknown key."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, Any] = {}
        for item in dataclasses.fields(value):
            attribute = getattr(value, item.name)
            if item.name == "extra":
                assert attribute == {}, "the golden contract has no unknown key"
                continue
            out[item.name] = _without_extra(attribute)
        return out
    if isinstance(value, list):
        return [_without_extra(item) for item in value]
    return value


class GoldenContractTest(unittest.TestCase):
    def test_the_detail_decodes_the_golden_contract_field_for_field(self) -> None:
        detail = InstanceGroupDetail.from_dict(GOLDEN)
        self.assertEqual(_without_extra(detail), GOLDEN)
        self.assertEqual(detail.members[1].protected_from_scale_in, True)
        self.assertIsNone(detail.activities[1].instance_id)
        self.assertIsNotNone(detail.launch_template)
        self.assertEqual(detail.launch_template.version, 2)  # type: ignore[union-attr]

    def test_the_list_answers_the_golden_group_without_members_and_activities(self) -> None:
        summary = {k: v for k, v in GOLDEN.items() if k not in ("members", "activities")}
        with Stub(lambda _r: (200, {"instance_groups": [summary], "count": 1})) as stub:
            [group] = client_for(stub).instance_groups.list()
        self.assertIsInstance(group, InstanceGroup)
        self.assertEqual(_without_extra(group), summary)

    def test_create_and_update_carry_the_description_and_an_empty_update_clears_it(self) -> None:
        with Stub(lambda _r: ACCEPTED) as stub:
            client = client_for(stub)
            client.instance_groups.create(**_valid_create(description="Public web tier"))
            client.instance_groups.update(GROUP, description="")
            self.assertEqual(stub.requests[0].json()["description"], "Public web tier")
            self.assertEqual(stub.requests[1].json(), {"description": ""})

    def test_a_long_or_multi_line_description_is_refused_before_any_request(self) -> None:
        _refused_locally(
            self, _create_with({"description": "x" * 257}), "description must be at most 256"
        )
        _refused_locally(self, _create_with({"description": "two\nlines"}), "on one line")
        _refused_locally(
            self,
            lambda c: c.instance_groups.update(GROUP, description="tab\there"),
            "on one line",
        )


class InstanceMembershipTest(unittest.TestCase):
    def test_an_instance_reports_its_group_or_null_when_it_belongs_to_none(self) -> None:
        def handler(request: RecordedRequest) -> tuple[int, Any]:
            if request.path.endswith(f"/{INSTANCE}"):
                member = {"id": INSTANCE, "name": "web-1", "instance_group_id": GROUP}
                return 200, {"instance": member}
            return 200, {"instance": {"id": "other", "name": "solo", "instance_group_id": None}}

        with Stub(handler) as stub:
            client = client_for(stub)
            self.assertEqual(client.compute.get_instance(INSTANCE).instance_group_id, GROUP)
            self.assertIsNone(client.compute.get_instance("other").instance_group_id)


class ReadTest(unittest.TestCase):
    def test_list_get_and_activities_read_their_routes(self) -> None:
        def handler(request: RecordedRequest) -> tuple[int, Any]:
            if request.path.endswith("/activities"):
                return 200, {
                    "activities": [
                        {
                            "id": 7,
                            "type": "scale_out",
                            "reason": "",
                            "instance_id": None,
                            "created_at": "2026-10-25T10:00:00Z",
                        }
                    ]
                }
            if request.path.endswith(f"/{GROUP}"):
                return 200, {
                    "instance_group": {
                        "id": GROUP,
                        "name": "web-tier",
                        "members": [{"instance_id": INSTANCE}],
                    }
                }
            return 200, {"instance_groups": [{"id": GROUP, "name": "web-tier"}], "count": 1}

        with Stub(handler) as stub:
            client = client_for(stub)
            self.assertEqual(client.instance_groups.list()[0].id, GROUP)
            detail = client.instance_groups.get(GROUP)
            self.assertEqual(detail.members[0].instance_id, INSTANCE)
            self.assertEqual(client.instance_groups.activities(GROUP)[0].type, "scale_out")
            self.assertEqual(
                [r.path for r in stub.requests],
                [
                    "/api/v1/compute/instance-groups",
                    f"/api/v1/compute/instance-groups/{GROUP}",
                    f"/api/v1/compute/instance-groups/{GROUP}/activities",
                ],
            )

    def test_a_read_template_exposes_user_data_as_a_flag_only(self) -> None:
        def handler(_r: RecordedRequest) -> tuple[int, Any]:
            return 200, {
                "instance_group": {
                    "id": GROUP,
                    "launch_template": {"image_id": "u", "user_data_present": True},
                }
            }

        with Stub(handler) as stub:
            group = client_for(stub).instance_groups.get(GROUP)
            self.assertIsNotNone(group.launch_template)
            self.assertTrue(group.launch_template.user_data_present)  # type: ignore[union-attr]


class CreateTest(unittest.TestCase):
    def test_create_sends_the_template_with_its_user_data(self) -> None:
        with Stub(lambda _r: ACCEPTED) as stub:
            client_for(stub).instance_groups.create(**_valid_create())
            request = stub.requests[0]
            self.assertEqual(
                (request.method, request.path), ("POST", "/api/v1/compute/instance-groups")
            )
            self.assertEqual(
                request.json(),
                {
                    "name": "web-tier",
                    "network_id": NET,
                    "subnet_id": SUBNET,
                    "min_size": 1,
                    "max_size": 5,
                    "launch_template": {
                        "image_id": "ubuntu-24.04",
                        "flavor_id": "gp.small",
                        "key_name": "ops",
                        "service_level": "99.9%",
                        "user_data": CLOUD_CONFIG,
                    },
                    "load_balancer_pools": [{"pool_id": POOL, "protocol_port": 443}],
                    "scaling_policies": [{"metric": "cpu_percent", "target": 60}],
                },
            )

    def test_create_refuses_what_the_api_refuses_before_any_request(self) -> None:
        template = {"image_id": "ubuntu-24.04", "flavor_id": "gp.small"}
        cases: list[tuple[dict[str, Any], str]] = [
            ({"name": "Web_Tier"}, "name must be"),
            ({"name": "x" * 57}, "name must be"),
            ({"network_id": "net-1"}, "network_id"),
            ({"subnet_id": "sub-1"}, "subnet_id"),
            ({"max_size": 51}, "max_size must be between 0 and 50"),
            ({"min_size": 3, "max_size": 2}, "min_size, desired_capacity and max_size"),
            ({"desired_capacity": 6}, "min_size, desired_capacity and max_size"),
            ({"security_groups": [SG] * 6}, "at most 5"),
            ({"load_balancer_pools": [{"pool_id": POOL, "protocol_port": 0}]}, "protocol_port"),
            (
                {
                    "load_balancer_pools": [
                        {"pool_id": POOL, "protocol_port": 80},
                        {"pool_id": POOL, "protocol_port": 81},
                    ]
                },
                "twice",
            ),
            ({"health_check": "load_balancer"}, "not available yet"),
            ({"health_check": "tcp"}, "must be instance"),
            ({"health_check_grace_seconds": 59}, "60 and 1800"),
            ({"drain_seconds": 301}, "0 and 300"),
            (
                {"scaling_policies": [{"metric": "memory_percent", "target": 60}]},
                "not available yet",
            ),
            (
                {"scaling_policies": [{"metric": "cpu_percent", "target": 95}]},
                "target must be between 10 and 90",
            ),
            ({"scaling_policies": [{"metric": "cpu_percent"}]}, "target is required"),
            (
                {
                    "scaling_policies": [
                        {"metric": "cpu_percent", "target": 50},
                        {"metric": "cpu_percent", "target": 60},
                    ]
                },
                "twice",
            ),
            (
                {"launch_template": {"image_id": "u", "vcpus": 2, "ram_mb": 1000, "disk_gb": 20}},
                "multiple of 256",
            ),
            ({"launch_template": {"image_id": "u", "vcpus": 2}}, "together"),
            (
                {"launch_template": {"image_id": "u", "vcpus": 65, "ram_mb": 2048, "disk_gb": 20}},
                "vcpus",
            ),
            ({"launch_template": {**template, "service_level": "gold"}}, "service_level"),
            ({"launch_template": {**template, "extra": "x"}}, "does not know"),
            ({"launch_template": {"flavor_id": "gp.small"}}, "image_id is required"),
            (
                {"launch_template": {**template, "user_data": "#!/bin/sh\n" + "x" * 16384}},
                "16384 bytes",
            ),
            ({"launch_template": {**template, "user_data": "echo hi"}}, "must start with"),
            (
                {"launch_template": {**template, "user_data": "   \n "}},
                "must start with|must not be empty",
            ),
        ]
        for patch, message in cases:
            with self.subTest(patch=patch):
                _refused_locally(self, _create_with(patch), message)

    def test_a_refused_user_data_never_shows_its_document(self) -> None:
        document = "echo SECRET-TOKEN-7731\n"
        kwargs = _valid_create(
            launch_template={"image_id": "u", "flavor_id": "gp.small", "user_data": document}
        )
        with Stub(lambda _r: ACCEPTED) as stub:
            with self.assertRaises(ValueError) as ctx:
                client_for(stub).instance_groups.create(**kwargs)
            self.assertIn("must start with", str(ctx.exception))
            self.assertNotIn("SECRET", str(ctx.exception))
            self.assertEqual(stub.requests, [])


class ChangeTest(unittest.TestCase):
    def test_update_sends_the_fields_given_and_an_empty_schedules_list_clears_them(self) -> None:
        with Stub(lambda _r: ACCEPTED) as stub:
            client_for(stub).instance_groups.update(GROUP, schedules=[], max_surge=2)
            request = stub.requests[0]
            self.assertEqual(
                (request.method, request.path),
                ("PATCH", f"/api/v1/compute/instance-groups/{GROUP}"),
            )
            self.assertEqual(request.json(), {"schedules": [], "max_surge": 2})

    def test_update_refusals(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({}, "no field to change"),
            ({"min_size": 4, "max_size": 3}, "must not exceed max_size"),
            ({"max_surge": 11}, "max_surge"),
            ({"max_unavailable_pct": 51}, "max_unavailable_pct"),
            ({"health_check": "load_balancer"}, "not available yet"),
            (
                {"schedules": [{"cron": "0 3 * * *", "min_size": 2, "max_size": 1}]},
                "must not exceed its max_size",
            ),
            ({"schedules": [{"cron": "@daily", "min_size": 2}]}, "5 fields"),
            ({"schedules": [{"cron": "0 3 * * *"}]}, "at least one"),
        ]
        for patch, message in cases:
            with self.subTest(patch=patch):
                _refused_locally(
                    self, lambda c, k=patch: c.instance_groups.update(GROUP, **k), message
                )

    def test_set_capacity_and_member_protection_reach_their_routes(self) -> None:
        with Stub(lambda _r: ACCEPTED) as stub:
            client = client_for(stub)
            client.instance_groups.set_capacity(GROUP, 3)
            client.instance_groups.set_member_protection(GROUP, INSTANCE, protected=True)
            capacity, protection = stub.requests
            self.assertEqual(
                (capacity.method, capacity.path),
                ("PUT", f"/api/v1/compute/instance-groups/{GROUP}/capacity"),
            )
            self.assertEqual(capacity.json(), {"desired_capacity": 3})
            self.assertEqual(
                protection.path,
                f"/api/v1/compute/instance-groups/{GROUP}/members/{INSTANCE}/protection",
            )
            self.assertEqual(protection.json(), {"protected": True})
        _refused_locally(
            self, lambda c: c.instance_groups.set_capacity(GROUP, 51), "between 0 and 50"
        )
        _refused_locally(
            self,
            lambda c: c.instance_groups.set_member_protection(GROUP, INSTANCE, protected="yes"),
            "true or false",  # type: ignore[arg-type]
        )

    def test_delete_keeps_members_unless_asked(self) -> None:
        with Stub(lambda _r: ACCEPTED) as stub:
            client = client_for(stub)
            client.instance_groups.delete(GROUP)
            client.instance_groups.delete(GROUP, delete_instances=True)
            self.assertEqual(stub.requests[0].query, "")
            self.assertEqual(stub.requests[1].query, "delete_instances=true")


class RefreshTest(unittest.TestCase):
    def test_refresh_posts_the_shares_given_and_leaves_the_others_to_the_group(self) -> None:
        with Stub(lambda _r: ACCEPTED) as stub:
            client = client_for(stub)
            client.instance_groups.refresh(GROUP, max_unavailable_pct=25, max_surge=2)
            client.instance_groups.refresh(GROUP)
            client.instance_groups.refresh(GROUP, max_surge=1)
            full, bare, surge = stub.requests
            self.assertEqual(
                (full.method, full.path),
                ("POST", f"/api/v1/compute/instance-groups/{GROUP}/refresh"),
            )
            self.assertEqual(full.json(), {"max_unavailable_pct": 25, "max_surge": 2})
            self.assertEqual(bare.json(), {})
            self.assertEqual(surge.json(), {"max_surge": 1})

    def test_refresh_refuses_out_of_range_shares_before_any_request(self) -> None:
        _refused_locally(
            self,
            lambda c: c.instance_groups.refresh(GROUP, max_unavailable_pct=51),
            "max_unavailable_pct must be between 0 and 50",
        )
        _refused_locally(
            self,
            lambda c: c.instance_groups.refresh(GROUP, max_surge=0),
            "max_surge must be between 1 and 10",
        )
        _refused_locally(
            self,
            lambda c: c.instance_groups.refresh(GROUP, max_surge=11),
            "max_surge must be between 1 and 10",
        )

    def test_cancel_refresh_posts_to_the_cancel_route(self) -> None:
        with Stub(lambda _r: ACCEPTED) as stub:
            client_for(stub).instance_groups.cancel_refresh(GROUP)
            request = stub.requests[0]
            self.assertEqual(
                (request.method, request.path),
                ("POST", f"/api/v1/compute/instance-groups/{GROUP}/refresh/cancel"),
            )

    def test_the_view_decodes_the_refresh_state(self) -> None:
        detail = InstanceGroupDetail.from_dict(GOLDEN)
        assert detail.refresh is not None
        self.assertEqual(detail.refresh.status, "in_progress")
        self.assertEqual(detail.refresh.target_version, 2)
        self.assertEqual(detail.refresh.skipped_protected, 1)
        self.assertIsNone(detail.refresh.completed_at)


if __name__ == "__main__":
    unittest.main()
